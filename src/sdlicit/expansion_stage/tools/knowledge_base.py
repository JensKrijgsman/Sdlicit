"""Knowledge base wrapper around LightRAG.

Provides a unified async interface for all agents to query the
knowledge corpus (ISO standards, design patterns, user documents).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sdlicit.helpers.token_counter import TokenCounter, make_counting_llm
from sdlicit.logging import get_logger

if TYPE_CHECKING:
    from sdlicit.config import SdlicitConfig
    from sdlicit.helpers.chunkers import Chunk

_log = get_logger("tool")


@dataclass
class KBChunk:
    """A single retrieval result with source attribution."""

    text: str
    source: str
    relevance: float
    mode: str = "hybrid"
    store: str = "all"  # "knowledge", "artifacts", or "all"


_INLINE_REF_RE = re.compile(r"\*\s*\[\d+\]\s*((?:knowledge|artifacts)/\S+)")


def _extract_inline_sources(text: str) -> list[str]:
    """Extract source references from LightRAG graph result text.

    LightRAG embeds references like:
      ``* [1] knowledge/.sdlicit/knowledge/ISO_29148.pdf#5-1-general``
    Returns a deduplicated list of source paths.
    """
    seen: set[str] = set()
    sources: list[str] = []
    for match in _INLINE_REF_RE.finditer(text):
        ref = match.group(1).strip()
        if ref not in seen:
            seen.add(ref)
            sources.append(ref)
    return sources


class KnowledgeBase:
    """Unified interface to LightRAG for all agents."""

    def __init__(
        self, working_dir: Path, provider: str, model: str, **kwargs: Any
    ) -> None:
        self._working_dir = working_dir.resolve()
        self._working_dir.mkdir(parents=True, exist_ok=True)
        self._provider = provider
        # Embedder provider may differ from the generation provider (e.g. a
        # local ollama model that embeds against a cloud-built KB). Empty
        # falls back to the generation provider.
        self._embed_provider = kwargs.get("embed_provider") or provider
        self._model = model
        self._kwargs = kwargs
        self._rag: Any | None = None
        self._rag_ready = False
        self._token_counter = TokenCounter()

    def _build_rag(self) -> Any:
        """Build the LightRAG instance (not yet initialized)."""
        if self._rag is not None:
            return self._rag

        try:
            from lightrag import LightRAG
            from lightrag.utils import EmbeddingFunc

            ollama_host = self._kwargs.get("ollama_host", "http://localhost:11434")

            # Embedder selection is keyed off the embed provider, which may
            # differ from the generation provider (self._provider).
            if self._embed_provider == "ollama":
                embed_name = self._kwargs.get("embed_model", "nomic-embed-text")
                embed_dim = int(self._kwargs.get("embed_dim", 768))
                embed_max_tokens = int(self._kwargs.get("embed_max_tokens", 2048))

                # Custom wrapper that passes dimensions= for MRL truncation
                # (Qwen3-Embedding supports Matryoshka output dims via Ollama API)
                async def _ollama_embed_with_dims(
                    texts: list[str], **kw: Any
                ) -> Any:
                    import numpy as np
                    import ollama as _ollama

                    api_key = kw.pop("api_key", None)
                    host = kw.pop("host", ollama_host)
                    timeout = kw.pop("timeout", None)
                    options = kw.pop("options", {})
                    headers = {
                        "Content-Type": "application/json",
                    }
                    if api_key:
                        headers["Authorization"] = f"Bearer {api_key}"

                    client = _ollama.AsyncClient(
                        host=host, timeout=timeout, headers=headers
                    )
                    try:
                        data = await client.embed(
                            model=embed_name,
                            input=texts,
                            options=options,
                            dimensions=embed_dim,
                        )
                        return np.array(data["embeddings"])
                    finally:
                        with contextlib.suppress(Exception):
                            await client._client.aclose()

                embed_fn = EmbeddingFunc(
                    embedding_dim=embed_dim,
                    max_token_size=embed_max_tokens,
                    func=_ollama_embed_with_dims,
                    model_name=embed_name,
                )
            else:
                # OpenRouter (OpenAI-compatible) cloud embeddings
                from lightrag.llm.openai import openai_embed

                cloud_embed_model = self._kwargs.get(
                    "embed_model", "openai/text-embedding-3-large"
                )
                cloud_embed_dim = int(self._kwargs.get("embed_dim", 3072))
                cloud_embed_max_tokens = int(self._kwargs.get("embed_max_tokens", 8192))
                api_key = self._kwargs.get("api_key", "")

                _base_embed = partial(
                    openai_embed.func,
                    model=cloud_embed_model,
                    base_url="https://openrouter.ai/api/v1",
                    api_key=api_key,
                )

                async def _safe_openrouter_embed(texts: list[str], **kw: Any) -> Any:

                    result = await _base_embed(texts, **kw)
                    if result is None:
                        raise RuntimeError(
                            "OpenRouter embedding returned None — likely a transient "
                            "rate-limit or server error; will retry"
                        )
                    return result

                embed_fn = EmbeddingFunc(
                    embedding_dim=cloud_embed_dim,
                    max_token_size=cloud_embed_max_tokens,
                    func=_safe_openrouter_embed,
                    model_name=cloud_embed_model,
                )

            # Common LightRAG settings matching the proven Research implementation
            # chunk_token_size must be well below nomic-embed-text's 2048
            # context limit because LightRAG counts with tiktoken (GPT-4o)
            # which under-counts relative to nomic's tokenizer.
            common_kwargs = dict(
                working_dir=str(self._working_dir),
                workspace=str(self._working_dir),
                llm_model_name=self._model,
                embedding_func=embed_fn,
                chunk_token_size=500,
                chunk_overlap_token_size=50,
            )

            if self._provider == "ollama":
                from lightrag.llm.ollama import ollama_model_complete

                self._rag = LightRAG(
                    **common_kwargs,
                    llm_model_func=make_counting_llm(
                        ollama_model_complete, self._token_counter
                    ),
                    llm_model_kwargs={
                        "host": ollama_host,
                        "timeout": 600,
                        "think": False,
                        "options": {"num_ctx": 4096},
                    },
                    default_llm_timeout=600,
                    llm_model_max_async=1,
                    # Conservative embedding settings for local Ollama
                    embedding_batch_num=4,
                    embedding_func_max_async=1,
                    default_embedding_timeout=300,
                )
            else:
                from lightrag.llm.openai import openai_complete_if_cache

                api_key = self._kwargs.get("api_key", "")

                # LightRAG calls llm_func(prompt, **kw) but
                # openai_complete_if_cache needs (model, prompt, **kw).
                # Extract model from hashing_kv like ollama_model_complete does.
                async def _openrouter_llm(prompt: str, **kw: Any) -> str:
                    model_name = kw.get("hashing_kv", {})
                    if hasattr(model_name, "global_config"):
                        model_name = model_name.global_config.get(
                            "llm_model_name", self._model
                        )
                    else:
                        model_name = self._model
                    # LightRAG merges llm_model_kwargs into kw, so strip
                    # keys we pass explicitly to avoid duplicates.
                    kw.pop("api_key", None)
                    kw.pop("base_url", None)
                    kw.pop("timeout", None)
                    # Disable structured output (response_format with Pydantic
                    # model) — many OpenRouter models don't support .parse().
                    # The LightRAG prompt already requests JSON, so the model
                    # returns parseable JSON without structured output mode.
                    kw.pop("keyword_extraction", None)
                    kw.pop("response_format", None)
                    return await openai_complete_if_cache(
                        model_name,
                        prompt,
                        base_url="https://openrouter.ai/api/v1",
                        api_key=api_key,
                        **kw,
                    )

                self._rag = LightRAG(
                    **common_kwargs,
                    llm_model_func=make_counting_llm(
                        _openrouter_llm, self._token_counter
                    ),
                    llm_model_kwargs={
                        "api_key": api_key,
                        "base_url": "https://openrouter.ai/api/v1",
                        "timeout": 120,
                    },
                    default_llm_timeout=120,
                    llm_model_max_async=4,
                    # Parallel cloud embedding: 4 concurrent calls × 32 chunks
                    embedding_batch_num=32,
                    embedding_func_max_async=4,
                    default_embedding_timeout=60,
                )
        except ImportError:
            _log.warning(
                "LightRAG not available — KB queries will return empty results"
            )
            return None

        return self._rag

    async def _ensure_rag(self) -> Any:
        """Build + async-initialize LightRAG on first use."""
        rag = self._build_rag()
        if rag is None:
            return None
        if not self._rag_ready:
            await rag.initialize_storages()
            self._rag_ready = True
            _log.info("LightRAG storages initialized")
        return rag

    async def query(
        self, text: str, mode: str = "hybrid", top_k: int = 5
    ) -> list[KBChunk]:
        """Query the knowledge base. Modes: naive, local, global, hybrid, mix.

        Always performs a vector search alongside the graph query so that
        returned chunks carry real ``source`` file paths (not the generic
        ``"knowledge_base"`` placeholder).
        """
        rag = await self._ensure_rag()
        if rag is None:
            return []

        try:
            if mode == "naive":
                # Pure vector search — chunks already have real sources
                vector_chunks = await self._vector_search(text, top_k=top_k)
                return vector_chunks if vector_chunks else []

            # For graph-based modes, run vector + graph in parallel
            from lightrag import QueryParam

            graph_mode = "local" if mode == "hybrid" else mode
            param = QueryParam(mode=graph_mode, top_k=top_k)

            async def _safe_graph_query() -> str | None:
                try:
                    return await rag.aquery(text, param=param)
                except Exception:
                    return None

            vector_chunks, graph_result = await asyncio.gather(
                self._vector_search(text, top_k=top_k),
                _safe_graph_query(),
            )

            if vector_chunks:
                # Attach the graph synthesis as the first chunk if available,
                # using the first vector chunk's source for attribution
                if graph_result:
                    # Extract inline references from the graph result text
                    sources = _extract_inline_sources(graph_result)
                    primary_source = sources[0] if sources else vector_chunks[0].source
                    return [
                        KBChunk(
                            text=graph_result,
                            source=primary_source,
                            relevance=1.0,
                            mode=mode,
                        ),
                        *vector_chunks,
                    ]
                return vector_chunks

            # Fallback: return graph result if vector search found nothing
            if graph_result:
                sources = _extract_inline_sources(graph_result)
                if sources:
                    # Create one chunk per extracted source reference
                    return [
                        KBChunk(
                            text=graph_result,
                            source=src,
                            relevance=1.0,
                            mode=mode,
                        )
                        for src in sources
                    ]
                return [
                    KBChunk(
                        text=graph_result,
                        source="knowledge_base",
                        relevance=1.0,
                        mode=mode,
                    )
                ]
            return []
        except Exception as exc:
            _log.warning("KB query failed: %s", exc)
            return []

    async def _vector_search(self, text: str, top_k: int = 5) -> list[KBChunk]:
        """Direct vector DB search returning chunks with real file_path sources."""
        rag = await self._ensure_rag()
        if rag is None:
            return []

        try:
            chunks_vdb = rag.chunks_vdb
            if chunks_vdb is None:
                return []

            # NanoVectorDBStorage.query is async and handles embedding internally
            raw_results = await chunks_vdb.query(text, top_k=top_k)

            results: list[KBChunk] = []
            for item in raw_results:
                source = item.get("file_path", "") or item.get("source_id", "")
                chunk_text = item.get("content", "") or item.get("text", "")
                if chunk_text:
                    results.append(
                        KBChunk(
                            text=chunk_text,
                            source=source or "unknown",
                            relevance=float(item.get("distance", 0.0)),
                            mode="naive",
                        )
                    )
            return results
        except Exception as exc:
            _log.warning("Vector search failed: %s", exc)
            return []

    async def query_filtered(
        self,
        text: str,
        path_prefix: str = "",
        mode: str = "hybrid",
        top_k: int = 5,
    ) -> list[KBChunk]:
        """Query with optional source filtering by file_path prefix.

        Always returns chunks with real file_path sources. Uses vector
        search for source attribution and optional graph query for
        richer synthesis.
        """
        rag = await self._ensure_rag()
        if rag is None:
            return []

        if not path_prefix:
            return await self.query(text, mode=mode, top_k=top_k)

        try:
            # Vector search with prefix filtering — always gives real sources
            chunks = await self._vector_search_filtered(text, path_prefix, top_k)
            return chunks if chunks else []
        except Exception as exc:
            _log.warning("KB filtered query failed: %s", exc)
            return []

    async def _vector_search_filtered(
        self, text: str, path_prefix: str, top_k: int = 5
    ) -> list[KBChunk]:
        """Direct vector DB search with file_path prefix filtering."""
        rag = await self._ensure_rag()
        if rag is None:
            return []

        try:
            # Access LightRAG's internal chunk vector DB
            chunks_vdb = rag.chunks_vdb
            if chunks_vdb is None:
                return await self.query(text, mode="naive", top_k=top_k)

            # NanoVectorDBStorage.query is async and handles embedding internally
            raw_results = await chunks_vdb.query(text, top_k=top_k * 3)

            # Filter by path prefix and take top_k
            filtered: list[KBChunk] = []
            for item in raw_results:
                source = item.get("file_path", "") or item.get("source_id", "")
                if source.startswith(path_prefix):
                    chunk_text = item.get("content", "") or item.get("text", "")
                    if chunk_text:
                        filtered.append(
                            KBChunk(
                                text=chunk_text,
                                source=source,
                                relevance=float(item.get("distance", 0.0)),
                                mode="naive",
                                store=path_prefix.rstrip("/"),
                            )
                        )
                if len(filtered) >= top_k:
                    break

            return filtered
        except Exception as exc:
            _log.warning("Vector search filtered failed: %s", exc)
            # Fallback to regular query
            return await self.query(text, mode="naive", top_k=top_k)

    async def probe_graph_entities(self, terms: list[str]) -> dict[str, bool]:
        """Check if terms exist as entities in the NetworkX graph.

        Returns a dict mapping each term to whether it was found.
        This is a cheap local check (no LLM calls).
        """
        graph_file = self._working_dir / "graph_chunk_entity_relation.graphml"
        if not graph_file.exists():
            return {t: False for t in terms}

        try:
            import networkx as nx

            G = nx.read_graphml(graph_file)
            # Entity names in LightRAG are typically uppercase
            node_names = {str(n).lower() for n in G.nodes()}

            results: dict[str, bool] = {}
            for term in terms:
                term_lower = term.lower()
                # Check exact match or substring in node names
                found = any(term_lower in name for name in node_names)
                results[term] = found
            return results
        except Exception as exc:
            _log.warning("Graph entity probe failed: %s", exc)
            return {t: False for t in terms}

    async def probe_local(self, text: str) -> bool:
        """Lightweight probe using LightRAG local mode with top_k=1.

        Returns True if the query returns any result (entities found in graph).
        """
        rag = await self._ensure_rag()
        if rag is None:
            return False

        try:
            from lightrag import QueryParam

            param = QueryParam(mode="local", top_k=1)
            result = await rag.aquery(text, param=param)
            return bool(result and result.strip())
        except Exception:
            return False

    async def insert(self, text: str, file_path: str | None = None) -> None:
        """Insert a document into the knowledge base (async)."""
        rag = await self._ensure_rag()
        if rag is not None:
            await rag.ainsert(text, file_paths=file_path)

    async def insert_chunks(
        self, chunks: list[Chunk], *, scope_prefix: str = ""
    ) -> int:
        """Insert pre-chunked content. Each :class:`Chunk` becomes one
        LightRAG document, identified by its ``file_path``.

        When *scope_prefix* is provided, only documents whose ``file_path``
        starts with that prefix will be processed.  Any other PENDING /
        FAILED documents are temporarily shelved so that LightRAG's
        ``apipeline_process_enqueue_documents`` does not sweep them up.

        Returns the number of chunks successfully inserted.
        """
        rag = await self._ensure_rag()
        if rag is None:
            return 0

        if scope_prefix:
            return await self._insert_chunks_scoped(rag, chunks, scope_prefix)

        # Unscoped: original per-chunk insert (used by bulk ingest)
        count = 0
        for ch in chunks:
            try:
                await rag.ainsert(ch.text, file_paths=ch.file_path)
                count += 1
            except Exception as exc:
                _log.warning("KB chunk insert failed for %s: %s", ch.file_path, exc)
        return count

    async def _insert_chunks_scoped(
        self, rag: Any, chunks: list[Chunk], scope_prefix: str
    ) -> int:
        """Insert chunks while preventing LightRAG from processing
        unrelated PENDING / FAILED documents.

        1. Shelve (delete) all PENDING & FAILED docs not matching *scope_prefix*
        2. Batch-enqueue & process only our chunks via ``ainsert``
        3. Restore shelved docs so they remain queued for later bulk ingest
        """
        from dataclasses import asdict

        from lightrag.base import DocStatus

        # ── 1. Shelve stale docs ──────────────────────────────────────────
        shelved: dict[str, dict[str, Any]] = {}
        for status in (DocStatus.PENDING, DocStatus.FAILED):
            docs = await rag.doc_status.get_docs_by_status(status)
            for doc_id, info in docs.items():
                fp = getattr(info, "file_path", "") or ""
                if not fp.startswith(scope_prefix):
                    raw = asdict(info)
                    # Serialize the DocStatus enum to its string value
                    raw["status"] = (
                        raw["status"].value
                        if hasattr(raw["status"], "value")
                        else str(raw["status"])
                    )
                    shelved[doc_id] = raw

        if shelved:
            _log.debug(
                "Scoped insert: shelving %d unrelated PENDING/FAILED docs",
                len(shelved),
            )
            await rag.doc_status.delete(list(shelved.keys()))

        # ── 2. Batch insert our chunks ────────────────────────────────────
        count = 0
        try:
            texts = [ch.text for ch in chunks]
            paths = [ch.file_path for ch in chunks]
            await rag.ainsert(texts, file_paths=paths)
            count = len(chunks)
        except Exception as exc:
            _log.warning("Scoped batch insert failed: %s", exc)
            # Fallback: try one-by-one
            for ch in chunks:
                try:
                    await rag.ainsert(ch.text, file_paths=ch.file_path)
                    count += 1
                except Exception as exc2:
                    _log.warning(
                        "KB chunk insert failed for %s: %s", ch.file_path, exc2
                    )

        # ── 3. Restore shelved docs ───────────────────────────────────────
        if shelved:
            try:
                await rag.doc_status.upsert(shelved)
                _log.debug("Restored %d shelved docs", len(shelved))
            except Exception as exc:
                _log.warning("Failed to restore %d shelved docs: %s", len(shelved), exc)

        return count

    async def delete_by_path_prefix(self, prefix: str) -> int:
        """Delete all documents whose ``file_path`` starts with *prefix*.

        Removes chunks, vector entries, and graph elements via
        LightRAG's :meth:`adelete_by_doc_id`.  Returns the number of
        documents removed.
        """
        rag = await self._ensure_rag()
        if rag is None or not prefix:
            return 0

        try:
            from lightrag.base import DocStatus
        except ImportError:
            return 0

        # Collect doc-ids whose file_path starts with prefix across all statuses
        matching: list[str] = []
        try:
            for status in DocStatus:
                docs = await rag.get_docs_by_status(status)
                for doc_id, info in docs.items():
                    fp = getattr(info, "file_path", "") or ""
                    if fp.startswith(prefix):
                        matching.append(doc_id)
        except Exception as exc:
            _log.warning("Listing docs for prefix %r failed: %s", prefix, exc)
            return 0

        removed = 0
        for doc_id in matching:
            try:
                await rag.adelete_by_doc_id(doc_id)
                removed += 1
            except Exception as exc:
                _log.warning("Delete failed for %s: %s", doc_id, exc)
        if removed:
            _log.info("Removed %d KB doc(s) under prefix %r", removed, prefix)
        return removed

    async def cleanup_failed_duplicates(
        self, file_paths: list[str] | None = None
    ) -> int:
        """Remove FAILED documents from LightRAG (typically duplicates).

        When re-ingesting, LightRAG marks duplicate chunks as FAILED.
        These stale entries pollute the status tracking. This method
        removes them so only the successfully PROCESSED version remains.

        Args:
            file_paths: If given, only clean up FAILED docs whose
                ``file_path`` is in this set.  Otherwise clean *all*
                FAILED docs.

        Returns the number of FAILED documents removed.
        """
        rag = await self._ensure_rag()
        if rag is None:
            return 0

        try:
            from lightrag.base import DocStatus
        except ImportError:
            return 0

        try:
            failed_docs = await rag.get_docs_by_status(DocStatus.FAILED)
        except Exception as exc:
            _log.warning("Could not list FAILED docs: %s", exc)
            return 0

        path_set = set(file_paths) if file_paths else None
        to_remove: list[str] = []
        for doc_id, info in failed_docs.items():
            fp = getattr(info, "file_path", "") or ""
            if path_set is None or fp in path_set:
                to_remove.append(doc_id)

        removed = 0
        for doc_id in to_remove:
            try:
                await rag.adelete_by_doc_id(doc_id)
                removed += 1
            except Exception as exc:
                _log.warning("Failed to remove FAILED doc %s: %s", doc_id, exc)

        if removed:
            _log.info("Cleaned up %d FAILED (duplicate) doc(s)", removed)
        return removed

    async def insert_batch(
        self, texts: list[str], file_paths: list[str] | None = None
    ) -> int:
        """Insert multiple documents. Returns count of successfully inserted."""
        rag = await self._ensure_rag()
        if rag is None:
            return 0
        count = 0
        for i, text in enumerate(texts):
            try:
                fp = file_paths[i] if file_paths and i < len(file_paths) else None
                await rag.ainsert(text, file_paths=fp)
                count += 1
            except Exception as exc:
                _log.warning("KB insert failed for one document: %s", exc)
        return count

    async def insert_all_with_progress(
        self,
        chunks: list[tuple[str, str]],  # (text, file_path)
    ) -> AsyncIterator[tuple[int, int]]:
        """Enqueue all chunks at once and stream (completed, total) progress.

        Uses LightRAG's batch pipeline instead of sequential ainsert() calls.
        Sequential ainsert() causes all progress events to fire before actual
        processing finishes because LightRAG's single-worker lock makes calls
        2-N return immediately while the first call's pipeline is running.

        Yields (completed, total) tuples as documents finish processing.
        """
        from lightrag.base import DocStatus

        rag = await self._ensure_rag()
        total = len(chunks)
        if rag is None or total == 0:
            yield (total, total)
            return

        texts = [c[0] for c in chunks]
        file_paths_list = [c[1] for c in chunks]

        # Snapshot baseline terminal-state counts (already processed before this run)
        baseline = await rag.get_processing_status()
        baseline_done = baseline.get(DocStatus.PROCESSED.value, 0) + baseline.get(
            DocStatus.FAILED.value, 0
        )

        # Enqueue all documents at once (marks them PENDING, does not process yet)
        await rag.apipeline_enqueue_documents(texts, file_paths=file_paths_list)

        # Kick off the pipeline (may return early if another pipeline is busy;
        # the running pipeline automatically picks up our newly-queued docs)
        process_task = asyncio.create_task(rag.apipeline_process_enqueue_documents())

        last_reported = -1
        no_change_ticks = 0
        # Require in_flight == 0 for several consecutive ticks before
        # concluding that processing really finished.  A brief dip to 0
        # between batches used to terminate the loop prematurely.
        idle_confirm_ticks = 0
        _IDLE_CONFIRM_NEEDED = 6  # 6 × 0.5 s = 3 s of sustained idle

        try:
            while True:
                await asyncio.sleep(0.5)

                status = await rag.get_processing_status()
                in_flight = (
                    status.get(DocStatus.PENDING.value, 0)
                    + status.get(DocStatus.PROCESSING.value, 0)
                    + status.get(DocStatus.PREPROCESSED.value, 0)
                )
                done = status.get(DocStatus.PROCESSED.value, 0) + status.get(
                    DocStatus.FAILED.value, 0
                )
                delta = max(0, done - baseline_done)

                if delta != last_reported:
                    last_reported = delta
                    no_change_ticks = 0
                    idle_confirm_ticks = 0
                    yield (min(delta, total), total)
                else:
                    no_change_ticks += 1

                if in_flight == 0:
                    idle_confirm_ticks += 1
                else:
                    idle_confirm_ticks = 0

                # If the pipeline task crashed, re-launch it so remaining
                # PENDING documents still get processed.
                if process_task.done() and in_flight > 0:
                    exc = (
                        process_task.exception()
                        if not process_task.cancelled()
                        else None
                    )
                    if exc:
                        _log.warning(
                            "Pipeline task failed (%s), re-launching for "
                            "%d remaining in-flight docs",
                            exc,
                            in_flight,
                        )
                    else:
                        _log.info(
                            "Pipeline task finished but %d docs still "
                            "in-flight — re-launching",
                            in_flight,
                        )
                    process_task = asyncio.create_task(
                        rag.apipeline_process_enqueue_documents()
                    )

                # Termination conditions (all require sustained idle):
                #  1. All chunks accounted for (delta >= total)
                #  2. Sustained idle: in_flight == 0 for _IDLE_CONFIRM_NEEDED
                #     consecutive ticks AND the pipeline task has finished
                #  3. Safety net: no progress at all for 60 s (120 ticks)
                all_done = delta >= total
                sustained_idle = (
                    idle_confirm_ticks >= _IDLE_CONFIRM_NEEDED and process_task.done()
                )
                stalled = no_change_ticks >= 120  # 60 s

                if all_done or sustained_idle or stalled:
                    if stalled and not all_done:
                        _log.warning(
                            "Ingestion stalled for 60 s at %d/%d — "
                            "stopping progress loop",
                            delta,
                            total,
                        )
                    if delta != last_reported:
                        yield (min(delta, total), total)
                    break
        finally:
            if not process_task.done():
                process_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await process_task

    def has_data(self) -> bool:
        """Quick check whether the KB working dir contains indexed data."""
        graph_file = self._working_dir / "graph_chunk_entity_relation.graphml"
        return graph_file.exists()

    async def verify_ingested(self, path_prefix: str) -> bool:
        """Check if any PROCESSED documents exist in LightRAG for *path_prefix*.

        This queries LightRAG's actual doc store (not just the local manifest)
        to confirm that chunks for the given path prefix are genuinely present.
        Returns True if at least one PROCESSED document matches.
        """
        rag = await self._ensure_rag()
        if rag is None:
            return False

        try:
            from lightrag.base import DocStatus

            docs = await rag.get_docs_by_status(DocStatus.PROCESSED)
            for _doc_id, info in docs.items():
                fp = getattr(info, "file_path", "") or ""
                if fp.startswith(path_prefix):
                    return True
        except Exception as exc:
            _log.warning("verify_ingested failed for %r: %s", path_prefix, exc)
        return False

    async def count_docs_by_prefix(self, path_prefix: str) -> dict[str, int]:
        """Count documents by status for a given path prefix.

        Returns a dict like ``{"PROCESSED": 5, "FAILED": 1, "PENDING": 0}``.
        """
        rag = await self._ensure_rag()
        if rag is None:
            return {}

        try:
            from lightrag.base import DocStatus

            counts: dict[str, int] = {}
            for status in DocStatus:
                docs = await rag.get_docs_by_status(status)
                n = sum(
                    1
                    for info in docs.values()
                    if (getattr(info, "file_path", "") or "").startswith(path_prefix)
                )
                if n > 0:
                    counts[status.value] = n
            return counts
        except Exception as exc:
            _log.warning("count_docs_by_prefix failed: %s", exc)
            return {}

    def stats(self) -> dict[str, Any]:
        return {
            "working_dir": str(self._working_dir),
            "has_data": self.has_data(),
            "tokens": self._token_counter.as_dict(),
        }

    async def list_docs_by_prefix(self, prefix: str) -> dict[str, tuple[str, int]]:
        """Return ``{doc_id: (file_path, content_length)}`` for PROCESSED
        documents whose ``file_path`` starts with *prefix*.
        """
        rag = await self._ensure_rag()
        if rag is None:
            return {}
        try:
            from lightrag.base import DocStatus

            docs = await rag.get_docs_by_status(DocStatus.PROCESSED)
            return {
                doc_id: (
                    getattr(info, "file_path", "") or "",
                    getattr(info, "content_length", 0) or 0,
                )
                for doc_id, info in docs.items()
                if (getattr(info, "file_path", "") or "").startswith(prefix)
            }
        except Exception as exc:
            _log.warning("list_docs_by_prefix(%r) failed: %s", prefix, exc)
            return {}

    async def delete_doc_ids(self, doc_ids: list[str]) -> int:
        """Delete specific document IDs from LightRAG.

        Returns the number successfully removed.
        """
        rag = await self._ensure_rag()
        if rag is None:
            return 0
        removed = 0
        for doc_id in doc_ids:
            try:
                await rag.adelete_by_doc_id(doc_id)
                removed += 1
            except Exception as exc:
                _log.warning("delete doc %s failed: %s", doc_id, exc)
        return removed

    def get_token_stats(self) -> dict[str, int]:
        """Return accumulated token usage for LightRAG LLM calls."""
        return self._token_counter.as_dict()

    def extract_graph_edges(self) -> list[dict[str, str]]:
        """Extract traceability-relevant edges from LightRAG's entity graph.

        Reads the NetworkX graphml file and returns edges whose labels
        indicate artifact relationships (IMPLEMENTS, SUPERSEDES, TESTED_BY,
        TRACES_TO, etc.).

        Returns a list of ``{"source": ..., "target": ..., "type": ...}`` dicts.
        """
        graph_file = self._working_dir / "graph_chunk_entity_relation.graphml"
        if not graph_file.exists():
            return []

        try:
            import networkx as nx

            G = nx.read_graphml(graph_file)
        except Exception as exc:
            _log.warning("Failed to read LightRAG graph for edge extraction: %s", exc)
            return []

        # Relationship keywords we care about — word-boundary regex to avoid
        # false positives on labels like "ALGORITHM IMPLEMENTS THE PROTOCOL"
        _EDGE_RE = re.compile(
            r"\b(?P<kw>IMPLEMENTS|SUPERSEDES|TESTED_BY|TESTS|TRACES_TO|"
            r"TRACES_FROM|DEPENDS_ON|DERIVED_FROM)\b"
        )
        _EDGE_MAPPING = {
            "IMPLEMENTS": "IMPLEMENTS",
            "SUPERSEDES": "SUPERSEDES",
            "TESTED_BY": "TESTED_BY",
            "TESTS": "TESTED_BY",
            "TRACES_TO": "TRACES_TO",
            "TRACES_FROM": "TRACES_FROM",
            "DEPENDS_ON": "TRACES_TO",
            "DERIVED_FROM": "TRACES_FROM",
        }

        edges: list[dict[str, str]] = []
        for u, v, data in G.edges(data=True):
            label = str(data.get("label", "") or data.get("description", "")).upper()
            # Only match short labels (≤ 5 words) to avoid noisy descriptions
            if len(label.split()) > 5:
                continue
            m = _EDGE_RE.search(label)
            if m:
                edge_type = _EDGE_MAPPING[m.group("kw")]
                edges.append(
                    {
                        "source": str(u),
                        "target": str(v),
                        "type": edge_type,
                        "label": str(data.get("label", "")),
                    }
                )
        return edges

    @classmethod
    def from_config(cls, config: SdlicitConfig) -> KnowledgeBase:
        return cls(
            working_dir=config.kb_path,
            provider=config.provider,
            model=config.model,
            api_key=config.api_key,
            embed_model=config.embed_model,
            embed_dim=config.embed_dim,
            embed_max_tokens=config.embed_max_tokens,
            embed_provider=config.embed_provider or config.provider,
            ollama_host=config.ollama_host,
        )

"""Knowledge Base Router — intelligent store-aware RAG routing.

Wraps KnowledgeBase to provide:
- Store-specific queries (knowledge vs artifacts vs all)
- Graph entity probing to decide if a query is worth executing
- Auto-ingest of artifacts into the artifact store namespace
- Configurable probe method (networkx graph check or LightRAG local mode)
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sdlicit.helpers.chunkers import Chunk, get_chunker
from sdlicit.expansion_stage.tools.knowledge_base import KBChunk, KnowledgeBase
from sdlicit.logging import get_logger

if TYPE_CHECKING:
    from sdlicit.config import SdlicitConfig

_log = get_logger("kb_router")

StoreType = Literal["knowledge", "artifacts", "all"]
ProbeMethod = Literal["networkx", "local"]


class StaticContextProvider:
    """Lightweight KB substitute that returns static context for all queries.

    Used when ``context_override`` is set in config — allows fast testing
    without LightRAG. Implements the same interface agents expect from KBRouter.
    """

    def __init__(self, context: str) -> None:
        self._context = context

    @property
    def kb(self) -> None:
        """No underlying KnowledgeBase."""
        return None

    async def query(
        self,
        text: str,
        store: "StoreType | None" = None,
        mode: str = "hybrid",
        top_k: int = 5,
        probe_first: bool = False,
    ) -> list[KBChunk]:
        """Return the static context as a single chunk (or empty if no context)."""
        if not self._context:
            return []
        return [KBChunk(text=self._context, source="context_override", relevance=1.0)]

    async def probe(self, text: str) -> bool:
        return bool(self._context)

    async def ingest_artifact(self, text: str, artifact_type: str, name: str) -> int:
        """No-op — static provider does not ingest."""
        return 0

    async def delete_artifact(self, artifact_type: str, name: str) -> int:
        """No-op."""
        return 0

    async def replace_artifact(self, text: str, artifact_type: str, name: str) -> int:
        """No-op."""
        return 0


class KBRouter:
    """Routing layer over KnowledgeBase for store-aware retrieval.

    Agents receive a KBRouter instead of raw KnowledgeBase. The router:
    1. Resolves which store to query based on agent preference + context
    2. Optionally probes the graph to check if retrieval is worthwhile
    3. Applies source filtering for store-specific queries
    4. Provides artifact auto-ingestion
    """

    def __init__(
        self,
        kb: KnowledgeBase,
        config: "SdlicitConfig",
        default_store: StoreType = "all",
    ) -> None:
        self._kb = kb
        self._config = config
        self._default_store = default_store
        self._probe_method: ProbeMethod = config.kb_probe_method
        self._knowledge_prefix = config.kb_knowledge_prefix
        self._artifacts_prefix = config.kb_artifacts_prefix

    @property
    def kb(self) -> KnowledgeBase:
        """Direct access to underlying KnowledgeBase (for backward compat)."""
        return self._kb

    async def query(
        self,
        text: str,
        store: StoreType | None = None,
        mode: str = "hybrid",
        top_k: int = 5,
        probe_first: bool = False,
    ) -> list[KBChunk]:
        """Query the knowledge base with store routing.

        Args:
            text: Query text.
            store: Which store to search. None uses the default_store.
            mode: LightRAG retrieval mode (naive, local, global, hybrid, mix).
            top_k: Number of results.
            probe_first: If True, check graph entities before full retrieval.
                         Returns empty if no relevant entities found.
        """
        effective_store = store or self._default_store

        if probe_first:
            has_relevant = await self.probe(text)
            if not has_relevant:
                _log.debug("Probe found no relevant entities for: %s", text[:80])
                return []

        prefix = self._store_to_prefix(effective_store)
        if prefix:
            return await self._kb.query_filtered(
                text, path_prefix=prefix, mode=mode, top_k=top_k
            )
        else:
            return await self._kb.query(text, mode=mode, top_k=top_k)

    async def probe(self, text: str) -> bool:
        """Check if a query is worth executing against the KB.

        Uses the configured probe method:
        - "networkx": Extract key terms and check graph entity names (fast, no LLM)
        - "local": Use LightRAG local mode with top_k=1 (uses LLM, more accurate)
        """
        if self._probe_method == "networkx":
            terms = self._extract_probe_terms(text)
            if not terms:
                return True  # Can't extract terms → query anyway
            results = await self._kb.probe_graph_entities(terms)
            return any(results.values())
        else:
            return await self._kb.probe_local(text)

    async def ingest_artifact(self, text: str, artifact_type: str, name: str) -> int:
        """Ingest a produced artifact (SOW, ADR, persona, story, SRS, ...).

        Uses a structure-aware :class:`Chunker` for *artifact_type* so
        each section / requirement / persona becomes its own LightRAG
        document with a compound ``file_path`` of the form
        ``{prefix}{type}/{name}.md#{n}-{slug}``.

        Args:
            text: Full artifact content.
            artifact_type: Type label (e.g. "sow", "adr", "personas").
            name: Artifact identifier (e.g. "0001-auth-strategy", "latest").

        Returns the number of chunks inserted.
        """
        base_path = f"{self._artifacts_prefix}{artifact_type}/{name}.md"
        chunker = get_chunker(artifact_type)
        chunks: list[Chunk] = chunker.chunk(text, base_path)
        if not chunks:
            _log.warning(
                "Chunker for %s produced no chunks — falling back to single insert",
                artifact_type,
            )
            await self._kb.insert(text, file_path=base_path)
            return 1

        _log.info("Auto-ingesting %s/%s as %d chunks", artifact_type, name, len(chunks))
        return await self._kb.insert_chunks(chunks, scope_prefix=base_path)

    async def delete_artifact(self, artifact_type: str, name: str) -> int:
        """Remove all KB chunks for an artifact (graph + vector).

        Returns the number of LightRAG documents removed.
        """
        base_path = f"{self._artifacts_prefix}{artifact_type}/{name}.md"
        return await self._kb.delete_by_path_prefix(base_path)

    async def replace_artifact(self, text: str, artifact_type: str, name: str) -> int:
        """Delete any existing chunks for *(artifact_type, name)* and
        re-ingest *text*.  Returns the number of new chunks inserted.
        """
        await self.delete_artifact(artifact_type, name)
        return await self.ingest_artifact(text, artifact_type, name)

    async def replace_artifact_incremental(
        self, text: str, artifact_type: str, name: str
    ) -> dict[str, int]:
        """Smart incremental replace: only update chunks that changed.

        1. Chunk the new text.
        2. Load stored content hashes (sidecar JSON).
        3. Compare new chunk hashes to stored hashes.
        4. Delete only chunks whose content changed or were removed.
        5. Insert only new or changed chunks.
        6. Save updated hashes.

        Returns ``{"added": n, "removed": m, "unchanged": u}``.
        """
        base_path = f"{self._artifacts_prefix}{artifact_type}/{name}.md"
        chunker = get_chunker(artifact_type)
        new_chunks: list[Chunk] = chunker.chunk(text, base_path)
        if not new_chunks:
            _log.warning(
                "Incremental replace: chunker produced 0 chunks for %s/%s, "
                "falling back to full replace",
                artifact_type,
                name,
            )
            count = await self.replace_artifact(text, artifact_type, name)
            return {"added": count, "removed": 0, "unchanged": 0}

        # Build new hash map: file_path → sha256
        new_hashes: dict[str, str] = {}
        new_by_path: dict[str, Chunk] = {}
        for ch in new_chunks:
            h = hashlib.sha256(ch.text.encode()).hexdigest()
            new_hashes[ch.file_path] = h
            new_by_path[ch.file_path] = ch

        # Load old hashes
        old_hashes = self._load_chunk_hashes(artifact_type, name)

        # Verify sidecar matches LightRAG state; force full replace on mismatch
        if old_hashes:
            existing_docs = await self._kb.list_docs_by_prefix(base_path)
            existing_paths = set(fp for fp, _length in existing_docs.values())
            sidecar_paths = set(old_hashes.keys())
            if existing_paths != sidecar_paths:
                _log.warning(
                    "Sidecar/LightRAG mismatch for %s/%s — forcing full replace",
                    artifact_type,
                    name,
                )
                count = await self.replace_artifact(text, artifact_type, name)
                self._save_chunk_hashes(artifact_type, name, new_hashes)
                return {"added": count, "removed": 0, "unchanged": 0}

        # Diff
        to_delete: list[str] = []  # file_paths to remove
        to_insert: list[Chunk] = []  # chunks to (re)insert
        unchanged = 0

        for fp, h in new_hashes.items():
            if fp in old_hashes and old_hashes[fp] == h:
                unchanged += 1
            else:
                # Changed or new chunk
                if fp in old_hashes:
                    to_delete.append(fp)
                to_insert.append(new_by_path[fp])

        # Removed sections: in old but not in new
        for fp in old_hashes:
            if fp not in new_hashes:
                to_delete.append(fp)

        # Execute deletions
        removed = 0
        if to_delete:
            existing_docs = await self._kb.list_docs_by_prefix(base_path)
            # Map file_path → doc_id for targeted deletion
            fp_to_docid: dict[str, str] = {}
            for doc_id, (fp, _length) in existing_docs.items():
                fp_to_docid[fp] = doc_id
            doc_ids_to_remove = [
                fp_to_docid[fp] for fp in to_delete if fp in fp_to_docid
            ]
            if doc_ids_to_remove:
                removed = await self._kb.delete_doc_ids(doc_ids_to_remove)

        # Execute insertions
        added = 0
        if to_insert:
            added = await self._kb.insert_chunks(to_insert, scope_prefix=base_path)

        # Save new hashes
        self._save_chunk_hashes(artifact_type, name, new_hashes)

        _log.info(
            "Incremental replace %s/%s: added=%d removed=%d unchanged=%d",
            artifact_type,
            name,
            added,
            removed,
            unchanged,
        )
        return {"added": added, "removed": removed, "unchanged": unchanged}

    # ---- Chunk hash sidecar persistence ----

    def _hash_sidecar_path(self, artifact_type: str, name: str) -> Path:
        """Path to the sidecar JSON storing chunk hashes for an artifact."""
        return (
            self._kb._working_dir
            / "chunk_hashes"
            / f"{artifact_type}_{name}.json"
        )

    def _load_chunk_hashes(self, artifact_type: str, name: str) -> dict[str, str]:
        """Load ``{file_path: sha256}`` from sidecar, or empty dict."""
        path = self._hash_sidecar_path(artifact_type, name)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    def _save_chunk_hashes(
        self, artifact_type: str, name: str, hashes: dict[str, str]
    ) -> None:
        """Persist ``{file_path: sha256}`` to sidecar."""
        path = self._hash_sidecar_path(artifact_type, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(hashes, indent=2))

    def _store_to_prefix(self, store: StoreType) -> str:
        """Map store type to file_path prefix for filtering."""
        if store == "knowledge":
            return self._knowledge_prefix
        elif store == "artifacts":
            return self._artifacts_prefix
        return ""  # "all" → no filtering

    async def cleanup_stale_artifacts(self, project_dir: Path) -> dict[str, int]:
        """Remove LightRAG chunks for artifacts whose source files no longer
        exist on disk.

        Iterates chunk-hash sidecar files to discover tracked artifacts,
        extracts the artifact base path from the stored chunk file_paths,
        and resolves that back to the actual on-disk location.  Artifacts
        may live in different layouts:

        - Flat:  ``.sdlicit/artifacts/SOW-001.md``
        - Subdir: ``.sdlicit/artifacts/adr/0001-auth.md``

        The LightRAG ``file_path`` uses the virtual form
        ``artifacts/{type}/{name}.md#…``.  We try both the subdirectory
        layout and the flat layout when checking for existence.

        Returns ``{"checked": n, "removed": m, "removed_chunks": c}``.
        """
        hash_dir = self._kb._working_dir / "chunk_hashes"
        if not hash_dir.is_dir():
            return {"checked": 0, "removed": 0, "removed_chunks": 0}

        sidecars = list(hash_dir.glob("*.json"))
        removed = 0
        removed_chunks = 0

        artifacts_root = project_dir / ".sdlicit" / "artifacts"

        for sidecar in sidecars:
            # Load the sidecar to get the base_path from stored chunk keys
            try:
                hashes: dict[str, str] = json.loads(sidecar.read_text())
            except Exception:
                continue
            if not hashes:
                continue

            # Extract base_path from the first chunk key
            # e.g. "artifacts/sow/sow_20260510.md#1-preamble"
            #   → base = "artifacts/sow/sow_20260510.md"
            first_key = next(iter(hashes))
            base_path = first_key.split("#")[0]  # "artifacts/sow/sow_20260510.md"

            # Strip the "artifacts/" prefix to get "{type}/{name}.md"
            rel = base_path.removeprefix(self._artifacts_prefix)  # "sow/sow_20260510.md"
            parts = rel.split("/", 1)
            if len(parts) != 2:
                continue
            artifact_type, filename = parts  # ("sow", "sow_20260510.md")

            # Check both possible on-disk layouts:
            #   1) Subdirectory: .sdlicit/artifacts/sow/sow_20260510.md
            #   2) Flat:         .sdlicit/artifacts/sow_20260510.md
            subdir_path = artifacts_root / artifact_type / filename
            flat_path = artifacts_root / filename

            if subdir_path.exists() or flat_path.exists():
                continue

            # Artifact file is gone — purge chunks from LightRAG
            name = Path(filename).stem
            n = await self.delete_artifact(artifact_type, name)
            removed_chunks += n
            removed += 1

            # Remove the sidecar file
            try:
                sidecar.unlink()
            except OSError:
                pass

            _log.info(
                "Stale artifact removed: %s/%s (%d chunk(s) purged)",
                artifact_type,
                name,
                n,
            )

        if removed:
            _log.info(
                "Artifact staleness check: %d artifact(s), %d chunk(s) removed",
                removed,
                removed_chunks,
            )
        else:
            _log.debug(
                "Artifact staleness check: all %d tracked artifact(s) present",
                len(sidecars),
            )

        return {
            "checked": len(sidecars),
            "removed": removed,
            "removed_chunks": removed_chunks,
        }

    @staticmethod
    def _extract_probe_terms(text: str) -> list[str]:
        """Extract key terms from query text for graph entity probing.

        Extracts capitalized phrases, individual capitalized words,
        quoted terms, and technical acronyms.
        """
        terms: list[str] = []

        # Quoted terms
        quoted = re.findall(r'"([^"]+)"', text)
        terms.extend(quoted)

        # Capitalized multi-word phrases (likely proper nouns / standards)
        caps = re.findall(r"\b([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)+)\b", text)
        terms.extend(caps)

        # Individual capitalized words (single proper nouns, e.g. "Outlook", "GitHub")
        single_caps = re.findall(r"\b([A-Z][a-z]{2,})\b", text)
        terms.extend(single_caps)

        # ISO/standard references
        standards = re.findall(r"\b(ISO[\s-]?\d+(?:[-:]\d+)?)\b", text, re.IGNORECASE)
        terms.extend(standards)

        # Technical acronyms (2+ uppercase letters)
        acronyms = re.findall(r"\b([A-Z]{2,})\b", text)
        terms.extend(a for a in acronyms if len(a) <= 10)

        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for t in terms:
            t_lower = t.lower()
            if t_lower not in seen:
                seen.add(t_lower)
                unique.append(t)
        return unique

    @classmethod
    def from_config(
        cls,
        kb: KnowledgeBase,
        config: "SdlicitConfig",
        agent_name: str = "",
    ) -> "KBRouter":
        """Create a KBRouter with agent-specific default store from config."""
        default_store: StoreType = "all"
        if agent_name and agent_name in config.kb_agent_stores:
            store_val = config.kb_agent_stores[agent_name]
            if store_val in ("knowledge", "artifacts", "all"):
                default_store = store_val  # type: ignore[assignment]

        return cls(kb=kb, config=config, default_store=default_store)

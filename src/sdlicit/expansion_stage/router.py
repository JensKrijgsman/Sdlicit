"""FastAPI router for the expansion stage.

No mutable module-level state.  The Orchestrator is injected via
``app.state.orchestrator`` using FastAPI's ``Depends()``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from sdlicit.config import SdlicitConfig
from sdlicit.expansion_stage.document_scanner import (
    IngestionManifest,
    extract_and_chunk,
    file_hash,
    scan_documents,
)
from sdlicit.expansion_stage.tools.knowledge_base import KnowledgeBase
from sdlicit.logging import get_logger
from sdlicit.orchestrator import Orchestrator

from .models import (
    ArtifactCoverageResponse,
    ArtifactKBStatusEntry,
    ArtifactKBStatusResponse,
    DeleteArtifactRequest,
    DeleteArtifactResponse,
    ExpandRequest,
    ExpandResponse,
    IngestArtifactRequest,
    IngestArtifactResponse,
    KBChunk,
    KBIngestRequest,
    KBQueryRequest,
    KBQueryResponse,
    KBScanResponse,
    KBStatusResponse,
    LocateChunkRequest,
    LocateChunkResponse,
    RAGChunk,
    RAGQueryRequest,
    RAGQueryResponse,
    SupersedeADRRequest,
    SupersedeADRResponse,
    TraceCheckRequest,
    TraceCheckResponse,
    TraceCheckResult,
    TraceCoverageRequest,
    TraceCoverageResponse,
    TraceGraphResponse,
)
from .service import expand_adr

_log = get_logger("route")

router = APIRouter(prefix="/expansion", tags=["expansion"])


def _get_orchestrator(request: Request) -> Orchestrator:
    orch = request.app.state.orchestrator
    if orch is None:
        raise HTTPException(
            status_code=503, detail="Not initialised — POST /api/v1/init first"
        )
    return orch


@router.post("/expand", response_model=ExpandResponse)
async def post_expand(
    request: ExpandRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> ExpandResponse:
    _log.info("POST /expansion/expand")
    return await expand_adr(request, orchestrator)


@router.post("/query-kb", response_model=KBQueryResponse)
async def post_query_kb(
    request: KBQueryRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> KBQueryResponse:
    _log.info("POST /expansion/query-kb  mode=%s", request.mode)
    if orchestrator.kb is None:
        return KBQueryResponse(results=[], rag_enabled=False)
    chunks = await orchestrator.query_kb(request.query, mode=request.mode)
    return KBQueryResponse(
        results=[KBChunk(**c) for c in chunks],
        rag_enabled=True,
    )


@router.post("/query-rag", response_model=RAGQueryResponse)
async def post_query_rag(
    request: RAGQueryRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> RAGQueryResponse:
    """Store-aware RAG query with optional graph probing."""
    _log.info(
        "POST /expansion/query-rag  store=%s mode=%s probe=%s",
        request.store,
        request.mode,
        request.probe_first,
    )
    if orchestrator.kb_router is None:
        return RAGQueryResponse(results=[], rag_enabled=False)
    chunks = await orchestrator.kb_router.query(
        text=request.query,
        store=request.store,  # type: ignore[arg-type]
        mode=request.mode,
        top_k=request.top_k,
        probe_first=request.probe_first,
    )
    return RAGQueryResponse(
        results=[
            RAGChunk(
                text=c.text,
                source=c.source,
                relevance=c.relevance,
                mode=c.mode,
                store=c.store,
            )
            for c in chunks
        ],
        rag_enabled=True,
        probed=request.probe_first,
        store=request.store,
    )


# ---------------------------------------------------------------------------
# Knowledge-base ingestion — backend reads files directly
# ---------------------------------------------------------------------------


def _kb_for_project(
    orchestrator: Orchestrator, project_dir: str
) -> KnowledgeBase | None:
    """Build a KnowledgeBase rooted at *project_dir* (or use server default)."""
    if orchestrator.kb is None:
        return None
    if project_dir:
        cfg = SdlicitConfig.from_project(Path(project_dir))
        return KnowledgeBase.from_config(cfg)
    return orchestrator.kb


@router.get("/scan-documents", response_model=KBScanResponse)
async def get_scan_documents(
    project_dir: str,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> KBScanResponse:
    """Scan a project directory and return found documents (no ingestion).

    Cross-references the local ingestion manifest with LightRAG's actual
    doc store to give accurate ingestion status.
    """
    _log.info("GET /expansion/scan-documents  project_dir=%s", project_dir)
    root = Path(project_dir)
    if not root.is_dir():
        return KBScanResponse(documents=[], total_files=0)

    docs = await asyncio.to_thread(scan_documents, root)

    # Resolve KB working dir for the manifest
    kb = _kb_for_project(orchestrator, project_dir)
    if kb is not None:
        manifest = await asyncio.to_thread(IngestionManifest, kb._working_dir)
        statuses = await asyncio.to_thread(manifest.all_statuses, docs)

        # Cross-check manifest "complete" entries with LightRAG's actual state.
        # If the manifest says complete but LightRAG has no PROCESSED docs for
        # that path prefix, downgrade to "none" (stale manifest entry).
        for doc in docs:
            if statuses.get(doc.relative_path) == "complete":
                prefix = f"knowledge/{doc.relative_path}"
                actually_present = await kb.verify_ingested(prefix)
                if not actually_present:
                    _log.info(
                        "Manifest says %s is complete but LightRAG has no "
                        "PROCESSED docs — marking as none",
                        doc.relative_path,
                    )
                    statuses[doc.relative_path] = "none"
    else:
        statuses = {}

    return KBScanResponse(
        documents=[
            {
                "relative_path": d.relative_path,
                "size_bytes": d.size_bytes,
                "suffix": d.suffix,
                "ingestion_status": statuses.get(d.relative_path, "none"),
            }
            for d in docs
        ],
        total_files=len(docs),
    )


async def _ingest_events(
    orchestrator: Orchestrator,
    project_dir: str,
    selected_files: list[str] | None = None,
) -> AsyncIterator[str]:
    """Yield SSE events as documents are scanned, chunked, and ingested."""
    kb = _kb_for_project(orchestrator, project_dir)
    if kb is None:
        yield _sse({"type": "error", "message": "RAG is disabled on the server"})
        return

    root = Path(project_dir)
    docs = await asyncio.to_thread(scan_documents, root)

    # Filter to selected files if provided
    if selected_files is not None:
        selected_set = set(selected_files)
        docs = [d for d in docs if d.relative_path in selected_set]

    if not docs:
        yield _sse({"type": "done", "ingested": 0, "total_chunks": 0, "errors": []})
        return

    # Build manifest for tracking
    manifest = await asyncio.to_thread(IngestionManifest, kb._working_dir)

    # When the user explicitly selected files, always re-ingest (delete old
    # chunks first to avoid duplicates).  Only auto-skip when no explicit
    # selection was given (i.e. ingest-all mode).
    pending_docs = []
    for doc in docs:
        fhash = await asyncio.to_thread(file_hash, doc.path)
        status = await asyncio.to_thread(manifest.get_status, doc.relative_path, fhash)
        if status == "complete" and selected_files is None:
            _log.info("Skipping already-ingested file: %s", doc.relative_path)
            continue
        # If re-ingesting an already-complete file, remove old chunks first
        if status == "complete" and selected_files is not None:
            prefix = f"knowledge/{doc.relative_path}"
            removed = await kb.delete_by_path_prefix(prefix)
            if removed:
                _log.info(
                    "Removed %d old chunk(s) for %s before re-ingestion",
                    removed,
                    doc.relative_path,
                )
        pending_docs.append((doc, fhash))

    if not pending_docs:
        yield _sse(
            {
                "type": "done",
                "ingested": 0,
                "total_chunks": 0,
                "errors": [],
                "skipped": len(docs),
            }
        )
        return

    # Extract and chunk all documents (CPU-bound → thread)
    # Track which doc each chunk range belongs to for manifest updates
    all_chunks = []
    doc_chunk_ranges: list[tuple[str, str, int, int]] = (
        []
    )  # (rel_path, fhash, start, end)
    for doc, fhash in pending_docs:
        start_idx = len(all_chunks)
        chunks = await asyncio.to_thread(extract_and_chunk, doc)
        if not chunks:
            yield _sse({
                "type": "error",
                "file": doc.relative_path,
                "message": "extraction produced zero chunks",
            })
        all_chunks.extend(chunks)
        end_idx = len(all_chunks)
        doc_chunk_ranges.append((doc.relative_path, fhash, start_idx, end_idx))
        # Mark partial before ingestion starts
        await asyncio.to_thread(
            manifest.mark_partial, doc.relative_path, fhash, len(chunks)
        )

    total = len(all_chunks)
    yield _sse({"type": "start", "total_chunks": total, "total_files": len(docs)})

    # Enqueue all chunks at once and stream real progress.
    chunk_pairs = [(c.text, c.source_name) for c in all_chunks]
    last_completed = 0

    async for completed, _ in kb.insert_all_with_progress(chunk_pairs):
        if completed != last_completed:
            last_completed = completed
            yield _sse(
                {
                    "type": "progress",
                    "current": completed,
                    "total": total,
                    "source_name": (
                        all_chunks[completed - 1].source_name if completed > 0 else ""
                    ),
                    "ok": True,
                }
            )

    # After processing, mark files complete in the manifest
    ingested = last_completed
    errors: list[str] = []
    if last_completed < total:
        errors = [
            f"Failed to process {total - last_completed} chunk(s) "
            "(duplicate or LightRAG error)"
        ]

    # Clean up FAILED entries (duplicates) so they don't pollute the KB.
    # Only target file_paths we just ingested.
    all_file_paths = [c.source_name for c in all_chunks]
    cleaned = await kb.cleanup_failed_duplicates(all_file_paths)
    if cleaned:
        _log.info("Cleaned up %d failed/duplicate entries after ingestion", cleaned)

    # Mark all docs complete (LightRAG deduplicates, so fewer chunks
    # processed than total is normal and still counts as success)
    for rel_path, fhash, _start, end in doc_chunk_ranges:
        await asyncio.to_thread(manifest.mark_complete, rel_path, fhash, end - _start)

    yield _sse(
        {
            "type": "done",
            "ingested": ingested,
            "total_chunks": total,
            "errors": errors,
        }
    )

    # Fire staleness check after ingestion so deleted files get purged.
    try:
        from sdlicit.expansion_stage.document_scanner import cleanup_stale_documents

        root = Path(project_dir)
        doc_result = await cleanup_stale_documents(root, kb)
        if doc_result["removed_files"]:
            _log.info("Post-ingest staleness check (docs): %s", doc_result)

        # Also check artifact staleness if a KBRouter is available
        if orchestrator.kb_router is not None:
            art_result = await orchestrator.kb_router.cleanup_stale_artifacts(root)
            if art_result["removed"]:
                _log.info("Post-ingest staleness check (artifacts): %s", art_result)
    except Exception as exc:
        _log.warning("Post-ingest staleness check failed: %s", exc)


def _sse(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"


@router.post("/ingest-kb")
async def post_ingest_kb(
    request: KBIngestRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> StreamingResponse:
    _log.info("POST /expansion/ingest-kb  project_dir=%s", request.project_dir)
    return StreamingResponse(
        _ingest_events(orchestrator, request.project_dir, request.selected_files),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/kb-status", response_model=KBStatusResponse)
async def get_kb_status(
    orchestrator: Orchestrator = Depends(_get_orchestrator),
    project_dir: str = "",
) -> KBStatusResponse:
    if orchestrator.kb is None:
        return KBStatusResponse(rag_enabled=False)

    kb = _kb_for_project(orchestrator, project_dir) or orchestrator.kb
    stats = kb.stats()
    return KBStatusResponse(
        rag_enabled=True,
        has_data=stats.get("has_data", False),
        working_dir=stats.get("working_dir", ""),
    )


@router.post("/ingest-artifact", response_model=IngestArtifactResponse)
async def post_ingest_artifact(
    request: IngestArtifactRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> IngestArtifactResponse:
    """Auto-ingest a produced artifact (SOW, ADR, personas, ...) into the KB.

    Uses a structure-aware chunker per :mod:`sdlicit.knowledge.chunkers`.
    Set ``replace=true`` to delete any prior chunks for the same artifact
    before ingesting (used for SOW updates).
    """
    if orchestrator.kb_router is None:
        return IngestArtifactResponse()

    if request.replace:
        result = await orchestrator.kb_router.replace_artifact_incremental(
            request.text, request.artifact_type, request.name
        )
        return IngestArtifactResponse(
            chunks=result["added"] + result["unchanged"],
            removed=result["removed"],
        )
    chunks = await orchestrator.kb_router.ingest_artifact(
        request.text, request.artifact_type, request.name
    )
    return IngestArtifactResponse(chunks=chunks, removed=0)


@router.post("/supersede-adr", response_model=SupersedeADRResponse)
async def post_supersede_adr(
    request: SupersedeADRRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> SupersedeADRResponse:
    """Mark an ADR as superseded: remove its KB chunks, ingest the new ADR.

    Also triggers a traceability check to flag impacted downstream artifacts.
    """
    result = await orchestrator.supersede_adr(
        request.old_adr_id, request.new_text, request.new_adr_id
    )
    return SupersedeADRResponse(
        removed=result.get("removed", 0),
        added=result.get("added", 0),
    )


@router.post("/delete-artifact", response_model=DeleteArtifactResponse)
async def post_delete_artifact(
    request: DeleteArtifactRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> DeleteArtifactResponse:
    """Delete an artifact's chunks from the KB."""
    if orchestrator.kb_router is None:
        return DeleteArtifactResponse()

    removed = await orchestrator.kb_router.delete_artifact(
        request.artifact_type, request.name
    )
    return DeleteArtifactResponse(removed=removed)


@router.get("/artifact-kb-status", response_model=ArtifactKBStatusResponse)
async def get_artifact_kb_status(
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> ArtifactKBStatusResponse:
    """Query KB ingestion status for all artifacts.

    Scans the ``artifacts/`` prefix in LightRAG to find which artifact
    types and names have PROCESSED chunks.
    """
    if orchestrator.kb is None:
        return ArtifactKBStatusResponse()

    kb = orchestrator.kb
    artifacts_prefix = orchestrator.config.kb_artifacts_prefix  # "artifacts/"

    try:
        from lightrag.base import DocStatus

        rag = await kb._ensure_rag()
        if rag is None:
            return ArtifactKBStatusResponse()

        # Collect all PROCESSED docs under artifacts/
        processed = await rag.get_docs_by_status(DocStatus.PROCESSED)
        # Group by artifact_type/name
        artifact_chunks: dict[tuple[str, str], int] = {}
        for _doc_id, info in processed.items():
            fp = getattr(info, "file_path", "") or ""
            if not fp.startswith(artifacts_prefix):
                continue
            # Parse: "artifacts/{type}/{name}.md#anchor"
            rest = fp[len(artifacts_prefix) :]
            parts = rest.split("/", 1)
            if len(parts) < 2:
                continue
            atype = parts[0]
            name_part = parts[1].split("#")[0]  # strip anchor
            if name_part.endswith(".md"):
                name_part = name_part[:-3]
            key = (atype, name_part)
            artifact_chunks[key] = artifact_chunks.get(key, 0) + 1

        entries = [
            ArtifactKBStatusEntry(
                artifact_type=atype,
                name=name,
                status="ingested",
                chunks=count,
            )
            for (atype, name), count in sorted(artifact_chunks.items())
        ]
        return ArtifactKBStatusResponse(artifacts=entries)
    except Exception as exc:
        _log.warning("artifact-kb-status failed: %s", exc)
        return ArtifactKBStatusResponse()


# ---------------------------------------------------------------------------
# Chunk source location — find page/position for explainability
# ---------------------------------------------------------------------------


def _locate_text_in_pdf(pdf_path: Path, snippet: str) -> tuple[int, float]:
    """Search for *snippet* text in a PDF, return (1-based page, match_score).

    Uses a sliding sentence-match approach: splits the snippet into
    sentences and checks which page contains the most matches.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        _log.warning("PyMuPDF not available — cannot locate text in PDF")
        return 1, 0.0

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        _log.warning("Could not open PDF %s: %s", pdf_path, exc)
        return 1, 0.0

    # Build searchable sentences from the snippet
    import re as _re

    # Strip the structured prefix like "[ISO 5.3.4 Portability]\n"
    clean_snippet = _re.sub(r"^\[.*?\]\n", "", snippet).strip()
    # Split into sentences/phrases for fuzzy matching
    sentences = [
        s.strip() for s in _re.split(r"[.;!\n]+", clean_snippet) if len(s.strip()) > 20
    ]
    if not sentences:
        # Fall back to searching for longest continuous substring
        sentences = (
            [clean_snippet[:200]] if len(clean_snippet) > 20 else [clean_snippet]
        )

    best_page = 1
    best_score = 0.0

    try:
        for page_num in range(doc.page_count):
            page = doc[page_num]
            page_text = page.get_text()
            if not page_text:
                continue
            page_lower = page_text.lower()
            matches = sum(1 for s in sentences if s.lower() in page_lower)
            score = matches / len(sentences) if sentences else 0.0
            if score > best_score:
                best_score = score
                best_page = page_num + 1  # 1-based
    finally:
        doc.close()

    return best_page, best_score


@router.post("/locate-chunk", response_model=LocateChunkResponse)
async def post_locate_chunk(
    request: LocateChunkRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> LocateChunkResponse:
    """Locate a chunk's text within its source document.

    Resolves the source reference to an actual file path and, for PDFs,
    searches for the chunk text to determine the page number.
    """
    _log.info("POST /expansion/locate-chunk  ref=%s", request.source_ref[:80])

    source_ref = request.source_ref
    # Split anchor from path: "knowledge/foo.pdf#4-1-scope" → ("knowledge/foo.pdf", "4-1-scope")
    if "#" in source_ref:
        rel_path, anchor = source_ref.rsplit("#", 1)
    else:
        rel_path, anchor = source_ref, ""

    # Resolve the KB namespace prefix to actual file path
    # "knowledge/.sdlicit/knowledge/ieee830.pdf" → ".sdlicit/knowledge/ieee830.pdf"
    # "artifacts/adr/0001.md" → ".sdlicit/artifacts/adr/0001.md"
    project_dir = (
        Path(request.project_dir)
        if request.project_dir
        else orchestrator.config.project_dir
    )

    actual_path: Path | None = None
    if rel_path.startswith("knowledge/"):
        # Strip the "knowledge/" prefix — it's a KB namespace, the rest is relative to project
        file_rel = rel_path[len("knowledge/") :]
        candidate = project_dir / file_rel
        if candidate.exists():
            actual_path = candidate
        else:
            # Try under .sdlicit/knowledge/
            candidate2 = project_dir / ".sdlicit" / "knowledge" / Path(file_rel).name
            if candidate2.exists():
                actual_path = candidate2
    elif rel_path.startswith("artifacts/"):
        candidate = project_dir / ".sdlicit" / rel_path
        if candidate.exists():
            actual_path = candidate

    if actual_path is None:
        # Last resort: try as direct relative path
        candidate = project_dir / rel_path
        if candidate.exists():
            actual_path = candidate

    if actual_path is None or not actual_path.exists():
        _log.warning("Could not resolve source ref to file: %s", source_ref)
        return LocateChunkResponse(found=False, anchor=anchor)

    suffix = actual_path.suffix.lower()
    file_type = suffix.lstrip(".")

    if suffix == ".pdf" and request.snippet:
        page, score = await asyncio.to_thread(
            _locate_text_in_pdf, actual_path, request.snippet
        )
        return LocateChunkResponse(
            found=score > 0,
            page=page,
            file_path=str(actual_path.resolve()),
            file_type=file_type,
            anchor=anchor,
            match_score=score,
        )

    # For text files, just return the file path and anchor
    return LocateChunkResponse(
        found=True,
        page=1,
        file_path=str(actual_path.resolve()),
        file_type=file_type,
        anchor=anchor,
        match_score=1.0,
    )


@router.get("/traceability-graph", response_model=TraceGraphResponse)
async def get_traceability_graph(
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> TraceGraphResponse:
    """Return the full traceability DAG for the frontend graph view."""
    graph_data = orchestrator.get_traceability_graph()
    return TraceGraphResponse(**graph_data)


@router.post("/check-traceability", response_model=TraceCheckResponse)
async def post_check_traceability(
    request: TraceCheckRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> TraceCheckResponse:
    """Run traceability checks for a specific artifact.

    Uses the TraceService (at least semantic mode) to detect content-level
    overlaps with existing requirements, and auto-suggests ``implements`` links.
    """
    result = await orchestrator.check_traceability(
        artifact_id=request.artifact_id,
        artifact_content=request.artifact_content or None,
    )
    issues = [
        TraceCheckResult(
            severity=i.get("severity", "warning"),
            message=i.get("message", ""),
            source_id=i.get("source_id", ""),
            target_id=i.get("target_id", ""),
        )
        for i in result.get("issues", [])
    ]
    return TraceCheckResponse(
        issues=issues,
        impacted_nodes=result.get("impacted_nodes", []),
        suggested_implements=result.get("suggested_implements", []),
    )


@router.post("/trace-coverage", response_model=TraceCoverageResponse)
async def post_trace_coverage(
    request: TraceCoverageRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> TraceCoverageResponse:
    """Run full trace coverage analysis on the workspace.

    Returns structural and (optionally) semantic coverage metrics,
    per-artifact scores, and graph issues — used by the extension for
    inline decorations and the trace graph view.
    """
    mode = request.mode if request.mode else None
    data = orchestrator.get_trace_coverage(mode=mode)
    artifacts = [
        ArtifactCoverageResponse(**a) for a in data.get("artifacts", [])
    ]
    return TraceCoverageResponse(
        mode=data.get("mode", "structural"),
        total_links=data.get("total_links", 0),
        valid_links=data.get("valid_links", 0),
        broken_links_count=data.get("broken_links_count", 0),
        structural_coverage_pct=data.get("structural_coverage_pct", 0.0),
        semantic_coverage_pct=data.get("semantic_coverage_pct"),
        mean_tfidf=data.get("mean_tfidf"),
        mean_jaccard=data.get("mean_jaccard"),
        mean_topic=data.get("mean_topic"),
        mean_combined=data.get("mean_combined"),
        per_requirement_scores=data.get("per_requirement_scores", {}),
        artifacts=artifacts,
        graph_issues=data.get("graph_issues", []),
        has_conflicts=data.get("has_conflicts", False),
        conflict_assessment=data.get("conflict_assessment", ""),
        artifact_counts=data.get("artifact_counts", {}),
    )

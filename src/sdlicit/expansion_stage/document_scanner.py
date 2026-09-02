"""Document scanning, text extraction, and pre-chunking for KB ingestion.

The backend reads project files directly — the CLI never sends file
contents over HTTP.  This module provides the shared logic for finding
ingestible documents, extracting text (including DRM-protected ISO PDFs
via PyMuPDF), stripping boilerplate, and splitting into embedding-safe
chunks.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sdlicit.logging import get_logger

if TYPE_CHECKING:
    from sdlicit.expansion_stage.tools.knowledge_base import KnowledgeBase

_log = get_logger("scanner")

# File types we can extract text from
_EXTENSIONS = {".pdf", ".md", ".txt", ".rst"}

# Directories to skip during recursive scan
_SKIP_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "lightrag_workdir",
}

# ISO licence / boilerplate noise patterns
_ISO_NOISE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"Licensed to",
        r"ISO Store Order",
        r"Single user licence",
        r"© ISO(?:/IEC)?",
        r"All rights reserved",
        r"Price based on \d+ pages",
        r"Copyright protected document",
        r"Published in Switzerland",
    ]
]

# Pre-chunking constants — safe for nomic-embed-text (2048 token context)
_MAX_CHUNK_CHARS = 2000  # ~500 tokens
_OVERLAP_CHARS = 200


@dataclass
class ScannedDocument:
    """A document found during scanning."""

    path: Path
    relative_path: str
    size_bytes: int
    suffix: str


@dataclass
class TextChunk:
    """A text chunk ready for ingestion."""

    text: str
    source_name: str


def _is_noise(text: str) -> bool:
    """Return True if *text* is ISO boilerplate noise."""
    return any(p.search(text) for p in _ISO_NOISE_PATTERNS)


def scan_documents(project_dir: Path) -> list[ScannedDocument]:
    """Find all ingestible documents under the project directory."""
    scan_dirs = [
        project_dir / ".sdlicit" / "knowledge",
        project_dir / "knowledge",
        project_dir,
    ]

    results: list[ScannedDocument] = []
    seen: set[Path] = set()

    for d in scan_dirs:
        if not d.is_dir():
            continue
        for item in sorted(d.rglob("*")):
            if any(part in _SKIP_DIRS for part in item.parts):
                continue
            if not item.is_file() or item.suffix.lower() not in _EXTENSIONS:
                continue
            resolved = item.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                results.append(
                    ScannedDocument(
                        path=item,
                        relative_path=str(item.relative_to(project_dir)),
                        size_bytes=item.stat().st_size,
                        suffix=item.suffix,
                    )
                )
            except (OSError, ValueError) as exc:
                _log.warning("Skipping %s: %s", item, exc)

    return results


def extract_text(path: Path) -> str:
    """Extract plain text from a file (PDF or text-based)."""
    if path.suffix.lower() == ".pdf":
        return _extract_pdf_text(path)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _log.warning("Could not read %s: %s", path.name, exc)
        return ""


def _extract_pdf_text(path: Path) -> str:
    """Extract text from a PDF using PyMuPDF."""
    import fitz  # PyMuPDF

    try:
        doc = fitz.open(str(path))
        pages: list[str] = []
        for page in doc:
            text = page.get_text()
            if isinstance(text, str) and text.strip():
                clean_lines = [
                    ln for ln in text.splitlines() if ln.strip() and not _is_noise(ln)
                ]
                if clean_lines:
                    pages.append("\n".join(clean_lines))
        doc.close()
        return "\n\n".join(pages)
    except Exception as exc:
        _log.warning("Could not extract PDF %s: %s", path.name, exc)
        return ""


def pre_chunk(text: str, source_name: str) -> list[TextChunk]:
    """Split text into overlapping chunks safe for embedding."""
    if len(text) <= _MAX_CHUNK_CHARS:
        return [TextChunk(text=text, source_name=source_name)]

    chunks: list[TextChunk] = []
    start = 0
    idx = 0
    while start < len(text):
        end = start + _MAX_CHUNK_CHARS
        if end < len(text):
            break_at = text.rfind("\n\n", start + _MAX_CHUNK_CHARS // 2, end)
            if break_at == -1:
                break_at = text.rfind("\n", start + _MAX_CHUNK_CHARS // 2, end)
            if break_at > start:
                end = break_at
        chunk = text[start:end].strip()
        if chunk:
            idx += 1
            chunks.append(
                TextChunk(text=chunk, source_name=f"{source_name}#chunk{idx}")
            )
        start = end - _OVERLAP_CHARS if end < len(text) else len(text)
    return chunks


def extract_and_chunk(doc: ScannedDocument) -> list[TextChunk]:
    """Extract text from a document and split into chunks.

    PDFs go through :class:`sdlicit.knowledge.chunkers.ISOChunker`
    for clause-aware splitting (TOC-based with regex fallback).  Other
    file types use the generic char-based :func:`pre_chunk` splitter.

    Source paths are namespaced under ``knowledge/`` so KBRouter store
    filtering (``kb_knowledge_prefix``) recognises them.
    """
    base_path = f"knowledge/{doc.relative_path}"

    if doc.suffix.lower() == ".pdf":
        from sdlicit.helpers.chunkers import ISOChunker

        chunks = ISOChunker().chunk_pdf(doc.path, base_path)
        if chunks:
            return [TextChunk(text=c.text, source_name=c.file_path) for c in chunks]
        # Fall through to generic chunking if ISOChunker returned nothing
        text = extract_text(doc.path)
    else:
        text = extract_text(doc.path)

    if not text.strip():
        return []
    return pre_chunk(text, base_path)


# ---------------------------------------------------------------------------
# Ingestion manifest — tracks per-file ingestion status
# ---------------------------------------------------------------------------

_MANIFEST_FILENAME = ".ingestion_manifest.json"


def file_hash(path: Path) -> str:
    """Compute SHA-256 hash of a file's content."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return h.hexdigest()


class IngestionManifest:
    """Track per-file ingestion status in a JSON manifest.

    Statuses: ``"complete"`` | ``"partial"`` | ``"error"`` | ``"none"``
    (``"none"`` is returned for files not present in the manifest).
    """

    def __init__(self, working_dir: Path) -> None:
        self._path = working_dir / _MANIFEST_FILENAME
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def get_status(self, relative_path: str, current_hash: str) -> str:
        """Return ingestion status for a file.

        Returns ``"none"`` if never ingested or if the file has changed
        since the last ingestion.
        """
        entry = self._data.get(relative_path)
        if entry is None:
            return "none"
        if entry.get("file_hash") != current_hash:
            return "none"  # file changed since last ingestion
        return entry.get("status", "none")

    def mark_partial(self, relative_path: str, fhash: str, total_chunks: int) -> None:
        self._data[relative_path] = {
            "file_hash": fhash,
            "status": "partial",
            "chunks_total": total_chunks,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self._save()

    def mark_complete(self, relative_path: str, fhash: str, total_chunks: int) -> None:
        self._data[relative_path] = {
            "file_hash": fhash,
            "status": "complete",
            "chunks_total": total_chunks,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self._save()

    def mark_error(self, relative_path: str, fhash: str) -> None:
        self._data[relative_path] = {
            "file_hash": fhash,
            "status": "error",
            "chunks_total": 0,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self._save()

    def all_statuses(self, docs: list[ScannedDocument]) -> dict[str, str]:
        """Return ``{relative_path: status}`` for a list of scanned docs."""
        result: dict[str, str] = {}
        for doc in docs:
            fh = file_hash(doc.path)
            result[doc.relative_path] = self.get_status(doc.relative_path, fh)
        return result

    def all_tracked_paths(self) -> list[str]:
        """Return all relative paths currently tracked in the manifest."""
        return list(self._data.keys())

    def remove_entry(self, relative_path: str) -> None:
        """Remove a file entry from the manifest and persist."""
        if relative_path in self._data:
            del self._data[relative_path]
            self._save()


# ---------------------------------------------------------------------------
# Staleness check — remove KB chunks for files no longer on disk
# ---------------------------------------------------------------------------


async def cleanup_stale_documents(
    project_dir: Path,
    kb: KnowledgeBase,
) -> dict[str, int]:
    """Remove LightRAG chunks for files tracked in the manifest that no
    longer exist on disk.

    This prevents stale knowledge from lingering in the KB after a user
    deletes or renames a source file.

    Returns ``{"checked": n, "removed_files": m, "removed_chunks": c}``.
    """
    import asyncio as _aio

    manifest = await _aio.to_thread(IngestionManifest, kb._working_dir)
    tracked = manifest.all_tracked_paths()

    removed_files = 0
    removed_chunks = 0

    for rel_path in tracked:
        source_file = project_dir / rel_path
        if source_file.exists():
            continue

        # File no longer on disk — purge its chunks from LightRAG
        prefix = f"knowledge/{rel_path}"
        n = await kb.delete_by_path_prefix(prefix)
        removed_chunks += n
        removed_files += 1

        # Remove from manifest so future checks skip it
        await _aio.to_thread(manifest.remove_entry, rel_path)

        _log.info(
            "Stale file removed: %s (%d chunk(s) purged)", rel_path, n
        )

    if removed_files:
        _log.info(
            "Staleness check complete: %d file(s), %d chunk(s) removed",
            removed_files,
            removed_chunks,
        )
    else:
        _log.debug(
            "Staleness check: all %d tracked file(s) still present",
            len(tracked),
        )

    return {
        "checked": len(tracked),
        "removed_files": removed_files,
        "removed_chunks": removed_chunks,
    }

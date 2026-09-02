"""Structure-aware chunkers for KB ingestion.

Each artifact type has its own chunking strategy that splits text on
semantically meaningful boundaries (markdown sections, MADR sections,
individual personas/stories, individual requirements, ISO clauses)
instead of LightRAG's default blind token-window split.

Each chunk carries a compound ``file_path`` of the form
``{base_path}#{n}-{slug}`` so KBRouter / graph traversal preserve
section-level provenance.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sdlicit.helpers.parsers import parse_madr_content
from sdlicit.logging import get_logger

_log = get_logger("chunker")


@dataclass(frozen=True)
class Chunk:
    """A single chunk ready for ingestion into LightRAG."""

    text: str
    file_path: str


class Chunker(Protocol):
    """Strategy that splits an artifact's text into structured chunks."""

    def chunk(self, text: str, base_path: str) -> list[Chunk]: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from sdlicit.helpers import slugify as _slugify


def _split_on_h2(text: str) -> list[tuple[str, str]]:
    """Split markdown on ``## `` headings.

    Returns a list of ``(heading, body)`` pairs.  Content before the
    first ``##`` (e.g. title + frontmatter) is returned with heading
    ``"preamble"`` if non-empty.
    """
    sections: list[tuple[str, str]] = []
    current_heading = "preamble"
    current_body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            body = "\n".join(current_body).strip()
            if body:
                sections.append((current_heading, body))
            current_heading = line[3:].strip()
            current_body = []
        else:
            current_body.append(line)
    body = "\n".join(current_body).strip()
    if body:
        sections.append((current_heading, body))
    return sections


def _enumerate_chunks(
    base_path: str,
    parts: Iterable[tuple[str, str]],
    label: str | None = None,
) -> list[Chunk]:
    """Convert ``(heading, body)`` parts into ``Chunk`` objects.

    The optional *label* is prefixed to chunk text for richer LLM
    grounding (e.g. ``[ADR 0001 / Considered Options]\\n...``) but is
    stored only in the body — the file_path encodes the section.
    """
    out: list[Chunk] = []
    for idx, (heading, body) in enumerate(parts, start=1):
        if not body.strip():
            continue
        slug = _slugify(heading)
        anchor = f"{idx}-{slug}"
        prefix = f"[{label} / {heading}]\n" if label else f"## {heading}\n"
        out.append(Chunk(text=prefix + body, file_path=f"{base_path}#{anchor}"))
    return out


# ---------------------------------------------------------------------------
# Generic markdown-section chunker (used by SOW)
# ---------------------------------------------------------------------------


class MarkdownSectionChunker:
    """One chunk per ``##`` heading."""

    def __init__(self, label: str | None = None) -> None:
        self._label = label

    def chunk(self, text: str, base_path: str) -> list[Chunk]:
        parts = _split_on_h2(text)
        if not parts:
            return [Chunk(text=text, file_path=base_path)]
        return _enumerate_chunks(base_path, parts, label=self._label)


# ---------------------------------------------------------------------------
# SOW
# ---------------------------------------------------------------------------


class SOWChunker:
    """Chunk a SOW document by top-level markdown sections."""

    def chunk(self, text: str, base_path: str) -> list[Chunk]:
        return MarkdownSectionChunker(label="SOW").chunk(text, base_path)


# ---------------------------------------------------------------------------
# ADR (MADR-aware)
# ---------------------------------------------------------------------------


class ADRChunker:
    """Chunk an ADR document by MADR sections.

    Uses :func:`sdlicit.helpers.parsers.parse_madr_content` for parsing.
    Emits one chunk per H2 section and an additional ``meta`` chunk
    containing title + status + date + traceability edges so the graph
    can connect ADRs via their identifiers and relationships.
    """

    def chunk(self, text: str, base_path: str) -> list[Chunk]:
        parsed = parse_madr_content(text)
        title = parsed.get("title", "")
        status = parsed.get("status", "")
        date = parsed.get("date", "")
        adr_id = parsed.get("id", "") or Path(base_path).stem
        supersedes = parsed.get("supersedes", "")
        implements = parsed.get("implements", []) or []
        tested_by = parsed.get("tested_by", []) or []
        sections: dict[str, list[str]] = parsed.get("sections", {}) or {}

        out: list[Chunk] = []

        # Meta chunk with explicit relationship statements for graph extraction
        meta_lines = [f"# {title}" if title else f"# {adr_id}"]
        meta_lines.append(f"ID: {adr_id}")
        if status:
            meta_lines.append(f"Status: {status}")
        if date:
            meta_lines.append(f"Date: {date}")
        if supersedes:
            meta_lines.append(f"{adr_id} SUPERSEDES {supersedes}")
        if implements:
            for req_id in implements:
                meta_lines.append(f"{adr_id} IMPLEMENTS {req_id}")
        if tested_by:
            for bdd_id in tested_by:
                meta_lines.append(f"{adr_id} TESTED_BY {bdd_id}")
        meta = "\n".join(meta_lines).strip()
        if meta:
            out.append(Chunk(text=meta, file_path=f"{base_path}#0-meta"))

        idx = 1
        for heading, body_lines in sections.items():
            body = "\n".join(body_lines).strip()
            if not body:
                continue
            slug = _slugify(heading)
            anchor = f"{idx}-{slug}"
            prefix = f"[ADR {adr_id} / {heading}]\n"
            out.append(Chunk(text=prefix + body, file_path=f"{base_path}#{anchor}"))
            idx += 1

        if not out:
            return [Chunk(text=text, file_path=base_path)]
        return out


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------


class PersonaChunker:
    """One chunk per persona.

    Splits on ``## `` headings (each persona is expected to begin with
    its name as an H2).  Falls back to whole-document if no H2 found.
    """

    def chunk(self, text: str, base_path: str) -> list[Chunk]:
        parts = _split_on_h2(text)
        # Drop preamble if it has no real persona content
        parts = [(h, b) for h, b in parts if h != "preamble" or b.strip()]
        if not parts:
            return [Chunk(text=text, file_path=base_path)]
        return _enumerate_chunks(base_path, parts, label="Persona")


# ---------------------------------------------------------------------------
# User stories
# ---------------------------------------------------------------------------

_STORY_HEADING_RE = re.compile(r"^(##+)\s+(.*?US[\s-]?\d+.*?)$", re.IGNORECASE)


class StoryChunker:
    """One chunk per user story.

    Recognises headings containing ``US-N`` / ``US N`` patterns (any
    heading depth).  Falls back to ``##`` splitting if no story-id
    headings are found.
    """

    def chunk(self, text: str, base_path: str) -> list[Chunk]:
        lines = text.splitlines()
        # First pass: try to find story-id headings
        story_indices = [i for i, ln in enumerate(lines) if _STORY_HEADING_RE.match(ln)]
        if not story_indices:
            return MarkdownSectionChunker(label="Story").chunk(text, base_path)

        out: list[Chunk] = []
        story_indices.append(len(lines))  # sentinel
        for idx, start in enumerate(story_indices[:-1], start=1):
            end = story_indices[idx]
            heading_match = _STORY_HEADING_RE.match(lines[start])
            heading = (
                heading_match.group(2).strip() if heading_match else f"story-{idx}"
            )
            body = "\n".join(lines[start:end]).strip()
            if not body:
                continue
            slug = _slugify(heading)
            out.append(
                Chunk(
                    text=f"[Story / {heading}]\n{body}",
                    file_path=f"{base_path}#{idx}-{slug}",
                )
            )
        return out


# ---------------------------------------------------------------------------
# SRS
# ---------------------------------------------------------------------------

# Matches Sdlicit's own format: "REQ-AUTH-01", "REQ-PERF-03.1"
_REQ_ID_RE = re.compile(
    r"\b(REQ-[A-Z]+-\d+(?:\.\d+)?)\b",
)

# Legacy fallback for imported SRS documents: "FR-1", "NFR-3.2"
_REQ_ID_RE_LEGACY = re.compile(
    r"\b((?:FR|NFR|CR|IR|PR|SR)[\s-]?\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)


class SRSChunker:
    """One chunk per individual requirement.

    Scans for requirement identifiers (FR-N, NFR-N, CR-N, ...) and
    emits one chunk per requirement.  The enclosing H2 group heading
    (e.g. "Functional Requirements") is preserved as a context prefix
    in the chunk text and reflected in the slug.

    If no requirement ids are detected, falls back to per-section
    chunking.
    """

    def chunk(self, text: str, base_path: str) -> list[Chunk]:
        sections = _split_on_h2(text)
        if not sections:
            return [Chunk(text=text, file_path=base_path)]

        out: list[Chunk] = []
        global_idx = 1
        any_req = False

        for group_heading, body in sections:
            req_chunks = self._chunk_requirements(group_heading, body)
            if req_chunks:
                any_req = True
                for heading, req_text in req_chunks:
                    slug = _slugify(heading)
                    out.append(
                        Chunk(
                            text=f"[SRS / {group_heading} / {heading}]\n{req_text}",
                            file_path=f"{base_path}#{global_idx}-{slug}",
                        )
                    )
                    global_idx += 1
            else:
                # Group has no requirement ids — keep it as a single chunk
                slug = _slugify(group_heading)
                out.append(
                    Chunk(
                        text=f"[SRS / {group_heading}]\n{body}",
                        file_path=f"{base_path}#{global_idx}-{slug}",
                    )
                )
                global_idx += 1

        if not any_req:
            # Fell through to plain section chunks — that's fine
            return out
        return out

    @staticmethod
    def _chunk_requirements(group_heading: str, body: str) -> list[tuple[str, str]]:
        """Split *body* into ``(req_heading, req_text)`` pairs.

        Returns ``[]`` if no requirement ids are detected.
        """
        lines = body.splitlines()
        # Find indices of lines that introduce a new requirement
        req_starts: list[tuple[int, str]] = []
        for i, ln in enumerate(lines):
            m = _REQ_ID_RE.search(ln) or _REQ_ID_RE_LEGACY.search(ln)
            if not m:
                continue
            # Heuristic: line must look like a requirement starter
            stripped = ln.lstrip("-*# \t")
            # Handle bracket-ID format: [REQ-ID] statement
            if stripped.startswith("["):
                stripped = stripped.lstrip("[")
            if stripped.lower().startswith(m.group(1).lower()) or ln.startswith("#"):
                heading_text = stripped.split("\n")[0]
                req_starts.append((i, heading_text))

        if not req_starts:
            return []

        out: list[tuple[str, str]] = []
        req_starts.append((len(lines), ""))  # sentinel
        for idx, (start, heading) in enumerate(req_starts[:-1]):
            end = req_starts[idx + 1][0]
            req_text = "\n".join(lines[start:end]).strip()
            if req_text:
                out.append((heading, req_text))
        return out


# ---------------------------------------------------------------------------
# ISO PDFs (clause-aware)
# ---------------------------------------------------------------------------

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

_CLAUSE_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+(\S.*)$")


def _strip_iso_noise(text: str) -> str:
    return "\n".join(
        ln
        for ln in text.splitlines()
        if ln.strip() and not any(p.search(ln) for p in _ISO_NOISE_PATTERNS)
    )


class ISOChunker:
    """Clause-aware chunker for ISO PDFs.

    Strategy:
      1. Try PyMuPDF's ``doc.get_toc()`` — split by clause page ranges.
      2. Fall back to regex on extracted text matching ``N.M.K Title``.

    Only used by :mod:`sdlicit.expansion_stage.document_scanner`; the
    PDF path is passed via :meth:`chunk_pdf` because the strategy needs
    structural metadata (TOC) that's lost once text is extracted.
    """

    def chunk(self, text: str, base_path: str) -> list[Chunk]:
        """Plain-text fallback (no PDF available)."""
        cleaned = _strip_iso_noise(text)
        return self._chunk_by_regex(cleaned, base_path)

    def chunk_pdf(self, pdf_path: Path, base_path: str) -> list[Chunk]:
        """Chunk an ISO PDF by clause structure.

        Tries TOC first, falls back to regex on full text.
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            _log.warning("PyMuPDF not available — cannot chunk %s", pdf_path)
            return []

        try:
            doc = fitz.open(str(pdf_path))
        except Exception as exc:
            _log.warning("Could not open PDF %s: %s", pdf_path, exc)
            return []

        try:
            toc_chunks = self._chunk_by_toc(doc, base_path)
            if toc_chunks:
                return toc_chunks

            # Fallback: extract all text and split by regex
            full_text = self._extract_all_text(doc)
            return self._chunk_by_regex(full_text, base_path)
        finally:
            doc.close()

    def _chunk_by_toc(self, doc, base_path: str) -> list[Chunk]:
        """Split using PyMuPDF's outline.

        Each leaf TOC entry whose title starts with a clause number
        (e.g. ``5.3.4 Portability``) becomes one chunk covering its
        page range.
        """
        toc = doc.get_toc()  # [[level, title, page1based], ...]
        if not toc:
            return []

        # Filter to clause-numbered entries only
        entries: list[tuple[int, str, str, int]] = []
        for level, title, page in toc:
            m = _CLAUSE_RE.match(title.strip())
            if not m:
                continue
            entries.append((level, m.group(1), m.group(2), max(1, page) - 1))

        if not entries:
            return []

        out: list[Chunk] = []
        for i, (_level, clause, heading, start_page) in enumerate(entries):
            end_page = entries[i + 1][3] if i + 1 < len(entries) else doc.page_count
            text_parts: list[str] = []
            for p in range(start_page, max(start_page + 1, end_page)):
                try:
                    page_text = doc[p].get_text()
                except Exception:
                    continue
                if page_text and isinstance(page_text, str):
                    text_parts.append(page_text)
            body = _strip_iso_noise("\n".join(text_parts)).strip()
            if not body:
                continue
            anchor = f"{clause.replace('.', '-')}-{_slugify(heading)}"
            prefix = f"[ISO {clause} {heading}]\nThis section defines {heading} (clause {clause}). The following concepts and terms are part of {heading}:\n"
            out.append(Chunk(text=prefix + body, file_path=f"{base_path}#{anchor}"))
        return out

    @staticmethod
    def _extract_all_text(doc) -> str:
        parts: list[str] = []
        for page in doc:
            try:
                t = page.get_text()
            except Exception:
                continue
            if t and isinstance(t, str):
                parts.append(t)
        return "\n".join(parts)

    @staticmethod
    def _chunk_by_regex(text: str, base_path: str) -> list[Chunk]:
        """Split text on lines matching ``N.M(.K...) Title``."""
        lines = text.splitlines()
        starts: list[tuple[int, str, str]] = []
        for i, ln in enumerate(lines):
            m = _CLAUSE_RE.match(ln.strip())
            if m:
                starts.append((i, m.group(1), m.group(2)))

        if not starts:
            # No clauses detected — emit one big chunk
            cleaned = _strip_iso_noise(text).strip()
            if not cleaned:
                return []
            return [Chunk(text=cleaned, file_path=base_path)]

        starts.append((len(lines), "", ""))  # sentinel
        out: list[Chunk] = []
        for idx, (start, clause, heading) in enumerate(starts[:-1]):
            end = starts[idx + 1][0]
            body = "\n".join(lines[start:end]).strip()
            body = _strip_iso_noise(body).strip()
            if not body:
                continue
            anchor = f"{clause.replace('.', '-')}-{_slugify(heading)}"
            prefix = f"[ISO {clause} {heading}]\nThis section defines {heading} (clause {clause}). The following concepts and terms are part of {heading}:\n"
            out.append(
                Chunk(
                    text=prefix + body,
                    file_path=f"{base_path}#{anchor}",
                )
            )
        return out


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Chunker] = {
    "sow": SOWChunker(),
    "adr": ADRChunker(),
    "brief": MarkdownSectionChunker(label="Brief"),
    "personas": PersonaChunker(),
    "persona": PersonaChunker(),
    "stories": StoryChunker(),
    "story": StoryChunker(),
    "srs": SRSChunker(),
    "iso": ISOChunker(),
}


def get_chunker(artifact_type: str) -> Chunker:
    """Return the chunker for *artifact_type*.

    Falls back to a generic markdown-section chunker for unknown types.
    """
    key = artifact_type.lower().strip()
    if key in _REGISTRY:
        return _REGISTRY[key]
    _log.debug("No chunker registered for %r — using generic markdown", key)
    return MarkdownSectionChunker(label=artifact_type)

"""Canonical artifact naming and path resolution.

The backend is the single source of truth for artifact filenames and
their relative location within the ``.sdlicit/artifacts/`` tree.
Frontends (CLI, VS Code extension) receive an ``ArtifactMeta`` with
every generation response and must use it for saving.

Folder layout::

    .sdlicit/artifacts/
        sow.md                           # Single file, overwritten
        srs.md                           # Single file, overwritten
        personas.md                      # Single file, overwritten
        stories.md                       # Single file, overwritten
        adr/
            ADR-0001-<slug>.md           # Sequential
            ADR-0002-<slug>.md
        bdd/
            <persona-slug>.feature       # One per persona
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from sdlicit.helpers import slugify as _slugify_base

# ── Supported artifact types ───────────────────────────────────────────────

ArtifactType = Literal["sow", "srs", "adr", "personas", "stories", "bdd"]

# ── Subfolder mapping ──────────────────────────────────────────────────────

_SUBFOLDERS: dict[ArtifactType, str | None] = {
    "sow": None,  # lives directly in artifacts/
    "srs": None,
    "personas": None,
    "stories": None,
    "adr": "adr",
    "bdd": "bdd",
}

# ── ArtifactMeta — returned in every response ─────────────────────────────


class ArtifactMeta(BaseModel):
    """Metadata the frontend uses to persist an artifact to disk.

    ``relative_path`` is relative to ``.sdlicit/artifacts/``.
    """

    tag: str = Field(description="Artifact tag e.g. 'ADR-0001', 'SOW', 'SRS'")
    filename: str = Field(description="Canonical filename e.g. 'ADR-0001-use-react.md'")
    relative_path: str = Field(
        description="Path relative to .sdlicit/artifacts/, e.g. 'adr/ADR-0001-use-react.md'"
    )
    artifact_type: ArtifactType


# ── Slug helpers ───────────────────────────────────────────────────────────

def _slugify(text: str, max_len: int = 40) -> str:
    """Convert arbitrary text to a filesystem-safe slug."""
    return _slugify_base(text, max_len=max_len, fallback="untitled")


def _extract_title_from_markdown(content: str) -> str:
    """Extract the first H1 heading from markdown content."""
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


# ── ADR naming (sequential) ───────────────────────────────────────────────


def _next_adr_number(project_dir: Path) -> int:
    """Scan .sdlicit/artifacts/adr/ and return the next sequential number."""
    adr_path = project_dir / ".sdlicit" / "artifacts" / "adr"
    if not adr_path.is_dir():
        return 1
    existing = sorted(adr_path.glob("ADR-*.md"))
    max_num = 0
    for f in existing:
        match = re.match(r"ADR-(\d+)", f.name)
        if match:
            max_num = max(max_num, int(match.group(1)))
    return max_num + 1


def resolve_adr_meta(project_dir: Path, title: str) -> ArtifactMeta:
    """Determine filename and path for a new ADR."""
    num = _next_adr_number(project_dir)
    tag = f"ADR-{num:04d}"
    slug = _slugify(title)
    filename = f"{tag}-{slug}.md"
    return ArtifactMeta(
        tag=tag,
        filename=filename,
        relative_path=f"adr/{filename}",
        artifact_type="adr",
    )


# ── SOW naming (single file) ──────────────────────────────────────────────


def resolve_sow_meta() -> ArtifactMeta:
    """SOW is always a single file."""
    return ArtifactMeta(
        tag="SOW",
        filename="sow.md",
        relative_path="sow.md",
        artifact_type="sow",
    )


# ── SRS naming (single file) ──────────────────────────────────────────────


def resolve_srs_meta() -> ArtifactMeta:
    """SRS is always a single file."""
    return ArtifactMeta(
        tag="SRS",
        filename="srs.md",
        relative_path="srs.md",
        artifact_type="srs",
    )


# ── Personas naming (single file) ─────────────────────────────────────────


def resolve_personas_meta() -> ArtifactMeta:
    """Personas is always a single file."""
    return ArtifactMeta(
        tag="PERSONAS",
        filename="personas.md",
        relative_path="personas.md",
        artifact_type="personas",
    )


# ── Stories naming (single file) ──────────────────────────────────────────


def resolve_stories_meta() -> ArtifactMeta:
    """Stories is always a single file."""
    return ArtifactMeta(
        tag="STORIES",
        filename="stories.md",
        relative_path="stories.md",
        artifact_type="stories",
    )


# ── BDD naming (per persona) ──────────────────────────────────────────────


def resolve_bdd_meta(persona_name: str) -> ArtifactMeta:
    """BDD file per persona."""
    slug = _slugify(persona_name)
    filename = f"{slug}.feature"
    return ArtifactMeta(
        tag=f"BDD-{slug}",
        filename=filename,
        relative_path=f"bdd/{filename}",
        artifact_type="bdd",
    )


# ── Generic resolver ───────────────────────────────────────────────────────


def resolve_artifact_meta(
    artifact_type: ArtifactType,
    project_dir: Path | None = None,
    title: str = "",
    persona_name: str = "",
) -> ArtifactMeta:
    """Resolve the canonical filename and path for any artifact type.

    Parameters
    ----------
    artifact_type : one of 'sow', 'srs', 'adr', 'personas', 'stories', 'bdd'
    project_dir : required for 'adr' (to determine sequential number)
    title : used for 'adr' slug
    persona_name : used for 'bdd' slug
    """
    if artifact_type == "sow":
        return resolve_sow_meta()
    elif artifact_type == "srs":
        return resolve_srs_meta()
    elif artifact_type == "personas":
        return resolve_personas_meta()
    elif artifact_type == "stories":
        return resolve_stories_meta()
    elif artifact_type == "adr":
        if project_dir is None:
            raise ValueError("project_dir is required for ADR naming")
        return resolve_adr_meta(project_dir, title)
    elif artifact_type == "bdd":
        return resolve_bdd_meta(persona_name or "scenarios")
    else:
        raise ValueError(f"Unknown artifact type: {artifact_type}")

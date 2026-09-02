"""Canonical artifact store — single source of truth for read/write.

All frontends (extension, CLI, pipeline/benchmark) must use this module
to persist and retrieve artifacts.

Format rules (architectural decision — enforced by this store):
  - SOW, SRS, ADR: Always markdown (.md) only. Never stored as JSON.
  - Personas, Stories, BDD: JSON (.json) is the primary format.
    Markdown export is optional (for human reading / KB ingestion).

Directory layout::

    .sdlicit/artifacts/
        sow.md
        srs.md
        personas.json
        personas.md          (optional export)
        stories.json
        stories.md           (optional export)
        adr/
            ADR-0001-<slug>.md
        bdd/
            <persona-slug>.json
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from sdlicit.helpers.artifact_naming import (
    ArtifactMeta,
    ArtifactType,
    resolve_adr_meta,
    resolve_bdd_meta,
    resolve_personas_meta,
    resolve_sow_meta,
    resolve_srs_meta,
    resolve_stories_meta,
)
from sdlicit.logging import get_logger

_log = get_logger("artifact_store")

# ═══════════════════════════════════════════════════════════════════════════
# Structured artifact models (JSON schemas)
# ═══════════════════════════════════════════════════════════════════════════


class SOWArtifact(BaseModel):
    """Structured Statement of Work."""

    artifact_type: Literal["sow"] = "sow"
    title: str = ""
    problem_statement: str = ""
    stakeholders: list[dict[str, Any]] = Field(default_factory=list)
    constraints: str = ""
    out_of_scope: str = ""
    open_questions: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    raw_markdown: str = Field(default="", description="Full markdown content if parsed from legacy .md")


class Requirement(BaseModel):
    """A single requirement within the SRS."""

    req_id: str = ""
    category: Literal["functional", "non-functional"] = "functional"
    statement: str = ""
    rationale: str = ""
    acceptance: str = ""


class SRSArtifact(BaseModel):
    """Structured Software Requirements Specification."""

    artifact_type: Literal["srs"] = "srs"
    introduction: str = ""
    scope: str = ""
    requirements: list[Requirement] = Field(default_factory=list)
    raw_markdown: str = ""


class PersonaItem(BaseModel):
    """A single persona."""

    persona_id: str = ""
    name: str = ""
    role: str = ""
    goals: list[str] = Field(default_factory=list)
    frustrations: list[str] = Field(default_factory=list)
    description: str = ""


class PersonasArtifact(BaseModel):
    """Structured personas collection."""

    artifact_type: Literal["personas"] = "personas"
    personas: list[PersonaItem] = Field(default_factory=list)
    raw_markdown: str = ""


class UserStoryItem(BaseModel):
    """A single user story."""

    story_id: str = ""
    persona_id: str = ""
    requirement_ids: list[str] = Field(default_factory=list)
    statement: str = ""


class StoriesArtifact(BaseModel):
    """Structured user stories collection."""

    artifact_type: Literal["stories"] = "stories"
    stories: list[UserStoryItem] = Field(default_factory=list)
    raw_markdown: str = ""


class ADRField(BaseModel):
    """A single field within an ADR."""

    key: str
    content: str = ""


class ADRArtifact(BaseModel):
    """Structured Architecture Decision Record."""

    artifact_type: Literal["adr"] = "adr"
    adr_id: str = ""
    title: str = ""
    status: str = "proposed"
    date: str = Field(default_factory=lambda: date.today().isoformat())
    context: str = ""
    decision: str = ""
    alternatives: str = ""
    rationale: str = ""
    consequences: str = ""
    implements: list[str] = Field(default_factory=list)
    supersedes: str = ""
    references: list[str] = Field(default_factory=list)


class BDDScenario(BaseModel):
    """A single BDD scenario."""

    scenario_id: str = ""
    story_id: str = ""
    adr_id: str = ""
    feature: str = ""
    gherkin: str = ""


class BDDArtifact(BaseModel):
    """Structured BDD scenarios for a persona."""

    artifact_type: Literal["bdd"] = "bdd"
    persona_slug: str = ""
    scenarios: list[BDDScenario] = Field(default_factory=list)
    raw_gherkin: str = ""


# Union type for all artifacts
ArtifactData = SOWArtifact | SRSArtifact | PersonasArtifact | StoriesArtifact | ADRArtifact | BDDArtifact


# ═══════════════════════════════════════════════════════════════════════════
# Store class — read/write/render
# ═══════════════════════════════════════════════════════════════════════════


class ArtifactStore:
    """Filesystem-backed artifact store with enforced format rules.

    Format rules (architectural decision):
      - SOW, SRS, ADR: Always markdown only (never JSON).
      - Personas, Stories, BDD: JSON is primary format; markdown is optional export.

    All consumers (extension, pipeline, replay, benchmark) MUST use this store.
    """

    # Types that are always stored as markdown (never JSON)
    MARKDOWN_ONLY_TYPES: set[str] = {"sow", "srs", "adr"}
    # Types that use JSON as primary format
    JSON_PRIMARY_TYPES: set[str] = {"personas", "stories", "bdd"}

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.artifacts_dir = project_dir / ".sdlicit" / "artifacts"

    def _ensure_dir(self, filepath: Path) -> None:
        filepath.parent.mkdir(parents=True, exist_ok=True)

    def _artifact_type_str(self, data: ArtifactData) -> str:
        """Get the artifact_type string from a model instance."""
        return getattr(data, "artifact_type", "unknown")

    # ── Write ──────────────────────────────────────────────────────────────

    def save(self, data: ArtifactData, meta: ArtifactMeta | None = None) -> Path:
        """Save artifact in its canonical format.

        - SOW/SRS/ADR → saved as markdown (never JSON).
        - Personas/Stories/BDD → saved as JSON (primary format).
        """
        if meta is None:
            meta = self._resolve_meta(data)

        atype = self._artifact_type_str(data)

        if atype in self.MARKDOWN_ONLY_TYPES:
            # Markdown-only types: render and save as .md
            return self.save_markdown(data, meta)

        # JSON-primary types: save as .json
        json_relative = re.sub(r"\.(md|feature)$", ".json", meta.relative_path)
        filepath = self.artifacts_dir / json_relative
        self._ensure_dir(filepath)

        content = data.model_dump(mode="json")
        filepath.write_text(
            json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _log.info("Saved artifact: %s → %s", meta.tag, filepath.relative_to(self.project_dir))
        return filepath

    def save_markdown(self, data: ArtifactData, meta: ArtifactMeta | None = None) -> Path:
        """Save artifact as markdown (for export/display). Returns the file path."""
        if meta is None:
            meta = self._resolve_meta(data)

        filepath = self.artifacts_dir / meta.relative_path
        self._ensure_dir(filepath)

        md = self.render_markdown(data)
        filepath.write_text(md, encoding="utf-8")
        return filepath

    def save_both(self, data: ArtifactData, meta: ArtifactMeta | None = None) -> tuple[Path, Path]:
        """Save artifact in canonical format + optional secondary format.

        - SOW/SRS/ADR: saves only markdown (returns same path twice).
        - Personas/Stories/BDD: saves JSON (primary) + markdown (for human reading).
        """
        if meta is None:
            meta = self._resolve_meta(data)

        atype = self._artifact_type_str(data)

        if atype in self.MARKDOWN_ONLY_TYPES:
            # Markdown-only: just save .md
            md_path = self.save_markdown(data, meta)
            return md_path, md_path

        # JSON-primary: save both
        json_path = self.save(data, meta)
        md_path = self.save_markdown(data, meta)
        return json_path, md_path

    # ── Read ───────────────────────────────────────────────────────────────

    def load(self, artifact_type: ArtifactType, filename: str | None = None) -> ArtifactData | None:
        """Load an artifact by type. For single-file types, filename is optional.

        Respects format rules:
        - SOW/SRS/ADR: reads .md (canonical), falls back to .json (legacy).
        - Personas/Stories/BDD: reads .json (canonical), falls back to .md (legacy).
        """
        if artifact_type in self.MARKDOWN_ONLY_TYPES:
            # Markdown-first for SOW/SRS/ADR
            md_path = self._resolve_read_path(artifact_type, filename, ext=".md")
            if md_path and md_path.exists():
                return self._load_legacy_md(artifact_type, md_path)
            # Fallback to JSON (legacy or migration)
            json_path = self._resolve_read_path(artifact_type, filename, ext=".json")
            if json_path and json_path.exists():
                return self._load_json(artifact_type, json_path)
        else:
            # JSON-first for Personas/Stories/BDD
            json_path = self._resolve_read_path(artifact_type, filename, ext=".json")
            if json_path and json_path.exists():
                return self._load_json(artifact_type, json_path)
            # Fallback to markdown (legacy)
            md_path = self._resolve_read_path(artifact_type, filename, ext=".md")
            if md_path and md_path.exists():
                return self._load_legacy_md(artifact_type, md_path)

        return None

    def load_all(self, artifact_type: ArtifactType) -> list[ArtifactData]:
        """Load all artifacts of a given type (for adr/bdd with multiple files)."""
        results: list[ArtifactData] = []
        if artifact_type == "adr":
            adr_path = self.artifacts_dir / "adr"
            if adr_path.is_dir():
                for f in sorted(adr_path.iterdir()):
                    if f.suffix == ".json":
                        loaded = self._load_json("adr", f)
                        if loaded:
                            results.append(loaded)
                    elif f.suffix == ".md":
                        loaded = self._load_legacy_md("adr", f)
                        if loaded:
                            results.append(loaded)
        elif artifact_type == "bdd":
            bdd_path = self.artifacts_dir / "bdd"
            if bdd_path.is_dir():
                for f in sorted(bdd_path.iterdir()):
                    if f.suffix == ".json":
                        loaded = self._load_json("bdd", f)
                        if loaded:
                            results.append(loaded)
                    elif f.suffix == ".feature":
                        loaded = self._load_legacy_md("bdd", f)
                        if loaded:
                            results.append(loaded)
        else:
            single = self.load(artifact_type)
            if single:
                results.append(single)
        return results

    def list_artifacts(self) -> list[dict[str, str]]:
        """List all artifact files with type and path information."""
        results: list[dict[str, str]] = []
        if not self.artifacts_dir.is_dir():
            return results

        for f in sorted(self.artifacts_dir.rglob("*")):
            if f.is_file() and f.suffix in (".json", ".md", ".feature"):
                rel = f.relative_to(self.artifacts_dir)
                atype = self._infer_type_from_path(str(rel))
                results.append({
                    "type": atype,
                    "filename": f.name,
                    "relative_path": str(rel),
                    "format": "json" if f.suffix == ".json" else "markdown",
                })
        return results

    # ── Markdown rendering ─────────────────────────────────────────────────

    def render_markdown(self, data: ArtifactData) -> str:
        """Render structured artifact data as canonical markdown."""
        if isinstance(data, SOWArtifact):
            return self._render_sow_md(data)
        elif isinstance(data, SRSArtifact):
            return self._render_srs_md(data)
        elif isinstance(data, PersonasArtifact):
            return self._render_personas_md(data)
        elif isinstance(data, StoriesArtifact):
            return self._render_stories_md(data)
        elif isinstance(data, ADRArtifact):
            return self._render_adr_md(data)
        elif isinstance(data, BDDArtifact):
            return self._render_bdd_md(data)
        return ""

    # ── Private helpers ────────────────────────────────────────────────────

    def _resolve_meta(self, data: ArtifactData) -> ArtifactMeta:
        """Resolve ArtifactMeta from data."""
        if isinstance(data, SOWArtifact):
            return resolve_sow_meta()
        elif isinstance(data, SRSArtifact):
            return resolve_srs_meta()
        elif isinstance(data, PersonasArtifact):
            return resolve_personas_meta()
        elif isinstance(data, StoriesArtifact):
            return resolve_stories_meta()
        elif isinstance(data, ADRArtifact):
            return resolve_adr_meta(self.project_dir, data.title)
        elif isinstance(data, BDDArtifact):
            return resolve_bdd_meta(data.persona_slug)
        raise ValueError(f"Unknown artifact type: {type(data)}")

    def _resolve_read_path(self, artifact_type: ArtifactType, filename: str | None, ext: str) -> Path | None:
        """Resolve the file path for reading an artifact."""
        if artifact_type == "sow":
            return self.artifacts_dir / f"sow{ext}"
        elif artifact_type == "srs":
            return self.artifacts_dir / f"srs{ext}"
        elif artifact_type == "personas":
            return self.artifacts_dir / f"personas{ext}"
        elif artifact_type == "stories":
            return self.artifacts_dir / f"stories{ext}"
        elif artifact_type == "adr":
            if filename:
                base = re.sub(r"\.(md|json)$", "", filename)
                return self.artifacts_dir / "adr" / f"{base}{ext}"
            # Return the latest ADR
            adr_path = self.artifacts_dir / "adr"
            if adr_path.is_dir():
                files = sorted(adr_path.glob(f"*{ext}"))
                return files[-1] if files else None
            return None
        elif artifact_type == "bdd":
            if filename:
                base = re.sub(r"\.(feature|json)$", "", filename)
                return self.artifacts_dir / "bdd" / f"{base}{ext}"
            return None
        return None

    def _load_json(self, artifact_type: ArtifactType, filepath: Path) -> ArtifactData | None:
        """Load artifact from JSON file."""
        try:
            raw = json.loads(filepath.read_text(encoding="utf-8"))
            return self._parse_json(artifact_type, raw)
        except (json.JSONDecodeError, OSError) as e:
            _log.warning("Failed to load %s: %s", filepath, e)
            return None

    def _parse_json(self, artifact_type: ArtifactType, raw: dict) -> ArtifactData | None:
        """Parse raw JSON dict into typed artifact model."""
        type_map: dict[str, type[ArtifactData]] = {
            "sow": SOWArtifact,
            "srs": SRSArtifact,
            "personas": PersonasArtifact,
            "stories": StoriesArtifact,
            "adr": ADRArtifact,
            "bdd": BDDArtifact,
        }
        cls = type_map.get(artifact_type)
        if cls is None:
            return None
        try:
            return cls.model_validate(raw)
        except Exception as e:
            _log.warning("Failed to parse %s artifact: %s", artifact_type, e)
            return None

    def _load_legacy_md(self, artifact_type: ArtifactType, filepath: Path) -> ArtifactData | None:
        """Load legacy markdown/feature file and parse into structured data."""
        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError:
            return None

        if artifact_type == "sow":
            return SOWArtifact(raw_markdown=content, title=self._extract_h1(content))
        elif artifact_type == "srs":
            reqs = self._parse_srs_requirements(content)
            return SRSArtifact(requirements=reqs, raw_markdown=content)
        elif artifact_type == "personas":
            personas = self._parse_personas_md(content)
            return PersonasArtifact(personas=personas, raw_markdown=content)
        elif artifact_type == "stories":
            stories = self._parse_stories_md(content)
            return StoriesArtifact(stories=stories, raw_markdown=content)
        elif artifact_type == "adr":
            return self._parse_adr_md(content)
        elif artifact_type == "bdd":
            return BDDArtifact(raw_gherkin=content, persona_slug=filepath.stem)
        return None

    def _infer_type_from_path(self, relative_path: str) -> str:
        """Infer artifact type from its relative path."""
        lower = relative_path.lower()
        if lower.startswith("adr/"):
            return "adr"
        if lower.startswith("bdd/"):
            return "bdd"
        if "sow" in lower:
            return "sow"
        if "srs" in lower:
            return "srs"
        if "persona" in lower:
            return "personas"
        if "stor" in lower:
            return "stories"
        return "unknown"

    # ── Legacy markdown parsers ────────────────────────────────────────────

    @staticmethod
    def _extract_h1(content: str) -> str:
        for line in content.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return ""

    @staticmethod
    def _parse_srs_requirements(content: str) -> list[Requirement]:
        """Parse SRS markdown into structured requirements."""
        reqs: list[Requirement] = []
        category: Literal["functional", "non-functional"] = "functional"
        lines = content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if "non-functional" in line.lower() and line.startswith("#"):
                category = "non-functional"
            elif "functional" in line.lower() and line.startswith("#"):
                category = "functional"

            # Match requirement lines like "- [REQ-XXX-NN] statement"
            m = re.match(r"^[-*]\s+\[(REQ-[A-Z0-9]+-\d+)\]\s+(.*)", line)
            if m:
                req_id = m.group(1)
                statement = m.group(2).strip()
                rationale = ""
                acceptance = ""
                # Look for indented details
                j = i + 1
                while j < len(lines) and lines[j].startswith("  "):
                    detail = lines[j].strip()
                    if detail.startswith("_Rationale:_") or detail.startswith("*Rationale:*"):
                        rationale = re.sub(r"^[_*]Rationale:[_*]\s*", "", detail)
                    elif detail.startswith("_Acceptance:_") or detail.startswith("*Acceptance:*"):
                        acceptance = re.sub(r"^[_*]Acceptance:[_*]\s*", "", detail)
                    j += 1
                reqs.append(Requirement(
                    req_id=req_id, category=category,
                    statement=statement, rationale=rationale, acceptance=acceptance,
                ))
                i = j
                continue
            i += 1
        return reqs

    @staticmethod
    def _parse_personas_md(content: str) -> list[PersonaItem]:
        """Parse personas markdown into structured items."""
        personas: list[PersonaItem] = []
        sections = re.split(r"^##\s+", content, flags=re.MULTILINE)
        for section in sections[1:]:  # skip preamble
            lines = section.strip().splitlines()
            if not lines:
                continue
            name = lines[0].strip()
            body = "\n".join(lines[1:])
            persona = PersonaItem(name=name, description=body.strip())
            # Try to extract persona_id
            id_match = re.search(r"PERSONA-\d+|P-\d+", section)
            if id_match:
                persona.persona_id = id_match.group()
            personas.append(persona)
        return personas

    @staticmethod
    def _parse_stories_md(content: str) -> list[UserStoryItem]:
        """Parse stories markdown into structured items."""
        stories: list[UserStoryItem] = []
        sections = re.split(r"^##\s+", content, flags=re.MULTILINE)
        for section in sections[1:]:
            lines = section.strip().splitlines()
            if not lines:
                continue
            story_id = lines[0].strip()
            persona_id = ""
            requirement_ids: list[str] = []
            statement_lines: list[str] = []

            for line in lines[1:]:
                stripped = line.strip()
                if stripped.startswith("**Persona") or stripped.startswith("Persona"):
                    persona_id = re.sub(r"^\*\*Persona\*\*[:\s]*|^Persona[:\s]*", "", stripped)
                elif stripped.startswith("**Requirement") or stripped.startswith("Requirement"):
                    reqs_text = re.sub(r"^\*\*Requirements?\*\*[:\s]*|^Requirements?[:\s]*", "", stripped)
                    requirement_ids = [r.strip() for r in re.split(r"[,;]\s*", reqs_text) if r.strip()]
                elif stripped:
                    statement_lines.append(stripped)

            stories.append(UserStoryItem(
                story_id=story_id,
                persona_id=persona_id,
                requirement_ids=requirement_ids,
                statement="\n".join(statement_lines),
            ))
        return stories

    @staticmethod
    def _parse_adr_md(content: str) -> ADRArtifact:
        """Parse ADR markdown (with or without YAML frontmatter) into structured data."""
        adr = ADRArtifact()

        # Extract YAML frontmatter
        fm_match = re.match(r"^---\n(.*?)\n---\n?", content, re.DOTALL)
        body = content
        if fm_match:
            for line in fm_match.group(1).splitlines():
                sep = line.find(":")
                if sep > 0:
                    key = line[:sep].strip()
                    val = line[sep + 1:].strip()
                    if key == "id":
                        adr.adr_id = val
                    elif key == "status":
                        adr.status = val
                    elif key == "implements":
                        # Parse [REQ-01, REQ-02] format
                        adr.implements = [s.strip().strip("'\"") for s in val.strip("[]").split(",") if s.strip()]
                    elif key == "supersedes":
                        adr.supersedes = val
            body = content[fm_match.end():]

        # Parse markdown sections
        current_key = ""
        current_lines: list[str] = []
        title_found = False

        for line in body.splitlines():
            if line.startswith("**Status:**") or line.startswith("**Date:**"):
                if line.startswith("**Date:**"):
                    adr.date = line.replace("**Date:**", "").strip()
                continue

            h1 = re.match(r"^#\s+(.+)", line)
            h2 = re.match(r"^##\s+(.+)", line)

            if h1 and not title_found:
                title_found = True
                adr.title = h1.group(1).strip()
            elif h2:
                # Flush previous section
                if current_key:
                    _set_adr_field(adr, current_key, "\n".join(current_lines).strip())
                heading = h2.group(1).strip().lower()
                current_key = _heading_to_adr_key(heading)
                current_lines = []
            else:
                current_lines.append(line)

        # Flush last section
        if current_key:
            _set_adr_field(adr, current_key, "\n".join(current_lines).strip())

        return adr

    # ── Markdown renderers ─────────────────────────────────────────────────

    @staticmethod
    def _render_sow_md(data: SOWArtifact) -> str:
        if data.raw_markdown:
            return data.raw_markdown
        lines = [f"# {data.title}", ""]
        if data.problem_statement:
            lines += ["## Problem Statement", "", data.problem_statement, ""]
        if data.stakeholders:
            lines += ["## Stakeholders", ""]
            for s in data.stakeholders:
                lines.append(f"### {s.get('name', 'Unknown')}")
                if s.get("needs"):
                    for n in s["needs"]:
                        lines.append(f"  - {n}")
                lines.append("")
        if data.constraints:
            lines += ["## Constraints", "", data.constraints, ""]
        if data.out_of_scope:
            lines += ["## Out of Scope", "", data.out_of_scope, ""]
        if data.open_questions:
            lines += ["## Open Questions", ""]
            for q in data.open_questions:
                lines.append(f"- {q}")
            lines.append("")
        if data.deliverables:
            lines += ["## Deliverables", ""]
            for d in data.deliverables:
                lines.append(f"- {d}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_srs_md(data: SRSArtifact) -> str:
        if data.raw_markdown:
            return data.raw_markdown
        lines = ["# Software Requirements Specification", ""]
        if data.introduction:
            lines += ["## Introduction", "", data.introduction, ""]
        if data.scope:
            lines += ["## Scope", "", data.scope, ""]

        fr = [r for r in data.requirements if r.category == "functional"]
        nfr = [r for r in data.requirements if r.category == "non-functional"]

        if fr:
            lines += ["## Functional Requirements", ""]
            for r in fr:
                lines.append(f"- [{r.req_id}] {r.statement}")
                if r.rationale:
                    lines.append(f"  - _Rationale:_ {r.rationale}")
                if r.acceptance:
                    lines.append(f"  - _Acceptance:_ {r.acceptance}")
            lines.append("")
        if nfr:
            lines += ["## Non-Functional Requirements", ""]
            for r in nfr:
                lines.append(f"- [{r.req_id}] {r.statement}")
                if r.rationale:
                    lines.append(f"  - _Rationale:_ {r.rationale}")
                if r.acceptance:
                    lines.append(f"  - _Acceptance:_ {r.acceptance}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_personas_md(data: PersonasArtifact) -> str:
        if data.raw_markdown:
            return data.raw_markdown
        lines = ["# Personas", ""]
        for p in data.personas:
            lines.append(f"## {p.name}")
            lines.append("")
            if p.persona_id:
                lines.append(f"**ID:** {p.persona_id}")
            if p.role:
                lines.append(f"**Role:** {p.role}")
            if p.goals:
                lines.append("**Goals:**")
                for g in p.goals:
                    lines.append(f"- {g}")
            if p.frustrations:
                lines.append("**Frustrations:**")
                for f in p.frustrations:
                    lines.append(f"- {f}")
            if p.description:
                lines.append("")
                lines.append(p.description)
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_stories_md(data: StoriesArtifact) -> str:
        if data.raw_markdown:
            return data.raw_markdown
        lines = ["# User Stories", ""]
        for s in data.stories:
            lines.append(f"## {s.story_id}")
            lines.append("")
            if s.persona_id:
                lines.append(f"**Persona:** {s.persona_id}")
            if s.requirement_ids:
                lines.append(f"**Requirements:** {', '.join(s.requirement_ids)}")
            lines.append("")
            lines.append(s.statement)
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_adr_md(data: ADRArtifact) -> str:
        """Render ADR as MADR-format markdown with YAML frontmatter."""
        lines: list[str] = []

        # YAML frontmatter for traceability
        fm = ["---", f"id: {data.adr_id}", f"status: {data.status}"]
        if data.implements:
            fm.append(f"implements: [{', '.join(data.implements)}]")
        if data.supersedes:
            fm.append(f"supersedes: {data.supersedes}")
        fm.append("---")
        lines += fm
        lines.append("")

        # Body
        lines.append(f"# {data.title}")
        lines.append("")
        lines.append(f"**Status:** {data.status.capitalize()}")
        lines.append(f"**Date:** {data.date}")
        lines.append("")

        if data.context:
            lines += ["## Context", "", data.context, ""]
        if data.decision:
            lines += ["## Decision", "", data.decision, ""]
        if data.alternatives:
            lines += ["## Alternatives Considered", "", data.alternatives, ""]
        if data.rationale:
            lines += ["## Rationale", "", data.rationale, ""]
        if data.consequences:
            lines += ["## Consequences", "", data.consequences, ""]

        # References section
        ref_lines: list[str] = []
        if data.implements:
            ref_lines.append(f"- Implements: {', '.join(data.implements)}")
        if data.references:
            for r in data.references:
                ref_lines.append(f"- {r}")
        if data.supersedes:
            ref_lines.append(f"- Supersedes: {data.supersedes}")
        if ref_lines:
            lines += ["## References", ""] + ref_lines + [""]

        return "\n".join(lines)

    @staticmethod
    def _render_bdd_md(data: BDDArtifact) -> str:
        if data.raw_gherkin:
            return data.raw_gherkin
        lines: list[str] = []
        for s in data.scenarios:
            if s.scenario_id:
                lines.append(f"# id: {s.scenario_id}")
            if s.story_id:
                lines.append(f"# story: {s.story_id}")
            if s.adr_id:
                lines.append(f"# adr: {s.adr_id}")
            lines.append(s.gherkin)
            lines.append("")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

_ADR_HEADING_MAP: dict[str, str] = {
    "context": "context",
    "decision": "decision",
    "alternatives considered": "alternatives",
    "alternatives": "alternatives",
    "rationale": "rationale",
    "consequences": "consequences",
    "references": "references",
    "status": "",  # skip
}


def _heading_to_adr_key(heading: str) -> str:
    """Map a markdown heading to an ADR field key."""
    return _ADR_HEADING_MAP.get(heading, heading.replace(" ", "_"))


def _set_adr_field(adr: ADRArtifact, key: str, value: str) -> None:
    """Set a field on ADRArtifact by key name."""
    if key == "context":
        adr.context = value
    elif key == "decision":
        adr.decision = value
    elif key == "alternatives":
        adr.alternatives = value
    elif key == "rationale":
        adr.rationale = value
    elif key == "consequences":
        adr.consequences = value
    elif key == "references":
        # Parse references for implements
        for line in value.splitlines():
            m = re.match(r"^[-*]\s+Implements:\s*(.+)", line)
            if m:
                adr.implements = [r.strip() for r in m.group(1).split(",") if r.strip()]
            m2 = re.match(r"^[-*]\s+Supersedes:\s*(.+)", line)
            if m2:
                adr.supersedes = m2.group(1).strip()

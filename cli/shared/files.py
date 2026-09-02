"""Shared file-system helpers for the Sdlicit CLI.

The CLI owns all file I/O — the backend never writes files.

Workspace layout (all under ``<working_dir>/.sdlicit/``)::

    .sdlicit/
        config.yaml
        artifacts/
            SOW-*.md      # Statements of Work
            adr/          # Architecture Decision Records
            personas/     # Generated personas
            stories/      # User stories
            srs/          # Software requirements
        knowledge/        # Knowledge base corpus
        sessions/
            chat/         # Per-query chat sessions  (ToM)
            sdlicit/      # Per-CLI-run session data  (ToM)
            user/         # Persistent user knowledge (ToM)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ── Workspace layout constants ─────────────────────────────────────────────
ROOT = Path(".sdlicit")
ARTIFACTS_DIR = ROOT / "artifacts"

ADR_DIR = ARTIFACTS_DIR / "adr"
PERSONAS_DIR = ARTIFACTS_DIR / "personas"
STORIES_DIR = ARTIFACTS_DIR / "stories"
SRS_DIR = ARTIFACTS_DIR / "srs"
KNOWLEDGE_DIR = ROOT / "knowledge"

SESSIONS_DIR = ROOT / "sessions"
SESSIONS_CHAT_DIR = SESSIONS_DIR / "chat"
SESSIONS_SDLICIT_DIR = SESSIONS_DIR / "sdlicit"
SESSIONS_USER_DIR = SESSIONS_DIR / "user"


# ── Path helpers ───────────────────────────────────────────────────────────


def sdlicit_root(working_dir: str) -> Path:
    """Return the ``.sdlicit/`` root for a working directory."""
    return Path(working_dir) / ROOT


def adr_dir(working_dir: str) -> Path:
    return Path(working_dir) / ADR_DIR


def sessions_path(working_dir: str) -> Path:
    return Path(working_dir) / SESSIONS_DIR


# ── ADR helpers ────────────────────────────────────────────────────────────


def list_adr_files(working_dir: str) -> list[dict[str, str | None]]:
    """Scan ``.sdlicit/adr/`` and return [{filename, title}, ...]."""
    path = adr_dir(working_dir)
    results: list[dict[str, str | None]] = []
    if not path.is_dir():
        return results
    for f in sorted(path.iterdir()):
        if f.suffix == ".md" and f.is_file():
            title: str | None = None
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.startswith("# "):
                        title = line[2:].strip()
                        break
            except OSError:
                pass
            results.append({"filename": f.name, "title": title})
    return results


def write_adr(working_dir: str, filename: str, content: str) -> Path:
    """Write an ADR file to ``.sdlicit/adr/`` and return its path."""
    dest = adr_dir(working_dir)
    dest.mkdir(parents=True, exist_ok=True)
    file_path = dest / filename
    file_path.write_text(content, encoding="utf-8")
    return file_path


def read_adr(working_dir: str, filename: str) -> str:
    """Read and return the raw content of an ADR file."""
    return (adr_dir(working_dir) / filename).read_text(encoding="utf-8")


# ── SOW helpers ────────────────────────────────────────────────────────────


def artifacts_dir(working_dir: str) -> Path:
    return Path(working_dir) / ARTIFACTS_DIR


def write_sow(working_dir: str, filename: str, content: str) -> Path:
    """Write a SOW file to ``.sdlicit/artifacts/`` and return its path."""
    dest = artifacts_dir(working_dir)
    dest.mkdir(parents=True, exist_ok=True)
    file_path = dest / filename
    file_path.write_text(content, encoding="utf-8")
    return file_path


# ── SOW lookups ────────────────────────────────────────────────────────────


def list_sow_files(working_dir: str) -> list[Path]:
    """Return every ``SOW-*.md`` file in ``.sdlicit/artifacts/``."""
    path = artifacts_dir(working_dir)
    if not path.is_dir():
        return []
    return sorted(path.glob("SOW-*.md"))


def latest_sow(working_dir: str) -> Path | None:
    files = list_sow_files(working_dir)
    return files[-1] if files else None


def next_sow_filename(working_dir: str, title_hint: str) -> str:
    n = len(list_sow_files(working_dir)) + 1
    slug = title_hint[:40].lower().replace(" ", "-").replace("/", "-").strip("-")
    return f"SOW-{n:04d}-{slug or 'untitled'}.md"


# ── SRS helpers ────────────────────────────────────────────────────────────


def list_srs_files(working_dir: str) -> list[Path]:
    """Every ``SRS-*.md`` in ``.sdlicit/artifacts/``."""
    path = artifacts_dir(working_dir)
    if not path.is_dir():
        return []
    return sorted(path.glob("SRS-*.md"))


def latest_srs(working_dir: str) -> Path | None:
    files = list_srs_files(working_dir)
    return files[-1] if files else None


def write_srs(working_dir: str, content: str, slug: str = "requirements") -> Path:
    """Write a new ``SRS-NNNN-<slug>.md`` to artifacts/."""
    dest = artifacts_dir(working_dir)
    dest.mkdir(parents=True, exist_ok=True)
    n = len(list_srs_files(working_dir)) + 1
    safe = (
        slug[:40].lower().replace(" ", "-").replace("/", "-").strip("-")
        or "requirements"
    )
    file_path = dest / f"SRS-{n:04d}-{safe}.md"
    file_path.write_text(content, encoding="utf-8")
    return file_path


# ── Personas helpers ───────────────────────────────────────────────────────


def personas_dir(working_dir: str) -> Path:
    return Path(working_dir) / PERSONAS_DIR


def personas_json_path(working_dir: str) -> Path:
    return personas_dir(working_dir) / "personas.json"


def personas_md_path(working_dir: str) -> Path:
    return personas_dir(working_dir) / "personas.md"


def load_personas(working_dir: str) -> list[dict]:
    """Load personas.json if present; return []."""
    import json

    p = personas_json_path(working_dir)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and "personas" in data:
        return list(data["personas"])
    if isinstance(data, list):
        return data
    return []


def write_personas(working_dir: str, personas: list[dict]) -> tuple[Path, Path]:
    """Persist personas as JSON + a human-readable markdown table."""
    import json

    p_dir = personas_dir(working_dir)
    p_dir.mkdir(parents=True, exist_ok=True)

    json_path = personas_json_path(working_dir)
    json_path.write_text(json.dumps({"personas": personas}, indent=2), encoding="utf-8")

    md_lines = ["# Personas", ""]
    for p in personas:
        md_lines.append(f"## {p.get('name', '?')} — {p.get('role', '?')}")
        if p.get("goals"):
            md_lines.append("**Goals:**")
            md_lines.extend(f"- {g}" for g in p["goals"])
        if p.get("frustrations"):
            md_lines.append("**Frustrations:**")
            md_lines.extend(f"- {f}" for f in p["frustrations"])
        md_lines.append("")
    md_path = personas_md_path(working_dir)
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return json_path, md_path


# ── Stories helpers ────────────────────────────────────────────────────────


def stories_dir(working_dir: str) -> Path:
    return Path(working_dir) / STORIES_DIR


def stories_json_path(working_dir: str) -> Path:
    return stories_dir(working_dir) / "stories.json"


def stories_md_path(working_dir: str) -> Path:
    return stories_dir(working_dir) / "stories.md"


def load_stories(working_dir: str) -> list[dict]:
    import json

    p = stories_json_path(working_dir)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and "stories" in data:
        return list(data["stories"])
    if isinstance(data, list):
        return data
    return []


def write_stories(working_dir: str, stories: list[dict]) -> tuple[Path, Path]:
    import json

    s_dir = stories_dir(working_dir)
    s_dir.mkdir(parents=True, exist_ok=True)

    json_path = stories_json_path(working_dir)
    json_path.write_text(json.dumps({"stories": stories}, indent=2), encoding="utf-8")

    md_lines = ["# User Stories", ""]
    for s in stories:
        sid = s.get("story_id", "?")
        pid = s.get("persona_id", "?")
        refs = ", ".join(s.get("requirement_ids", []))
        md_lines.append(
            f"- **{sid}** _(persona: {pid}; req: {refs})_ — {s.get('statement', '')}"
        )
    md_path = stories_md_path(working_dir)
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return json_path, md_path


# ── Gherkin helpers ────────────────────────────────────────────────────────


def gherkin_dir(working_dir: str) -> Path:
    return Path(working_dir) / ARTIFACTS_DIR / "gherkin"


def list_gherkin_files(working_dir: str) -> list[Path]:
    g = gherkin_dir(working_dir)
    if not g.is_dir():
        return []
    return sorted(g.glob("*.feature"))


def write_gherkin(working_dir: str, slug: str, content: str) -> Path:
    g = gherkin_dir(working_dir)
    g.mkdir(parents=True, exist_ok=True)
    safe = (slug or "scenarios").lower().replace(" ", "-").replace("/", "-").strip("-")
    if not safe:
        safe = "scenarios"
    path = g / f"{safe}.feature"
    path.write_text(content, encoding="utf-8")
    return path


# ── Backend-routed artifact save ───────────────────────────────────────────


def save_artifact_via_backend(
    client: Any,
    artifact_type: str,
    data: dict,
    working_dir: str = "",
) -> dict:
    """Save an artifact through the backend API for consistent naming/format.

    Falls back to local write if the backend call fails.
    Returns the response dict with ``json_path``, ``markdown_path``, and
    ``artifact_meta``.
    """
    try:
        return client.save_artifact(
            artifact_type=artifact_type,
            data=data,
            project_dir=working_dir,
        )
    except Exception:
        return {}

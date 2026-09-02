"""Read-only project file access for the backend.

The backend can read any file within a project directory but never
writes user-facing artifacts (ADRs, SOWs, etc.).  Writing is the
responsibility of the CLI / VS Code extension.

This module mirrors the reading functions in ``cli/shared/files.py``
so the backend can load ADRs and other project files itself instead
of receiving file contents over HTTP.

Workspace layout (all under ``<project>/.sdlicit/``)::

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

from dataclasses import dataclass, field
from pathlib import Path

from sdlicit.logging import get_logger

_log = get_logger("project")

# ── Workspace layout constants ─────────────────────────────────────────────
SDLICIT_DIR = Path(".sdlicit")
ARTIFACTS_DIR = SDLICIT_DIR / "artifacts"

ADR_DIR = ARTIFACTS_DIR / "adr"
BDD_DIR = ARTIFACTS_DIR / "bdd"
PERSONAS_DIR = ARTIFACTS_DIR / "personas"
STORIES_DIR = ARTIFACTS_DIR / "stories"
SRS_DIR = ARTIFACTS_DIR / "srs"
KNOWLEDGE_DIR = SDLICIT_DIR / "knowledge"

# Canonical single-file artifacts (relative to ARTIFACTS_DIR)
SOW_FILE = "sow.md"
SRS_FILE = "srs.md"
PERSONAS_FILE = "personas.md"
STORIES_FILE = "stories.md"

SESSIONS_DIR = SDLICIT_DIR / "sessions"
SESSIONS_CHAT_DIR = SESSIONS_DIR / "chat"
SESSIONS_SDLICIT_DIR = SESSIONS_DIR / "sdlicit"
SESSIONS_USER_DIR = SESSIONS_DIR / "user"


# ── Path helpers ───────────────────────────────────────────────────────────


def sdlicit_root(project_dir: Path) -> Path:
    """Return the ``.sdlicit/`` root for a project."""
    return project_dir / SDLICIT_DIR


def adr_dir(project_dir: Path) -> Path:
    """Return the ADR directory for a project."""
    return project_dir / ADR_DIR


def sessions_dir(project_dir: Path) -> Path:
    """Return the sessions root for a project."""
    return project_dir / SESSIONS_DIR


# ── File listing / reading helpers ─────────────────────────────────────────


def _list_md_files(directory: Path) -> list[dict[str, str | None]]:
    """List markdown files in *directory*, extracting ``# Title`` from each."""
    results: list[dict[str, str | None]] = []
    if not directory.is_dir():
        return results
    for f in sorted(directory.iterdir()):
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


def list_adr_files(project_dir: Path) -> list[dict[str, str | None]]:
    """Scan ``.sdlicit/adr/`` and return [{filename, title}, ...]."""
    return _list_md_files(adr_dir(project_dir))


def read_adr(project_dir: Path, filename: str) -> str:
    """Read and return the raw content of an ADR file."""
    return (adr_dir(project_dir) / filename).read_text(encoding="utf-8")


# ── ADR cache (mtime-invalidated) ──────────────────────────────────────────

@dataclass
class _AdrCache:
    """Simple per-directory mtime-invalidated cache for ADR contents."""

    _entries: dict[Path, tuple[float, list[str]]] = field(default_factory=dict)

    def _max_mtime(self, directory: Path) -> float:
        if not directory.is_dir():
            return 0.0
        return max(
            (f.stat().st_mtime for f in directory.iterdir() if f.suffix == ".md" and f.is_file()),
            default=0.0,
        )

    def get(self, project_dir: Path) -> list[str]:
        directory = adr_dir(project_dir)
        mtime = self._max_mtime(directory)
        cached = self._entries.get(directory)
        if cached and cached[0] >= mtime:
            return list(cached[1])  # return a copy
        # Rebuild
        contents: list[str] = []
        for entry in list_adr_files(project_dir):
            name = entry.get("filename")
            if name:
                try:
                    contents.append(read_adr(project_dir, name))
                except OSError as exc:
                    _log.warning("Could not read ADR %s: %s", name, exc)
        self._entries[directory] = (mtime, contents)
        return list(contents)


_adr_cache = _AdrCache()


def read_all_adrs(project_dir: Path) -> list[str]:
    """Read all ADR files and return their contents (mtime-cached)."""
    return _adr_cache.get(project_dir)


def read_all_adrs_except(project_dir: Path, exclude_filename: str) -> list[str]:
    """Read all ADR files except the named one."""
    contents: list[str] = []
    for entry in list_adr_files(project_dir):
        name = entry.get("filename")
        if name and name != exclude_filename:
            try:
                contents.append(read_adr(project_dir, name))
            except OSError as exc:
                _log.warning("Could not read ADR %s: %s", name, exc)
    return contents

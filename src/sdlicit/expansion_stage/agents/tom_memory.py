"""Three-tier hierarchical memory for the ToM agent.

Layout::

    sessions/
        chat/<session_id>/<seq>-<endpoint>.json  Tier 1 — append-only raw interactions
        sdlicit/<session_id>/tom_analysis.json   Tier 2 — per-session analysis
        user/user_model.json                     Tier 3 — aggregated user model
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sdlicit.expansion_stage.agents.tom_models import (
    RawInteraction,
    RawSession,
    SessionUserModel,
    UserModel,
)
from sdlicit.logging import get_logger

_log = get_logger("tom")

_USER_MODEL_FILE = "user_model.json"
_STATE_OF_MIND_FILE = "state_of_mind.json"
_STATE_OF_MIND_LOG_FILE = "state_of_mind_log.json"


class ToMMemory:
    """Read and write the three-tier ToM memory hierarchy.

    Writes use atomic rename (write to `.tmp` then ``replace()``) to
    prevent partial reads from corrupted files.
    """

    def __init__(
        self,
        chat_dir: Path,
        sdlicit_dir: Path,
        user_dir: Path,
    ) -> None:
        self._chat_dir = chat_dir
        self._sdlicit_dir = sdlicit_dir
        self._user_dir = user_dir

    # -- Tier 1: Raw sessions --------------------------------------------------

    def load_raw_session(self, session_id: str) -> RawSession | None:
        """Load a single raw session by ID.

        Looks first for ``chat/<session_id>.json`` (legacy single-file
        layout), then for ``chat/<session_id>/`` containing per-request
        files written by the CLI journal.
        """
        legacy = self._chat_dir / f"{session_id}.json"
        if legacy.exists():
            data = json.loads(legacy.read_text(encoding="utf-8"))
            return RawSession(**data)

        session_dir = self._chat_dir / session_id
        if session_dir.is_dir():
            return _build_raw_session_from_dir(session_id, session_dir)
        return None

    def list_raw_sessions(self) -> list[str]:
        """Return all raw session IDs (sorted newest-first)."""
        if not self._chat_dir.is_dir():
            return []
        ids: set[str] = set()
        for p in self._chat_dir.iterdir():
            if p.is_file() and p.suffix == ".json":
                ids.add(p.stem)
            elif p.is_dir():
                ids.add(p.name)
        return sorted(ids, reverse=True)

    def recent_raw_sessions(self, n: int = 5) -> list[RawSession]:
        """Load the *n* most recent raw sessions."""
        sessions: list[RawSession] = []
        for sid in self.list_raw_sessions()[:n]:
            s = self.load_raw_session(sid)
            if s is not None:
                sessions.append(s)
        return sessions

    # -- Tier 2: Session analyses ----------------------------------------------

    def load_session_model(self, session_id: str) -> SessionUserModel | None:
        """Load a session analysis by ID.

        Supports both ``sdlicit/<session_id>.json`` (legacy) and
        ``sdlicit/<session_id>/tom_analysis.json`` (CLI journal layout).
        """
        legacy = self._sdlicit_dir / f"{session_id}.json"
        if legacy.exists():
            data = json.loads(legacy.read_text(encoding="utf-8"))
            return SessionUserModel(**data)

        nested = self._sdlicit_dir / session_id / "tom_analysis.json"
        if nested.exists():
            data = json.loads(nested.read_text(encoding="utf-8"))
            return SessionUserModel(**data)
        return None

    def list_session_models(self) -> list[str]:
        """Return all session-model IDs (newest-first)."""
        if not self._sdlicit_dir.is_dir():
            return []
        ids: set[str] = set()
        for p in self._sdlicit_dir.iterdir():
            if p.is_file() and p.suffix == ".json":
                ids.add(p.stem)
            elif p.is_dir() and (p / "tom_analysis.json").exists():
                ids.add(p.name)
        return sorted(ids, reverse=True)

    def recent_session_models(self, n: int = 5) -> list[SessionUserModel]:
        """Load the *n* most recent session models."""
        models: list[SessionUserModel] = []
        for sid in self.list_session_models()[:n]:
            m = self.load_session_model(sid)
            if m is not None:
                models.append(m)
        return models

    # -- Tier 3: Aggregated user model -----------------------------------------

    def load_user_model(self) -> UserModel:
        """Load the overall user model, returning a default if absent."""
        path = self._user_dir / _USER_MODEL_FILE
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return UserModel(**data)
        return UserModel()

    def load_state_of_mind(self) -> dict[str, Any] | None:
        """Load the current state-of-mind snapshot (written by extension)."""
        path = self._user_dir / _STATE_OF_MIND_FILE
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        return None

    def load_state_of_mind_log(self, max_entries: int = 20) -> list[dict[str, Any]]:
        """Load recent state-of-mind history entries."""
        path = self._user_dir / _STATE_OF_MIND_LOG_FILE
        if not path.exists():
            return []
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
            return entries[-max_entries:] if isinstance(entries, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    # -- Write methods (atomic) ------------------------------------------------

    def append_raw_interaction(
        self, session_id: str, seq: int, endpoint: str, payload: dict[str, Any]
    ) -> None:
        """Append a single interaction to a session's Tier 1 directory."""
        session_dir = self._chat_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{seq:04d}-{endpoint}.json"
        self._atomic_write(session_dir / filename, payload)

    def save_raw_session(self, session_id: str, data: dict[str, Any]) -> None:
        """Write the full raw session as Tier 1 (legacy single-file format)."""
        self._chat_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self._chat_dir / f"{session_id}.json", data)

    def save_session_model(self, session_id: str, model: dict[str, Any]) -> None:
        """Write Tier 2 session analysis."""
        session_dir = self._sdlicit_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(session_dir / "tom_analysis.json", model)

    def save_user_model(self, model: dict[str, Any]) -> None:
        """Write Tier 3 aggregated user model."""
        self._user_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self._user_dir / _USER_MODEL_FILE, model)

    def save_compact(self, session_id: str, data: dict[str, Any]) -> None:
        """Write a mid-session compaction summary."""
        session_dir = self._sdlicit_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(session_dir / "compact.json", data)

    def load_compact(self, session_id: str) -> dict[str, Any] | None:
        """Load a compaction summary for a session, if it exists."""
        path = self._sdlicit_dir / session_id / "compact.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        return None

    @staticmethod
    def _atomic_write(path: Path, data: dict[str, Any]) -> None:
        """Write JSON atomically via tmp + rename."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)

    # -- Cross-tier search -----------------------------------------------------

    def search_interactions(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Simple keyword search across recent raw sessions."""
        query_lower = query.lower()
        results: list[dict[str, Any]] = []

        for session in self.recent_raw_sessions(n=10):
            for interaction in session.interactions:
                text = json.dumps(interaction.data).lower()
                if query_lower in text or query_lower in interaction.event_type.lower():
                    results.append(
                        {
                            "session_id": session.session_id,
                            "stage": session.stage,
                            "interaction": interaction.model_dump(),
                        }
                    )
                    if len(results) >= max_results:
                        return results

        return results

    def build_context_summary(
        self, max_sessions: int = 3, current_session_id: str | None = None
    ) -> str:
        """Build a compact text summary of recent history for LLM context."""
        parts: list[str] = []

        # If a compaction exists for the current session, use it first
        if current_session_id:
            compact = self.load_compact(current_session_id)
            if compact and "summary" in compact:
                summary = compact["summary"]
                parts.append(
                    f"Current session (compacted {compact.get('interaction_count', '?')} interactions): "
                    f"intents={summary.get('inferred_intents', [])}, "
                    f"patterns={summary.get('interaction_patterns', [])}"
                )

        user = self.load_user_model()
        if user.session_count > 0:
            parts.append(
                f"User profile (across {user.session_count} sessions): "
                f"style={user.interaction_style}, "
                f"scaffolding={user.scaffolding_preference}, "
                f"expertise={user.expertise_areas}"
            )

        # Include current state of mind if available
        state = self.load_state_of_mind()
        if state:
            parts.append(
                f"Current state: frustration={state.get('frustration_level', 'unknown')}, "
                f"engagement={state.get('engagement', 'unknown')}, "
                f"confidence={state.get('confidence', 'unknown')}, "
                f"goals={state.get('inferred_goals', [])}"
            )

        for sm in self.recent_session_models(n=max_sessions):
            parts.append(
                f"Session {sm.session_id}: "
                f"intents={sm.inferred_intents}, "
                f"patterns={sm.interaction_patterns}, "
                f"scaffolding={sm.scaffolding_level}"
            )

        return "\n".join(parts) if parts else "No prior session data available."


# ── Helpers ────────────────────────────────────────────────────────────────


def _build_raw_session_from_dir(session_id: str, session_dir: Path) -> RawSession:
    """Reconstruct a ``RawSession`` from a CLI-written per-request directory.

    The CLI writes one JSON file per request into ``chat/<sid>/``.  Each
    file holds ``{kind, endpoint, ts, request, response, ...}`` and is
    converted into a :class:`RawInteraction`.
    """
    interactions: list[RawInteraction] = []
    started_at: str | None = None
    ended_at: str | None = None

    files = sorted(session_dir.glob("*.json"))
    for f in files:
        try:
            entry = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        ts = entry.get("ts", "")
        if started_at is None:
            started_at = ts
        ended_at = ts or ended_at

        interactions.append(
            RawInteraction(
                event_type=entry.get("kind", entry.get("endpoint", "request")),
                agent=entry.get("endpoint", ""),
                data={
                    k: v
                    for k, v in entry.items()
                    if k not in ("kind", "endpoint", "ts")
                },
            )
        )

    return RawSession(
        session_id=session_id,
        stage=_infer_stage_from_endpoints(files) or "unknown",
        started_at=started_at or "",
        ended_at=ended_at or "",
        interactions=interactions,
    )


def _infer_stage_from_endpoints(files: list[Path]) -> str | None:
    """Best-effort stage inference from filename prefixes.

    Per-request files are named ``<seq>-<sanitized_endpoint>.json``;
    e.g. ``0001-intake-sow.json``.  Returns the most common stage prefix.
    """
    counts: dict[str, int] = {}
    for f in files:
        name = f.stem  # "0001-intake-sow"
        parts = name.split("-", 2)
        if len(parts) >= 2:
            counts[parts[1]] = counts.get(parts[1], 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]

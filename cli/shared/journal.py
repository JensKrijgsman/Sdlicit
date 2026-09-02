"""Crash-safe per-request journal for the Sdlicit CLI.

The CLI owns *all* writes under ``.sdlicit/sessions/``.  Every HTTP
request the CLI makes is journaled through this module **before** the
HTTP call is issued and **again** after the response (or error) is
received, with ``fsync`` after each write so a SIGKILL leaves a usable
trace on disk.

Disk layout (per project)::

    .sdlicit/sessions/
        chat/<session_id>/<seq>-<endpoint>.json     # raw per-request entry
        sdlicit/<session_id>/
            meta.json        — session metadata + status (active/closed/crashed)
            tokens.json      — per-agent token aggregate
            compact.json     — ToM compacted summary (≤ threshold)
            tom_analysis.json — Tier-2 analysis (CLI writes; backend computes)
        user/user_model.json — Tier-3 (CLI writes; backend computes)
        index.json           — last_session_id + recent[]

A single :class:`Journal` instance lives for the whole CLI run.  It is
bound to a session_id supplied by the backend (``POST /session/start``)
or, if the backend is unreachable, a locally-generated UUID prefix.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── Disk layout (mirror of cli/shared/files.py) ───────────────────────────

ROOT = Path(".sdlicit")
SESSIONS_DIR = ROOT / "sessions"
CHAT_DIR = SESSIONS_DIR / "chat"
SDLICIT_DIR = SESSIONS_DIR / "sdlicit"
USER_DIR = SESSIONS_DIR / "user"
INDEX_FILE = SESSIONS_DIR / "index.json"

USER_MODEL_FILE = "user_model.json"
META_FILE = "meta.json"
TOKENS_FILE = "tokens.json"
COMPACT_FILE = "compact.json"
TOM_ANALYSIS_FILE = "tom_analysis.json"


_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _sanitize(name: str, max_len: int = 40) -> str:
    """Filesystem-safe version of an endpoint name."""
    cleaned = _SAFE.sub("-", name).strip("-")
    return (cleaned or "request")[:max_len]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON to *path* atomically with fsync (write-temp + rename).

    Atomic rename gives crash safety for files that need a coherent
    snapshot (meta.json, index.json, user_model.json).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, indent=2, default=str).encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _append_write(path: Path, payload: dict[str, Any]) -> None:
    """Write a single JSON document with fsync (one file per request)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, default=str).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


# ── Payload sanitisation ──────────────────────────────────────────────────

_MAX_PAYLOAD_CHARS = 20_000


def _summarize(value: Any) -> Any:
    """Trim large strings/lists for journaling (full data lives in caller's memory).

    Keeps shape but truncates strings beyond ``_MAX_PAYLOAD_CHARS`` and
    lists beyond 50 elements.  Used for HTTP request/response bodies.
    """
    if isinstance(value, str):
        if len(value) > _MAX_PAYLOAD_CHARS:
            return value[:_MAX_PAYLOAD_CHARS] + f"…[truncated {len(value)} chars]"
        return value
    if isinstance(value, list):
        if len(value) > 50:
            return [_summarize(v) for v in value[:50]] + [
                f"…[truncated {len(value) - 50} items]"
            ]
        return [_summarize(v) for v in value]
    if isinstance(value, dict):
        return {k: _summarize(v) for k, v in value.items()}
    return value


# ── Token usage helper ────────────────────────────────────────────────────


def parse_usage_headers(headers: dict[str, str] | Any) -> dict[str, Any]:
    """Extract ``X-Sdlicit-Tokens-*`` headers into a dict.

    Accepts both a plain dict and an ``httpx.Headers`` object.
    """

    def _get(name: str) -> str | None:
        if hasattr(headers, "get"):
            return headers.get(name)
        return None

    by_agent_raw = _get("X-Sdlicit-Tokens-By-Agent")
    by_agent: dict[str, Any] = {}
    if by_agent_raw:
        try:
            by_agent = json.loads(by_agent_raw)
        except json.JSONDecodeError:
            by_agent = {}

    return {
        "prompt_tokens": int(_get("X-Sdlicit-Tokens-Prompt") or 0),
        "completion_tokens": int(_get("X-Sdlicit-Tokens-Completion") or 0),
        "total_tokens": int(_get("X-Sdlicit-Tokens-Total") or 0),
        "calls": int(_get("X-Sdlicit-Tokens-Calls") or 0),
        "by_agent": by_agent,
    }


# ── Journal ───────────────────────────────────────────────────────────────


class Journal:
    """Crash-safe per-session writer for CLI request/response artifacts."""

    def __init__(
        self,
        working_dir: str | Path,
        session_id: str | None = None,
    ) -> None:
        self.working_dir = Path(working_dir)
        self.session_id = session_id or _generate_session_id()
        self._seq = 0
        self._totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "by_agent": {},  # agent -> {prompt, completion, total, calls}
        }
        self._meta = {
            "session_id": self.session_id,
            "started_at": _now_iso(),
            "last_event_at": _now_iso(),
            "status": "active",
            "stage": "cli",
            "event_count": 0,
            "tokens": self._totals,
            "endpoints": {},  # endpoint -> count
            "model": None,
            "model_context_window": None,
            "compact_threshold_pct": None,
            "compactions": 0,
        }
        # Ensure directories exist.
        self.chat_session_dir.mkdir(parents=True, exist_ok=True)
        self.sdlicit_session_dir.mkdir(parents=True, exist_ok=True)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self._flush_meta()
        self._update_index(active=True)

    # -- Path helpers ------------------------------------------------------

    @property
    def root(self) -> Path:
        return self.working_dir / SESSIONS_DIR

    @property
    def chat_session_dir(self) -> Path:
        return self.working_dir / CHAT_DIR / self.session_id

    @property
    def sdlicit_session_dir(self) -> Path:
        return self.working_dir / SDLICIT_DIR / self.session_id

    @property
    def user_dir(self) -> Path:
        return self.working_dir / USER_DIR

    @property
    def meta_path(self) -> Path:
        return self.sdlicit_session_dir / META_FILE

    @property
    def tokens_path(self) -> Path:
        return self.sdlicit_session_dir / TOKENS_FILE

    @property
    def compact_path(self) -> Path:
        return self.sdlicit_session_dir / COMPACT_FILE

    @property
    def tom_analysis_path(self) -> Path:
        return self.sdlicit_session_dir / TOM_ANALYSIS_FILE

    @property
    def user_model_path(self) -> Path:
        return self.user_dir / USER_MODEL_FILE

    @property
    def index_path(self) -> Path:
        return self.working_dir / INDEX_FILE

    # -- Public configuration -------------------------------------------------

    def set_server_config(self, server_config: dict[str, Any]) -> None:
        """Stamp the active server config (model, ctx window, etc.) into meta."""
        self._meta["model"] = server_config.get("model")
        self._meta["model_context_window"] = server_config.get("model_context_window")
        self._meta["compact_threshold_pct"] = server_config.get("compact_threshold_pct")
        self._flush_meta()

    # -- Recording ------------------------------------------------------------

    def record_request(
        self,
        endpoint: str,
        method: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Journal an outgoing request *before* it's sent.

        Returns a context dict that should be passed to
        :meth:`record_response` so the matching response file links back.
        """
        self._seq += 1
        seq = self._seq
        ctx = {
            "seq": seq,
            "endpoint": endpoint,
            "method": method,
            "started_at": _now_iso(),
            "started_perf": time.perf_counter(),
            "filename": f"{seq:04d}-{_sanitize(endpoint)}.json",
        }

        entry = {
            "kind": "request",
            "seq": seq,
            "endpoint": endpoint,
            "method": method,
            "ts": ctx["started_at"],
            "request": _summarize(payload) if payload is not None else None,
        }
        _append_write(self.chat_session_dir / ctx["filename"], entry)

        self._meta["event_count"] = seq
        self._meta["last_event_at"] = ctx["started_at"]
        self._meta["endpoints"][endpoint] = self._meta["endpoints"].get(endpoint, 0) + 1
        self._flush_meta()
        return ctx

    def record_response(
        self,
        ctx: dict[str, Any],
        *,
        status_code: int,
        response: Any = None,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Journal a response (or error) for a previously recorded request."""
        latency_ms = (time.perf_counter() - ctx["started_perf"]) * 1000.0

        entry = {
            "kind": "error" if error else "response",
            "seq": ctx["seq"],
            "endpoint": ctx["endpoint"],
            "method": ctx["method"],
            "ts": _now_iso(),
            "started_at": ctx["started_at"],
            "status_code": status_code,
            "latency_ms": round(latency_ms, 2),
            "usage": usage or {},
            "response": _summarize(response) if response is not None else None,
            "error": error,
        }
        _append_write(self.chat_session_dir / ctx["filename"], entry)

        if usage:
            self._accumulate_usage(usage)
        self._meta["last_event_at"] = entry["ts"]
        self._flush_meta()
        self._flush_tokens()

    def note(self, kind: str, payload: dict[str, Any]) -> None:
        """Stage-level event that's not an HTTP round-trip.

        Use this for UI-side breadcrumbs that should still appear in the
        session log (e.g. "user picked menu item X", "wizard step Y").
        """
        self._seq += 1
        seq = self._seq
        entry = {
            "kind": kind,
            "seq": seq,
            "ts": _now_iso(),
            **payload,
        }
        filename = f"{seq:04d}-{_sanitize(kind)}.json"
        _append_write(self.chat_session_dir / filename, entry)
        self._meta["event_count"] = seq
        self._meta["last_event_at"] = entry["ts"]
        self._flush_meta()

    # -- Tokens ---------------------------------------------------------------

    def _accumulate_usage(self, usage: dict[str, Any]) -> None:
        self._totals["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        self._totals["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        self._totals["total_tokens"] += int(usage.get("total_tokens") or 0)
        self._totals["calls"] += int(usage.get("calls") or 0)
        for agent, slot in (usage.get("by_agent") or {}).items():
            running = self._totals["by_agent"].setdefault(
                agent,
                {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "calls": 0,
                },
            )
            running["prompt_tokens"] += int(slot.get("prompt_tokens") or 0)
            running["completion_tokens"] += int(slot.get("completion_tokens") or 0)
            running["total_tokens"] += int(slot.get("total_tokens") or 0)
            running["calls"] += int(slot.get("calls") or 0)

    @property
    def totals(self) -> dict[str, Any]:
        return dict(self._totals)

    # -- ToM artefacts (CLI is the writer) ------------------------------------

    def write_tom_analysis(self, payload: dict[str, Any]) -> None:
        _atomic_write(self.tom_analysis_path, payload)

    def write_user_model(self, payload: dict[str, Any]) -> None:
        _atomic_write(self.user_model_path, payload)

    def write_compact(self, payload: dict[str, Any]) -> None:
        _atomic_write(self.compact_path, payload)
        self._meta["compactions"] = int(self._meta.get("compactions", 0)) + 1
        self._flush_meta()

    def has_compact(self) -> bool:
        return self.compact_path.exists()

    # -- Lifecycle ------------------------------------------------------------

    def mark_closed(self, status: str = "closed") -> None:
        """Mark the session as cleanly closed (or interrupted)."""
        self._meta["status"] = status
        self._meta["last_event_at"] = _now_iso()
        self._flush_meta()
        self._update_index(active=False)

    # -- Internal flushers ----------------------------------------------------

    def _flush_meta(self) -> None:
        self._meta["tokens"] = self._totals
        _atomic_write(self.meta_path, self._meta)

    def _flush_tokens(self) -> None:
        _atomic_write(
            self.tokens_path,
            {
                "session_id": self.session_id,
                "updated_at": _now_iso(),
                **self._totals,
            },
        )

    def _update_index(self, *, active: bool) -> None:
        idx_payload = read_index(self.working_dir)
        idx_payload.setdefault("recent", [])
        # Move this session to the front of recent[]
        recent = [
            s for s in idx_payload["recent"] if s.get("session_id") != self.session_id
        ]
        recent.insert(
            0,
            {
                "session_id": self.session_id,
                "started_at": self._meta["started_at"],
                "last_event_at": self._meta["last_event_at"],
                "status": self._meta["status"],
            },
        )
        idx_payload["recent"] = recent[:50]
        idx_payload["last_session_id"] = self.session_id
        idx_payload["active_session_id"] = self.session_id if active else None
        _atomic_write(self.index_path, idx_payload)


# ── Module-level helpers ──────────────────────────────────────────────────


def _generate_session_id() -> str:
    return uuid.uuid4().hex[:12]


def read_index(working_dir: str | Path) -> dict[str, Any]:
    """Read ``sessions/index.json`` (returns empty dict if missing)."""
    path = Path(working_dir) / INDEX_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def detect_crashed_sessions(working_dir: str | Path) -> list[dict[str, Any]]:
    """Find sessions whose ``meta.json`` is still ``status=active``.

    Called on CLI startup.  Returns one dict per crashed session with
    ``{session_id, started_at, last_event_at, event_count}``.  The caller
    should call :func:`mark_session_crashed` after the user decides what
    to do with each one.
    """
    sdlicit_root = Path(working_dir) / SDLICIT_DIR
    if not sdlicit_root.is_dir():
        return []

    crashed: list[dict[str, Any]] = []
    for session_dir in sdlicit_root.iterdir():
        if not session_dir.is_dir():
            continue
        meta_path = session_dir / META_FILE
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("status") == "active":
            crashed.append(
                {
                    "session_id": meta.get("session_id", session_dir.name),
                    "started_at": meta.get("started_at"),
                    "last_event_at": meta.get("last_event_at"),
                    "event_count": meta.get("event_count", 0),
                    "tokens": meta.get("tokens", {}).get("total_tokens", 0),
                    "meta_path": str(meta_path),
                }
            )
    return crashed


def mark_session_crashed(working_dir: str | Path, session_id: str) -> None:
    """Flip a stale session's status to ``crashed``."""
    meta_path = Path(working_dir) / SDLICIT_DIR / session_id / META_FILE
    if not meta_path.exists():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    meta["status"] = "crashed"
    _atomic_write(meta_path, meta)


@contextmanager
def journal_request(
    journal: Journal,
    endpoint: str,
    method: str,
    payload: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Convenience context manager: records request, yields ctx, finalises on exit.

    The body of the ``with`` block is responsible for calling
    :meth:`Journal.record_response` with the result.  If an exception
    escapes the block, an ``error`` response is recorded automatically.
    """
    ctx = journal.record_request(endpoint, method, payload)
    try:
        yield ctx
    except Exception as exc:  # noqa: BLE001 — we re-raise after logging
        journal.record_response(
            ctx,
            status_code=0,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

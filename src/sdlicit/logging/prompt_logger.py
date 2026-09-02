"""Full prompt/response logger for research reproducibility.

When ``log_prompts`` is enabled in the config, every LLM call is logged
as a JSONL entry containing:
- The full prompt text sent to the model
- The full response text received
- The DSPy signature name
- Token counts
- Timestamp and model name

Log entries are appended to ``.sdlicit/logs/prompts.jsonl`` in the project
directory.  This file grows large — enable only for research/audit runs.

The logger checks an app-scoped flag rather than importing config to
avoid circular dependencies.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sdlicit.logging import get_logger

_log = get_logger("tool")

# App-scoped state — set by bootstrap.create_system()
_enabled = False
_log_dir: Path | None = None
_lock = threading.Lock()


def configure(enabled: bool, log_dir: Path | None) -> None:
    """Called once at startup to set the prompt logging configuration."""
    global _enabled, _log_dir
    _enabled = enabled
    _log_dir = log_dir
    if enabled:
        _log.info("Prompt logging ENABLED → %s/prompts.jsonl", log_dir)


def log_prompt_exchange(
    *,
    signature: str,
    prompt: str,
    response: str,
    inputs: dict[str, Any] | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model: str | None = None,
) -> None:
    """Append a prompt/response entry to the prompts log file.

    No-op if prompt logging is disabled.
    """
    if not _enabled or not _log_dir:
        return

    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "signature": signature,
        "model": model or "unknown",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "prompt": prompt[:50000],  # cap at 50k chars to prevent runaway
        "response": response[:50000],
    }

    # Include signature input field names (not values — those are in prompt)
    if inputs:
        entry["input_fields"] = list(inputs.keys())

    try:
        with _lock:
            _log_dir.mkdir(parents=True, exist_ok=True)
            log_path = _log_dir / "prompts.jsonl"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        _log.debug("Failed to write prompt log entry", exc_info=True)

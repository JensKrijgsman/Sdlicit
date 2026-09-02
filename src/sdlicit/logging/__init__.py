"""Structured colour-coded logger for Sdlicit.

Every log line is prefixed with a coloured tag that identifies its origin:

    [AGENT]  — agent handle / process calls
    [TOOL]   — MCP tool invocations
    [ROUTE]  — FastAPI route entry / exit
    [MCP]    — MCP server-level events

Colours are applied via ANSI escape codes and work in any terminal that
supports 256-colour output (including Uvicorn's default stderr).

Usage::

    from sdlicit.logging import get_logger
    log = get_logger("route")
    log.info("POST /composing/step from CLI")
"""

from __future__ import annotations

import logging
import sys

# ANSI colour codes (256-colour mode)
_COLOURS: dict[str, str] = {
    "agent": "\033[1;35m",  # bold magenta
    "tool": "\033[1;36m",  # bold cyan
    "route": "\033[1;32m",  # bold green
    "mcp": "\033[1;34m",  # bold blue
}
_RESET = "\033[0m"

_TAGS: dict[str, str] = {
    "agent": "AGENT",
    "tool": "TOOL",
    "route": "ROUTE",
    "mcp": "MCP",
}


class _ColorFormatter(logging.Formatter):
    """Formatter that prepends a coloured ``[TAG]`` based on logger name."""

    def __init__(self, category: str) -> None:
        super().__init__()
        self._prefix = (
            f"{_COLOURS.get(category, '')}"
            f"[{_TAGS.get(category, category.upper())}]"
            f"{_RESET} "
        )

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        ts = self.formatTime(record, "%H:%M:%S")
        level = record.levelname[0]  # I / W / E / D
        return f"{ts} {level} {self._prefix}{record.getMessage()}"


def get_logger(category: str) -> logging.Logger:
    """Return a category-scoped logger with colour-coded output.

    Parameters
    ----------
    category:
        One of ``"agent"``, ``"tool"``, ``"route"``, ``"mcp"``.
    """
    name = f"sdlicit.{category}"
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_ColorFormatter(category))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    return logger

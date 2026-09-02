"""Composing stage tools — MADR parsing and summarisation utilities.

These are standalone helper functions. The main MCP tools are registered
in main.py on the single unified MCP server instance.
"""

from __future__ import annotations

from typing import Any

from sdlicit.helpers.parsers import parse_madr_content, summarise_adr


def parse_madr(content: str) -> dict[str, Any]:
    """Parse MADR 4.0.0 markdown into structured data."""
    return parse_madr_content(content)


def summarise(content: str) -> str:
    """Compact one-paragraph summary of an ADR for context windows."""
    return summarise_adr(content)

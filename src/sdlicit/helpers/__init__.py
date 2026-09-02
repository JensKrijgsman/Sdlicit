"""Shared helpers — artifact parsers and utilities."""

import re

from .parsers import parse_madr_content, summarise_adr

__all__ = ["parse_madr_content", "slugify", "summarise_adr"]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 60, fallback: str = "section") -> str:
    """Convert arbitrary text to a kebab-case slug."""
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s or fallback

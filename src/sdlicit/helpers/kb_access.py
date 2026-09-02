"""KB retrieval helper — eliminates duplicated router/fallback/join pattern.

Every agent that needs KB context repeats the same 8-line pattern:
  if router: query via router
  elif kb: query directly
  else: empty
  join text

This module extracts that into a single reusable function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sdlicit.logging import get_logger

if TYPE_CHECKING:
    from sdlicit.expansion_stage.tools.kb_router import KBRouter, StaticContextProvider
    from sdlicit.expansion_stage.tools.knowledge_base import KnowledgeBase

_log = get_logger("kb_access")


async def retrieve_context(
    query: str,
    *,
    kb_router: KBRouter | StaticContextProvider | None = None,
    kb: KnowledgeBase | None = None,
    mode: str = "hybrid",
    probe_first: bool = True,
    max_chars: int = 0,
) -> str:
    """Retrieve KB context with automatic router/KB fallback.

    Args:
        query: Natural language search query.
        kb_router: Store-aware router (preferred).
        kb: Raw KnowledgeBase (fallback if no router).
        mode: LightRAG search mode for raw KB queries.
        probe_first: Whether router should probe before querying.
        max_chars: If >0, truncate result to this many characters.

    Returns:
        Concatenated text chunks separated by double-newlines.
        Returns "" if both kb_router and kb are None, or on error.
    """
    chunks: list[Any] = []
    try:
        if kb_router is not None:
            chunks = await kb_router.query(query, probe_first=probe_first)
        elif kb is not None:
            chunks = await kb.query(query, mode=mode)
    except Exception as exc:
        _log.warning("KB query failed (continuing without): %s", exc)
        return ""

    text = "\n\n".join(c.text for c in chunks if c.text)
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
    return text

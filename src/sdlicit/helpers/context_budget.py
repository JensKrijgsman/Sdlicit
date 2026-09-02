"""Bounded, ranked prior artifact context for generation signatures.

Replaces the old pattern of concatenating the first N prior ADRs (sorted
oldest first) with: rank by relatedness to the topic at hand, keep the
most related in full while a token budget allows, summarise the rest,
and drop anything past the budget instead of growing prompts without
bound as a project accumulates ADRs.

The token budget reuses ``config.model_context_window`` and
``config.compact_threshold_pct`` — the same two fields ToM session
compaction already uses — so both subsystems share one configurable
notion of "context is getting large" instead of two disconnected ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sdlicit.helpers.parsers import parse_madr_content, summarise_adr
from sdlicit.logging.usage import estimate_tokens

_REQ_ID_RE = re.compile(r"REQ-(?:FUNC|NONFUNC)-\d+")
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "in", "on", "to", "with",
    "by", "is", "are", "was", "were", "be", "been",
    "use", "using", "choose", "select", "decision", "record",
})


@dataclass(frozen=True)
class ADRRecord:
    """A parsed prior ADR, ready to be ranked and budgeted."""

    content: str
    id: str
    title: str
    implements: tuple[str, ...]

    @property
    def sequence(self) -> int:
        """Numeric suffix of the id (``ADR-0012`` -> 12), 0 if unparseable.

        Used as a recency proxy: ids are assigned sequentially, so a
        higher number was written later. There is no reliable mtime for
        artifacts assembled in memory (e.g. a sequential replay run
        that has not written files yet), so id order is the one signal
        that works for both an on disk project and an in-memory run.
        """
        m = re.search(r"(\d+)$", self.id)
        return int(m.group(1)) if m else 0


def parse_adr_records(contents: list[str]) -> list[ADRRecord]:
    """Parse raw ADR markdown strings into :class:`ADRRecord` objects."""
    records = []
    for content in contents:
        data = parse_madr_content(content)
        records.append(
            ADRRecord(
                content=content,
                id=str(data.get("id") or ""),
                title=str(data.get("title") or ""),
                implements=tuple(data.get("implements") or ()),
            )
        )
    return records


def _tokens(text: str) -> set[str]:
    words = _WORD_RE.findall(text.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 2}


def _word_overlap_score(topic: str, record: ADRRecord) -> float:
    """Jaccard overlap between the topic and the record's title plus content."""
    topic_tokens = _tokens(topic)
    if not topic_tokens:
        return 0.0
    record_tokens = _tokens(f"{record.title} {record.content[:1000]}")
    if not record_tokens:
        return 0.0
    intersection = topic_tokens & record_tokens
    union = topic_tokens | record_tokens
    return len(intersection) / len(union) if union else 0.0


def _requirement_overlap_score(topic: str, record: ADRRecord) -> float:
    """1.0 if the record implements a requirement also mentioned in the topic, else 0."""
    if not record.implements:
        return 0.0
    topic_reqs = set(_REQ_ID_RE.findall(topic))
    if not topic_reqs:
        return 0.0
    return 1.0 if topic_reqs & set(record.implements) else 0.0


def rank_prior_adrs(topic: str, records: list[ADRRecord], limit: int = 10) -> list[ADRRecord]:
    """Rank *records* by relatedness to *topic*, most related first.

    Score is requirement overlap (shared ``implements`` ids with the
    topic text) first, word overlap second, and id sequence (recency)
    as the final tiebreaker. This replaces plain oldest first file
    order slicing, which silently favoured the earliest ADRs and
    dropped the newest ones once a project grew past the cap.
    """
    scored = [
        (
            _requirement_overlap_score(topic, r),
            _word_overlap_score(topic, r),
            r.sequence,
            r,
        )
        for r in records
    ]
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [r for *_scores, r in scored[:limit]]


def context_budget_tokens(model_context_window: int, compact_threshold_pct: float) -> int:
    """Token budget for prior artifact context, floored so it is never degenerate."""
    return max(500, int(model_context_window * compact_threshold_pct))


def assemble_context(
    records: list[ADRRecord],
    token_budget: int,
    model: str | None = None,
) -> tuple[str, int]:
    """Build bounded prior ADR context text from already ranked *records*.

    Includes full text for as many leading records as fit the budget,
    then switches to :func:`summarise_adr` output for the remainder
    while that still fits, then stops. Returns ``(text, dropped_count)``
    so callers can log or surface how much was left out instead of
    silently truncating.
    """
    if not records:
        return "", 0

    parts: list[str] = []
    used = 0
    dropped = 0
    summarising = False

    for record in records:
        piece = record.content if not summarising else summarise_adr(record.content)
        piece_tokens = estimate_tokens(piece, model)

        if used + piece_tokens > token_budget:
            if summarising:
                dropped += 1
                continue
            # Switch to summaries for the rest of the budget before giving up.
            summarising = True
            piece = summarise_adr(record.content)
            piece_tokens = estimate_tokens(piece, model)
            if used + piece_tokens > token_budget:
                dropped += 1
                continue

        parts.append(piece)
        used += piece_tokens

    return "\n\n---\n\n".join(parts), dropped

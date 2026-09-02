"""Socratic envelope helpers — shared conversion between transport and domain types.

Eliminates duplicated ClarificationItem→Clarification and SocraticProbe→SocraticProbeOut
conversions that appear in every service layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdlicit.agents.base import Clarification, SocraticProbe
    from sdlicit.agents.transport import ClarificationItem, SocraticProbeOut


def to_clarifications(items: list[ClarificationItem]) -> list[Clarification]:
    """Convert transport ClarificationItems to domain Clarification objects."""
    from sdlicit.agents.base import Clarification

    return [
        Clarification(question=c.question, answer=c.answer, turn=c.turn)
        for c in items
    ]


def to_probe_out(probe: SocraticProbe | None) -> SocraticProbeOut | None:
    """Convert a domain SocraticProbe to the transport SocraticProbeOut model.

    Returns None if probe is None.
    """
    if probe is None:
        return None

    from sdlicit.agents.transport import SocraticProbeOut

    return SocraticProbeOut(
        probe_id=probe.probe_id,
        question=probe.question,
        style=probe.style,
        originating_agent=probe.originating_agent,
        what_was_asked=probe.what_was_asked,
        turn=probe.turn,
        max_turns=probe.max_turns,
        rag_grounding=probe.rag_grounding,
        kb_facts=getattr(probe, "kb_facts", ""),
        transparency_events=list(getattr(probe, "transparency_events", [])),
    )

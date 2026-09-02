"""Canonical Socratic probe transport models shared across all stages."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ClarificationItem(BaseModel):
    """A single Q&A pair from a Socratic dialogue, sent back to resume."""

    question: str
    answer: str
    turn: int = 1


class SocraticProbeOut(BaseModel):
    """Probe surfaced by the agent -- the CLI prompts the user with this."""

    probe_id: str
    question: str
    style: Literal["assumption", "contradiction", "depth", "perspective"] = "depth"
    originating_agent: str = ""
    what_was_asked: str = ""
    turn: int = 1
    max_turns: int = 7
    rag_grounding: str = ""
    kb_facts: str = Field(
        default="",
        description="Raw KB facts to present to the user verbatim before the question.",
    )
    transparency_events: list[str] = Field(
        default_factory=list,
        description="Backend actions taken while generating this probe "
        "(e.g. 'Consulting Knowledge Base', 'Analyzing interaction history', "
        "'Dean review applied'). Render as status hints in the UI.",
    )

"""Agent base types — dataclass values + Protocol for polymorphism.

Every agent returns ``AgentResult`` — a plain immutable-style value.
``ToMConsultant`` is the polymorphic interface for Theory-of-Mind
consultation.

Rich Hickey's rule: program to abstractions, dispatch on data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Error hierarchy — explicit partial-failure reporting
# ---------------------------------------------------------------------------


class SdlicitError(Exception):
    """Base exception for all Sdlicit errors."""


class LLMError(SdlicitError):
    """LLM prediction failed (timeout, malformed output, rate limit)."""


class KBError(SdlicitError):
    """Knowledge base query or ingestion failed."""


class TraceError(SdlicitError):
    """Traceability analysis error."""

# ---------------------------------------------------------------------------
# Value types — plain data, no behaviour
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KBReference:
    """A single knowledge-base retrieval result."""

    source: str
    chunk: str
    relevance: float


@dataclass
class Suggestion:
    """One actionable suggestion from an agent."""

    field: str
    message: str
    severity: Literal["low", "medium", "high"]
    should_show: bool = True
    references: list[KBReference] = field(default_factory=list)


@dataclass
class Clarification:
    """A single Q&A pair from a Socratic dialogue.

    Sent by the CLI alongside the original request so the agent can
    re-run with the user's clarifications folded in.
    """

    question: str
    answer: str
    turn: int = 1


@dataclass
class SocraticProbe:
    """A thought-provoking question surfaced to the user.

    The agent stops mid-task and returns this so the CLI can prompt
    the user.  When the user answers, the CLI adds a ``Clarification``
    to the request and re-calls the same endpoint.
    """

    probe_id: str
    question: str
    style: Literal["assumption", "contradiction", "depth", "perspective"]
    originating_agent: str
    what_was_asked: str
    turn: int = 1
    max_turns: int = 7
    rag_grounding: str = ""
    kb_facts: str = ""
    transparency_events: list[str] = field(default_factory=list)


@dataclass
class ADRDirection:
    """One suggested ADR topic the user should consider writing.

    Returned by ``ADRAgent.suggest_directions``.  Carries WHAT decision
    needs to be made and WHY — never a proposed solution.
    """

    title: str
    rationale: str
    priority: Literal["high", "medium", "low"] = "medium"
    gap_filled: str = ""
    references: list[KBReference] = field(default_factory=list)


@dataclass
class AgentResult:
    """Uniform return type for every agent call.

    The ``errors`` field provides partial-failure transparency: an agent
    can return useful output *and* report that some steps failed (e.g.
    KB query failed but LLM still produced results).
    """

    suggestions: list[Suggestion] = field(default_factory=list)
    compliance_notes: str = ""
    tom_observation: str = ""
    raw_text: str = ""
    adr_directions: list[ADRDirection] = field(default_factory=list)
    socratic_probe: SocraticProbe | None = None
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Protocols — polymorphism through structural typing
# ---------------------------------------------------------------------------


@runtime_checkable
class ToMConsultant(Protocol):
    """Protocol for Theory-of-Mind consultation.

    Any agent that holds an optional ``tom`` reference can call
    ``consult()`` when uncertain about user intent.  The orchestrator
    injects the concrete ``ToMAgent`` at construction time.
    """

    async def consult(self, agent_query: str, current_context: str = "") -> Any: ...

    def observe(self, event_type: str, data: dict[str, Any]) -> None: ...

    def should_show(self, section: str, threshold: float = 0.5) -> bool: ...

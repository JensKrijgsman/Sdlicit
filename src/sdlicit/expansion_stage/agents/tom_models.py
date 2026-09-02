"""Pydantic models for the three-tier ToM memory system.

Tier 1 (Raw / ``chat/``)   — complete interaction logs per query/event.
Tier 2 (Session / ``sdlicit/``) — per-CLI-run extracted preferences & intents.
Tier 3 (User / ``user/``)  — aggregated cross-session user profile.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Tier 1 — Raw session storage  (sessions/chat/)
# ---------------------------------------------------------------------------


class RawInteraction(BaseModel):
    """A single timestamped event in the interaction log."""

    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    event_type: str = Field(
        desc="step_input | user_skip | user_engage | agent_suggestion | consult_tom | …"
    )
    agent: str = Field(default="", desc="Which agent produced/consumed this event")
    data: dict[str, Any] = Field(default_factory=dict)


class RawSession(BaseModel):
    """Complete interaction log for one CLI run or chat query (Tier 1)."""

    session_id: str
    started_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    ended_at: str | None = None
    stage: str = Field(
        default="unknown", desc="intake | composing | expansion | generation"
    )
    interactions: list[RawInteraction] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tier 2 — Session-based user model  (sessions/sdlicit/)
# ---------------------------------------------------------------------------


class SessionUserModel(BaseModel):
    """Extracted per-session analysis of user behaviour (Tier 2).

    Produced by the ``ToMSessionAnalysis`` DSPy signature after a
    session ends.
    """

    session_id: str
    analysed_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    inferred_intents: list[str] = Field(default_factory=list)
    interaction_patterns: list[str] = Field(default_factory=list)
    preferences: dict[str, str] = Field(default_factory=dict)
    emotional_states: list[str] = Field(default_factory=list)
    confidence_per_section: dict[str, float] = Field(default_factory=dict)
    scaffolding_level: Literal["minimal", "moderate", "detailed"] = "moderate"


# ---------------------------------------------------------------------------
# Tier 3 — Overall user model  (sessions/user/)
# ---------------------------------------------------------------------------


class UserModel(BaseModel):
    """Aggregated cross-session user profile (Tier 3).

    Updated by ``ToMUserModelUpdate`` after each session analysis.
    A single ``user_model.json`` file lives in ``sessions/user/``.
    """

    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    preference_clusters: dict[str, list[str]] = Field(
        default_factory=dict,
        desc="Grouped preferences, e.g. {'documentation': ['prefers concise ADRs', …]}",
    )
    interaction_style: str = Field(
        default="unknown",
        desc="e.g. 'direct', 'exploratory', 'thorough'",
    )
    scaffolding_preference: Literal["minimal", "moderate", "detailed"] = "moderate"
    expertise_areas: list[str] = Field(default_factory=list)
    common_patterns: list[str] = Field(
        default_factory=list,
        desc="Recurring behaviours observed across sessions",
    )
    session_count: int = 0


# ---------------------------------------------------------------------------
# ToM consultation result (returned by consult_tom tool)
# ---------------------------------------------------------------------------


class ToMRecommendation(BaseModel):
    """What the ToM agent tells a consulting agent."""

    observation: str = Field(desc="Summary of the user's inferred mental state")
    recommendation: str = Field(desc="Suggested behavioural adjustment for the agent")
    scaffolding_level: Literal["minimal", "moderate", "detailed"] = "moderate"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    inferred_goals: list[str] = Field(default_factory=list)

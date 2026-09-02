"""Theory of Mind Agent — cross-cutting user-model service.

Exposes two tools consumable by any Sdlicit subagent (and via MCP):

* **consult** (in-session) — an agent queries the ToM when uncertain
  about user intent, frustration, or desired scaffolding level.
* **update_memory** (after-session) — processes the completed session
  through the three-tier hierarchical memory:
      Tier 1 (chat/)    — raw interaction log
      Tier 2 (sdlicit/) — per-session extracted user model
      Tier 3 (user/)    — aggregated cross-session profile

Fast rule-based ``observe()`` still runs inline for instant confidence
tracking; the LLM-based tools are called on-demand or at boundaries.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dspy

from sdlicit.agents.base import AgentResult
from sdlicit.agents.llm.dspy_modules import (
    ToMAssessment,
    ToMChatObservation,
    ToMConsultation,
    ToMGiveSuggestions,
    ToMSessionAnalysis,
    ToMUserModelUpdate,
)
from sdlicit.agents.llm.gateway import LLMGateway
from sdlicit.expansion_stage.agents.tom_memory import ToMMemory
from sdlicit.expansion_stage.agents.tom_models import (
    RawInteraction,
    RawSession,
    SessionUserModel,
    ToMRecommendation,
    UserModel,
)
from sdlicit.logging import get_logger

_log = get_logger("tom")


# ---------------------------------------------------------------------------
# In-memory state (lives for one CLI run / API session)
# ---------------------------------------------------------------------------


@dataclass
class ToMState:
    """Per-session Theory of Mind state (kept in memory)."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    confidence: dict[str, float] = field(default_factory=dict)
    interactions: list[dict[str, Any]] = field(default_factory=list)
    inferred_goals: list[str] = field(default_factory=list)
    scaffolding_level: str = "moderate"
    stage: str = "unknown"
    # Proactive guidance for the Socratic Solicitor (updated after each answer)
    active_guidance: str = ""
    needs_dean: bool = False
    _seq: int = field(default=0, repr=False)

    def next_seq(self) -> int:
        """Return and increment the interaction sequence number."""
        seq = self._seq
        self._seq += 1
        return seq


# ---------------------------------------------------------------------------
# ToMAgent
# ---------------------------------------------------------------------------


class ToMAgent:
    """Cross-cutting Theory of Mind agent with three-tier memory.

    Lifecycle::

        1. Orchestrator creates ToMAgent at startup.
        2. ``start_session(stage)`` — begins a new raw session.
        3. During the session, other agents call:
           - ``observe()``  for fast rule-based tracking
           - ``consult()``  for LLM-based intent inference (MCP tool)
           - ``should_show()`` to gate suggestions
        4. ``assess()`` at stage transitions (existing behaviour).
        5. ``end_session()`` — persists raw log + runs after-session
           analysis + updates the user model.
    """

    def __init__(
        self,
        llm: LLMGateway,
        chat_sessions_dir: Path | None = None,
        sdlicit_sessions_dir: Path | None = None,
        user_dir: Path | None = None,
        focus: str = "all",
    ) -> None:
        self._llm = llm
        self._state = ToMState()
        self._session_lock = asyncio.Lock()
        self._focus = focus  # "all" | "scaffolding" | "frustration" | "preferences"

        # Wire up the memory manager (gracefully handles None dirs)
        if chat_sessions_dir and sdlicit_sessions_dir and user_dir:
            self._memory: ToMMemory | None = ToMMemory(
                chat_dir=chat_sessions_dir,
                sdlicit_dir=sdlicit_sessions_dir,
                user_dir=user_dir,
            )
        else:
            self._memory = None

        self._raw_session: RawSession | None = None

    # -- Properties ------------------------------------------------------------

    @property
    def state(self) -> ToMState:
        return self._state

    @property
    def memory(self) -> ToMMemory | None:
        return self._memory

    # -- Session lifecycle -----------------------------------------------------

    def start_session(self, stage: str = "unknown") -> str:
        """Begin a new session.  Returns the session ID."""
        self._state = ToMState(stage=stage)
        self._raw_session = RawSession(
            session_id=self._state.session_id,
            stage=stage,
        )
        self._session_lock = asyncio.Lock()
        _log.info("ToM session started id=%s stage=%s", self._state.session_id, stage)
        return self._state.session_id

    async def end_session(self) -> dict[str, Any]:
        """End the current session.

        Returns a dict with the **raw session** (Tier 1), the **session
        analysis** (Tier 2) and the **updated user model** (Tier 3).

        Result schema::

            {
                "session_id": str,
                "raw_session": RawSession.model_dump(),
                "session_model": SessionUserModel.model_dump() | None,
                "user_model": UserModel.model_dump() | None,
            }
        """
        out: dict[str, Any] = {
            "session_id": self._state.session_id,
            "raw_session": None,
            "session_model": None,
            "user_model": None,
        }

        if self._raw_session is None:
            _log.warning("end_session called but no active session")
            return out

        async with self._session_lock:
            # Finalise raw session
            self._raw_session.ended_at = datetime.now(timezone.utc).isoformat()
            self._raw_session.interactions = [
                RawInteraction(
                    event_type=i.get("event", "unknown"),
                    agent=i.get("agent", ""),
                    data={k: v for k, v in i.items() if k not in ("event", "agent")},
                )
                for i in self._state.interactions
            ]
            out["raw_session"] = self._raw_session.model_dump()

            if self._memory is None:
                _log.info("No memory dirs configured — skipping analysis")
                return out

            # Tier 1: persist raw session
            self._memory.save_raw_session(
                self._state.session_id, self._raw_session.model_dump()
            )

            # Tier 2: LLM-based session analysis
            session_model = await self._analyze_session(self._raw_session)
            if session_model is not None:
                out["session_model"] = session_model.model_dump()
                self._memory.save_session_model(
                    self._state.session_id, session_model.model_dump()
                )

                # Tier 3: compute updated aggregated user model
                updated = await self._compute_user_model_update(session_model)
                if updated is not None:
                    out["user_model"] = updated.model_dump()
                    self._memory.save_user_model(updated.model_dump())

        _log.info("ToM session ended id=%s", self._state.session_id)
        return out

    # -- Tool: compact (mid-session token-budget compaction) ------------------

    async def compact_session(self) -> dict[str, Any] | None:
        """LLM-summarise the current session interactions into a compact form.

        Used by the CLI when the running token total approaches the
        configured threshold (``compact_threshold_pct`` of
        ``model_context_window``).  Returns ``None`` if there is no
        active session or no interactions to summarise.
        """
        if self._raw_session is None or not self._state.interactions:
            return None

        async with self._session_lock:
            # Take a snapshot — interactions may continue accumulating.
            snapshot = RawSession(
                session_id=self._raw_session.session_id,
                stage=self._raw_session.stage,
                started_at=self._raw_session.started_at,
                ended_at=datetime.now(timezone.utc).isoformat(),
                interactions=[
                    RawInteraction(
                        event_type=i.get("event", "unknown"),
                        agent=i.get("agent", ""),
                        data={k: v for k, v in i.items() if k not in ("event", "agent")},
                    )
                    for i in self._state.interactions
                ],
            )

            session_model = await self._analyze_session(snapshot)
            if session_model is None:
                return None

            result = {
                "session_id": self._state.session_id,
                "compacted_at": datetime.now(timezone.utc).isoformat(),
                "interaction_count": len(snapshot.interactions),
                "summary": session_model.model_dump(),
            }

            # Persist so build_context_summary can use it
            if self._memory is not None:
                self._memory.save_compact(self._state.session_id, result)

            return result

    # -- Tool 1: observe (fast, rule-based) ------------------------------------

    def observe(self, event_type: str, data: dict[str, Any]) -> None:
        """Rule-based observation — instant, no LLM call."""
        self._state.interactions.append({"event": event_type, **data})

        # Tier 1 journaling: append to disk
        if self._memory is not None:
            seq = self._state.next_seq()
            self._memory.append_raw_interaction(
                self._state.session_id, seq, event_type, data
            )

        field_name = data.get("field", "")
        if not field_name:
            return

        if event_type == "step_input":
            value = data.get("value", "")
            char_count = len(str(value))
            current = self._state.confidence.get(field_name, 0.5)
            if char_count > 100:
                self._state.confidence[field_name] = min(1.0, current + 0.1)
            elif char_count < 20:
                self._state.confidence[field_name] = max(0.0, current - 0.1)

        elif event_type == "user_skip":
            current = self._state.confidence.get(field_name, 0.5)
            self._state.confidence[field_name] = min(1.0, current + 0.15)

        elif event_type == "user_engage":
            # Lower competence ⇒ user needs help; engagement signals uncertainty
            current = self._state.confidence.get(field_name, 0.5)
            self._state.confidence[field_name] = max(0.0, current - 0.1)

    def should_show(self, section: str, threshold: float = 0.5) -> bool:
        """Return True if the agent should show a suggestion for this section."""
        return self._state.confidence.get(section, 0.5) < threshold

    # -- Tool 2: consult (in-session, LLM-based) — MCP-exposed ----------------

    async def consult(
        self,
        signature: type[dspy.Signature],
        **kwargs: str | int,
    ) -> ToMRecommendation:
        """In-session consultation using a typed DSPy signature.

        Each agent passes its own per-scenario signature (e.g.
        ``ToMADRStepConsult``) with structured inputs.  This method
        automatically injects ``user_model`` and ``session_history``.

        For MCP callers that send a freeform query, use
        ``consult_freeform()`` instead (wraps ``ToMConsultation``).
        """
        sig_name = signature.__name__
        _log.info("consult  signature=%s focus=%s", sig_name, self._focus)

        # Auto-inject memory context into kwargs
        if self._memory is not None:
            kwargs.setdefault(
                "user_model", self._memory.load_user_model().model_dump_json()
            )
            history = self._memory.build_context_summary()
            if self._focus != "all":
                history = f"[ToM focus: {self._focus}]\n{history}"
            kwargs.setdefault("session_history", history)
        else:
            kwargs.setdefault("user_model", UserModel().model_dump_json())
            kwargs.setdefault("session_history", "No prior session data.")

        # Stringify all values for DSPy
        str_kwargs = {k: str(v) for k, v in kwargs.items()}

        result = await self._llm.predict(signature, **str_kwargs)

        rec = getattr(result, "recommendation", None)
        if rec is None:
            return ToMRecommendation(
                observation="Unable to assess",
                recommendation="Proceed with moderate scaffolding",
            )

        # Record the consultation as an interaction
        self.observe("consult_tom", {"signature": sig_name})

        recommendation = ToMRecommendation(
            observation=getattr(rec, "observation", str(rec)),
            recommendation=getattr(rec, "recommendation", ""),
            scaffolding_level=getattr(rec, "scaffolding_level", "moderate"),
            confidence=getattr(rec, "confidence", 0.5),
            inferred_goals=getattr(rec, "inferred_goals", []),
        )

        # Update in-memory state from consultation
        self._state.scaffolding_level = recommendation.scaffolding_level
        self._state.inferred_goals = recommendation.inferred_goals

        return recommendation

    async def consult_freeform(
        self,
        agent_query: str,
        current_context: str = "",
    ) -> ToMRecommendation:
        """Freeform consultation — for MCP callers that send a text query.

        Wraps the generic ``ToMConsultation`` signature.  In-process
        agents should prefer ``consult()`` with a typed signature.
        """
        _log.info("consult_freeform  query=%s", agent_query[:80])

        # Gather memory context
        if self._memory is not None:
            user_model_json = self._memory.load_user_model().model_dump_json()
            history_summary = self._memory.build_context_summary()
        else:
            user_model_json = UserModel().model_dump_json()
            history_summary = "No prior session data."

        # Add recent in-session interactions to context
        recent = json.dumps(self._state.interactions[-15:])
        full_context = f"{current_context}\n\nRecent interactions:\n{recent}"

        result = await self._llm.predict(
            ToMConsultation,
            agent_query=agent_query,
            current_context=full_context,
            user_model=user_model_json,
            session_history=history_summary,
        )

        rec = getattr(result, "recommendation", None)
        if rec is None:
            return ToMRecommendation(
                observation="Unable to assess",
                recommendation="Proceed with moderate scaffolding",
            )

        self.observe("consult_tom", {"query": agent_query, "agent": "mcp_caller"})

        recommendation = ToMRecommendation(
            observation=getattr(rec, "observation", str(rec)),
            recommendation=getattr(rec, "recommendation", ""),
            scaffolding_level=getattr(rec, "scaffolding_level", "moderate"),
            confidence=getattr(rec, "confidence", 0.5),
            inferred_goals=getattr(rec, "inferred_goals", []),
        )

        self._state.scaffolding_level = recommendation.scaffolding_level
        self._state.inferred_goals = recommendation.inferred_goals

        return recommendation

    # -- Tool 3: update_memory (after-session) — MCP-exposed ------------------

    async def update_memory(self, session_id: str | None = None) -> dict[str, Any]:
        """After-session memory update — processes raw session into Tier 2/3.

        The backend computes the analysis but does not write anything;
        the result is returned to the CLI which persists it.
        """
        if self._memory is None:
            return {"status": "skipped", "reason": "no memory dirs configured"}

        # Load the raw session
        target_id = session_id or self._state.session_id
        raw = self._memory.load_raw_session(target_id)
        if raw is None and self._raw_session is not None:
            raw = self._raw_session

        if raw is None:
            return {"status": "error", "reason": f"session {target_id} not found"}

        # Run analysis + compute updated user model
        session_model = await self._analyze_session(raw)
        if session_model is None:
            return {"status": "error", "reason": "session analysis failed"}

        updated_user = await self._compute_user_model_update(session_model)

        return {
            "status": "ok",
            "session_id": target_id,
            "session_model": session_model.model_dump(),
            "user_model": updated_user.model_dump() if updated_user else None,
            "intents": session_model.inferred_intents,
            "scaffolding": session_model.scaffolding_level,
        }

    # -- Legacy: assess (stage-transition assessment) --------------------------

    async def assess(self, agent_outputs: list[dict[str, str]]) -> AgentResult:
        """Deep LLM-based assessment — run at stage transitions."""
        result = await self._llm.predict(
            ToMAssessment,
            user_interactions=json.dumps(self._state.interactions[-20:]),
            agent_outputs=json.dumps(agent_outputs),
        )

        assessment = getattr(result, "assessment", None)
        if assessment is None:
            return AgentResult()

        self._state.scaffolding_level = getattr(
            assessment, "scaffolding_level", "moderate"
        )
        self._state.inferred_goals = getattr(assessment, "user_goals", [])

        return AgentResult(
            tom_observation=getattr(assessment, "observation", str(assessment)),
        )

    # -- Internal: session analysis (Tier 1 → Tier 2) -------------------------

    async def _analyze_session(self, raw: RawSession) -> SessionUserModel | None:
        """LLM-analyses a raw session to extract preferences and intents."""
        if not raw.interactions:
            _log.info("Empty session %s — skipping analysis", raw.session_id)
            return None

        user_model = self._memory.load_user_model() if self._memory else UserModel()
        result = await self._llm.predict(
            ToMSessionAnalysis,
            raw_session=raw.model_dump_json(),
            previous_user_model=user_model.model_dump_json(),
        )

        analysis = getattr(result, "analysis", None)
        if analysis is None:
            _log.warning("Session analysis returned None for %s", raw.session_id)
            return None

        return SessionUserModel(
            session_id=raw.session_id,
            inferred_intents=getattr(analysis, "inferred_intents", []),
            interaction_patterns=getattr(analysis, "interaction_patterns", []),
            preferences=getattr(analysis, "preferences", {}),
            emotional_states=getattr(analysis, "emotional_states", []),
            confidence_per_section=dict(self._state.confidence),
            scaffolding_level=getattr(analysis, "scaffolding_level", "moderate"),
        )

    # -- Internal: user model update (Tier 2 → Tier 3) ------------------------

    async def _compute_user_model_update(
        self, session_model: SessionUserModel
    ) -> UserModel | None:
        """Aggregate a session model into the overall user profile.

        Returns the new :class:`UserModel`; the caller is responsible
        for persisting it (typically the CLI).
        """
        if self._memory is None:
            return None

        current = self._memory.load_user_model()

        result = await self._llm.predict(
            ToMUserModelUpdate,
            current_user_model=current.model_dump_json(),
            new_session_analysis=session_model.model_dump_json(),
        )

        updated = getattr(result, "updated_model", None)
        if updated is None:
            _log.warning("User model update returned None")
            return None

        new_model = UserModel(
            preference_clusters=getattr(
                updated, "preference_clusters", current.preference_clusters
            ),
            interaction_style=getattr(
                updated, "interaction_style", current.interaction_style
            ),
            scaffolding_preference=getattr(
                updated, "scaffolding_preference", current.scaffolding_preference
            ),
            expertise_areas=getattr(
                updated, "expertise_areas", current.expertise_areas
            ),
            common_patterns=getattr(
                updated, "common_patterns", current.common_patterns
            ),
            session_count=current.session_count + 1,
        )

        _log.info(
            "User model computed — sessions=%d style=%s scaffolding=%s",
            new_model.session_count,
            new_model.interaction_style,
            new_model.scaffolding_preference,
        )
        return new_model

    # -- Tool: observe_chat (passive chat observation) -------------------------

    async def observe_chat(
        self,
        user_message: str,
        assistant_response: str,
        chat_history: list[dict[str, str]],
    ) -> ToMRecommendation:
        """Passively observe a chat exchange and update ToM state.

        Called after every chat message exchange.  Uses the
        ``ToMChatObservation`` DSPy signature to infer emotional state,
        satisfaction, and whether a follow-up is needed.
        """
        _log.info(
            "observe_chat  msg_len=%d resp_len=%d",
            len(user_message),
            len(assistant_response),
        )

        # Record the interaction
        self.observe(
            "chat_message",
            {
                "agent": "chat",
                "user_len": len(user_message),
                "response_len": len(assistant_response),
            },
        )

        # Build context
        history_summary = "\n".join(
            f"{h.get('role', 'unknown')}: {h.get('content', '')[:200]}"
            for h in chat_history[-5:]
        )

        if self._memory is not None:
            user_model_json = self._memory.load_user_model().model_dump_json()
            session_summary = self._memory.build_context_summary()
        else:
            user_model_json = UserModel().model_dump_json()
            session_summary = "No prior session data."

        result = await self._llm.predict(
            ToMChatObservation,
            user_message=user_message,
            assistant_response=assistant_response,
            chat_history_summary=history_summary,
            user_model=user_model_json,
            session_history=session_summary,
        )

        obs = getattr(result, "observation", None)
        if obs is None:
            return ToMRecommendation(
                observation="Unable to assess chat exchange",
                recommendation="Continue normally",
            )

        rec = ToMRecommendation(
            observation=getattr(obs, "observation", str(obs)),
            recommendation=getattr(obs, "recommendation", ""),
            scaffolding_level=getattr(obs, "scaffolding_level", "moderate"),
            confidence=getattr(obs, "confidence", 0.5),
            inferred_goals=getattr(obs, "inferred_goals", []),
        )

        # Update state
        self._state.scaffolding_level = rec.scaffolding_level
        self._state.inferred_goals = rec.inferred_goals

        return rec

    async def give_suggestions(
        self,
        chat_history: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Proactively suggest a Socratic probe based on observed state.

        Implements the ``give_suggestions`` action from the ToM-SWE paper.
        Returns a dict with:
        - ``should_probe``: bool
        - ``probe_question``: str (if should_probe)
        - ``probe_style``: str
        - ``satisfaction_score``: float
        - ``reasoning``: str
        """
        _log.info("give_suggestions  history_len=%d", len(chat_history))

        recent = "\n---\n".join(
            f"{h.get('role', 'unknown')}: {h.get('content', '')[:300]}"
            for h in chat_history[-5:]
        )

        if self._memory is not None:
            user_model_json = self._memory.load_user_model().model_dump_json()
        else:
            user_model_json = UserModel().model_dump_json()

        current_state = json.dumps(
            {
                "scaffolding_level": self._state.scaffolding_level,
                "confidence": dict(self._state.confidence),
                "inferred_goals": self._state.inferred_goals,
            }
        )

        result = await self._llm.predict(
            ToMGiveSuggestions,
            recent_exchanges=recent,
            user_model=user_model_json,
            current_state=current_state,
        )

        suggestion = getattr(result, "suggestion", None)
        if suggestion is None:
            return {
                "should_probe": False,
                "probe_question": "",
                "probe_style": "",
                "satisfaction_score": 0.5,
                "reasoning": "Unable to assess",
            }

        return {
            "should_probe": getattr(suggestion, "should_probe", False),
            "probe_question": getattr(suggestion, "probe_question", ""),
            "probe_style": getattr(suggestion, "probe_style", "clarification"),
            "satisfaction_score": getattr(suggestion, "satisfaction_score", 0.5),
            "reasoning": getattr(suggestion, "reasoning", ""),
            "needs_dean": getattr(suggestion, "needs_dean", False),
            "socratic_guidance": getattr(suggestion, "socratic_guidance", ""),
        }

    # -- Proactive guidance for Socratic Solicitor ----------------------------

    async def evaluate_and_get_guidance(
        self,
        clarifications: list[Any],
    ) -> tuple[str, bool]:
        """Evaluate recent Socratic exchanges and return actionable guidance.

        Called by the Socratic Solicitor at the start of each ``consult()``
        call so that the ToM's session-specific observations are incorporated
        into the next probe.

        Returns ``(guidance_str, needs_dean)``:
        - ``guidance_str``: Direct instruction for the Solicitor. Empty string
          means no adaptation needed — use defaults.
        - ``needs_dean``: Whether the Dean should review the draft probe
          before it is shown to the user.

        On the first turn (no clarifications yet) the long-term user model
        is used to seed the guidance with known preferences.
        """
        # First turn: inject long-term preferences from Tier-3 user model
        if not clarifications:
            if self._memory is not None:
                try:
                    user_model = self._memory.load_user_model()
                    parts: list[str] = []
                    if user_model.scaffolding_preference != "moderate":
                        parts.append(
                            f"scaffolding preference: {user_model.scaffolding_preference}"
                        )
                    if user_model.expertise_areas:
                        parts.append(
                            f"expertise areas: {', '.join(user_model.expertise_areas[:3])}"
                        )
                    if (
                        user_model.interaction_style
                        and user_model.interaction_style != "unknown"
                    ):
                        parts.append(
                            f"interaction style: {user_model.interaction_style}"
                        )
                    if parts:
                        guidance = (
                            "User profile from prior sessions — "
                            + "; ".join(parts)
                            + "."
                        )
                        self._state.active_guidance = guidance
                        _log.info(
                            "evaluate_and_get_guidance: seeded from user model turn=0"
                        )
                        return guidance, False
                except Exception as exc:
                    _log.debug(
                        "evaluate_and_get_guidance: user model load failed: %s", exc
                    )
            return self._state.active_guidance, self._state.needs_dean

        # Build a minimalist chat-history from recent Socratic turns
        chat_history: list[dict[str, str]] = []
        for c in clarifications[-3:]:
            if hasattr(c, "question"):
                chat_history.append({"role": "assistant", "content": c.question})
                chat_history.append({"role": "user", "content": c.answer})
            elif isinstance(c, dict):
                chat_history.append(
                    {"role": "assistant", "content": c.get("question", "")}
                )
                chat_history.append({"role": "user", "content": c.get("answer", "")})

        if not chat_history:
            return self._state.active_guidance, self._state.needs_dean

        try:
            suggestion = await self.give_suggestions(chat_history)
        except Exception as exc:
            _log.warning("evaluate_and_get_guidance: give_suggestions failed: %s", exc)
            return self._state.active_guidance, self._state.needs_dean

        guidance = suggestion.get("socratic_guidance", "")
        # Fall back to reasoning if no explicit guidance was generated
        if not guidance:
            reasoning = suggestion.get("reasoning", "")
            if reasoning and suggestion.get("satisfaction_score", 0.5) < 0.35:
                guidance = reasoning

        needs_dean = bool(suggestion.get("needs_dean", False))

        self._state.active_guidance = guidance
        self._state.needs_dean = needs_dean
        _log.info(
            "evaluate_and_get_guidance: needs_dean=%s guidance=%s",
            needs_dean,
            guidance[:80] if guidance else "(none)",
        )
        return guidance, needs_dean

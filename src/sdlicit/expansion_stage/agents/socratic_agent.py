"""Socratic Solicitor — cross-cutting consultation service.

Two roles:

1. **Cross-cutting consultation** (new): any agent (currently
   ``ADRAgent.review_step`` and ``SOWAgent.create_from_brief``) can
   ask the Socratic agent to:
   * judge whether user input is adequate (``judge_input``);
   * judge whether an LLM output is grounded (``judge_output``);
   * generate a thought-provoking probe (``consult``);
   * decide whether the user has reflected enough (``judge_resolution``).

2. **Post-hoc section review** (existing): expansion-stage step
   that asks Socratic follow-up questions about a completed section
   via :pyfunc:`review_section`.

Style rule (Socratic method): probes NEVER propose solutions or
candidates.  They expose hidden assumptions, surface contradictions,
probe quickly-made decisions, or raise unconsidered perspectives.
ToM context is used to calibrate depth and detect resolution / fatigue.

Each probe + answer is logged through ``tom.observe('socratic_qa', …)``
so the dialogue feeds into the three-tier user model.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Literal

from sdlicit.agents.base import (
    AgentResult,
    Clarification,
    KBReference,
    SocraticProbe,
    Suggestion,
)
from sdlicit.agents.llm.dspy_modules import (
    DeanReview,
    InputAdequacyJudge,
    OutputGroundingJudge,
    SocraticProbeGeneration,
    SocraticResolutionJudge,
    SocraticReview,
)
from sdlicit.agents.llm.gateway import LLMGateway
from sdlicit.logging import get_logger

if TYPE_CHECKING:
    from sdlicit.expansion_stage.agents.tom_agent import ToMAgent, ToMState
    from sdlicit.expansion_stage.tools.kb_router import KBRouter
    from sdlicit.expansion_stage.tools.knowledge_base import KnowledgeBase

_log = get_logger("agent")


# Heuristics for the "hybrid" judge mode --------------------------------------

_INPUT_TOO_SHORT = 30  # chars — likely under-specified
_INPUT_CLEARLY_ADEQUATE = 400  # chars — long enough to skip judging
_OUTPUT_GROUND_SKIP = 4000  # chars — output too long to bother judging cheaply


JudgeMode = Literal["dspy", "hybrid", "heuristic"]


class SocraticAgent:
    """Cross-cutting Socratic consultation + post-hoc section review."""

    def __init__(
        self,
        llm: LLMGateway,
        kb: KnowledgeBase | None,
        tom: ToMAgent | None = None,
        judge_mode: JudgeMode = "hybrid",
        max_turns: int = 7,
        kb_router: KBRouter | None = None,
    ) -> None:
        self._llm = llm
        self._kb = kb
        self._tom = tom
        self._judge_mode: JudgeMode = judge_mode
        self._max_turns = max_turns
        self._kb_router = kb_router

    # -- Helpers ---------------------------------------------------------------

    def _user_model_json(self) -> str:
        if self._tom is not None and self._tom.memory is not None:
            try:
                return self._tom.memory.load_user_model().model_dump_json()
            except Exception:
                return "{}"
        return "{}"

    @staticmethod
    def _format_history(clarifications: list[Clarification]) -> str:
        if not clarifications:
            return ""
        return "\n".join(
            f"Q{c.turn}: {c.question}\nA{c.turn}: {c.answer}" for c in clarifications
        )

    async def _kb_lookup(self, query: str) -> tuple[str, list[KBReference]]:
        if not query.strip():
            return "", []
        # Prefer KBRouter for store-aware retrieval with probing
        if self._kb_router is not None:
            chunks = await self._kb_router.query(query[:200], probe_first=True)
            text = "\n".join(c.text for c in chunks if c.text)
            refs = [
                KBReference(source=c.source, chunk=c.text, relevance=c.relevance)
                for c in chunks
            ]
            return text, refs
        if self._kb is None:
            return "", []
        chunks = await self._kb.query(query[:200])
        text = "\n".join(c.text for c in chunks if c.text)
        refs = [
            KBReference(source=c.source, chunk=c.text, relevance=c.relevance)
            for c in chunks
        ]
        return text, refs

    # -- Cross-cutting: input adequacy ----------------------------------------

    async def judge_input(
        self,
        agent_name: str,
        what_was_asked: str,
        user_input: str,
        prior_context: str = "",
    ) -> tuple[bool, list[str]]:
        """Return ``(adequate, missing_facts)``."""
        text = user_input.strip()

        # heuristic short-circuits
        if self._judge_mode == "heuristic":
            return (len(text) >= _INPUT_TOO_SHORT, [])
        if self._judge_mode == "hybrid":
            if len(text) >= _INPUT_CLEARLY_ADEQUATE:
                return True, []
            if len(text) < _INPUT_TOO_SHORT and not prior_context:
                return False, []  # let the LLM probe figure out specifics

        try:
            result = await self._llm.predict(
                InputAdequacyJudge,
                agent_name=agent_name,
                what_was_asked=what_was_asked,
                user_input=user_input,
                prior_context=prior_context,
                user_model=self._user_model_json(),
            )
            verdict = getattr(result, "verdict", None)
            if verdict is None:
                return True, []
            return (
                bool(getattr(verdict, "adequate", True)),
                list(getattr(verdict, "missing_facts", [])),
            )
        except Exception as exc:
            _log.debug("[SocraticAgent] judge_input failed: %s", exc)
            return True, []  # fail-open: don't block on judge errors

    # -- Cross-cutting: output grounding --------------------------------------

    async def judge_output(
        self,
        user_input: str,
        llm_output: str,
        kb_context: str = "",
    ) -> tuple[bool, list[str]]:
        """Return ``(grounded, ungrounded_claims)``."""
        if self._judge_mode == "heuristic":
            return True, []  # no LLM-based grounding check
        if self._judge_mode == "hybrid" and len(llm_output) > _OUTPUT_GROUND_SKIP:
            return True, []  # cost guard

        try:
            result = await self._llm.predict(
                OutputGroundingJudge,
                user_input=user_input,
                kb_context=kb_context,
                llm_output=llm_output,
            )
            verdict = getattr(result, "verdict", None)
            if verdict is None:
                return True, []
            return (
                bool(getattr(verdict, "grounded", True)),
                list(getattr(verdict, "ungrounded_claims", [])),
            )
        except Exception as exc:
            _log.debug("[SocraticAgent] judge_output failed: %s", exc)
            return True, []

    # -- Cross-cutting: resolution check --------------------------------------

    async def judge_resolution(
        self,
        what_was_asked: str,
        clarifications: list[Clarification],
    ) -> tuple[bool, bool]:
        """Return ``(resolved, fatigue_detected)``.

        Used for early-exit from multi-turn Socratic dialogues.
        """
        if not clarifications:
            return False, False

        last = clarifications[-1].answer.strip()
        if len(last) <= 2 or last.lower() in {"idk", "skip", "stop", "n/a", "no"}:
            return True, True

        if self._judge_mode == "heuristic":
            return False, False

        try:
            result = await self._llm.predict(
                SocraticResolutionJudge,
                what_was_asked=what_was_asked,
                conversation_history=self._format_history(clarifications),
                user_model=self._user_model_json(),
            )
            verdict = getattr(result, "verdict", None)
            if verdict is None:
                return False, False
            return (
                bool(getattr(verdict, "resolved", False)),
                bool(getattr(verdict, "fatigue_detected", False)),
            )
        except Exception as exc:
            _log.debug("[SocraticAgent] judge_resolution failed: %s", exc)
            return False, False

    # -- Cross-cutting: probe generation --------------------------------------

    async def consult(
        self,
        originating_agent: str,
        what_was_asked: str,
        what_is_known: str,
        clarifications: list[Clarification] | None = None,
        suspect_output: str = "",
        issue: Literal["ambiguous_input", "ungrounded_output"] = "ambiguous_input",
        missing_facts: list[str] | None = None,
        ungrounded_claims: list[str] | None = None,
    ) -> SocraticProbe | None:
        """Generate one Socratic probe.  Returns ``None`` if the max-turn
        limit has been reached or the user appears resolved / fatigued."""
        clarifications = clarifications or []
        missing_facts = missing_facts or []
        ungrounded_claims = ungrounded_claims or []

        turn = len(clarifications) + 1
        if turn > self._max_turns:
            _log.info(
                "[SocraticAgent] max_turns=%d reached -- stopping", self._max_turns
            )
            return None

        if clarifications:
            resolved, fatigue = await self.judge_resolution(
                what_was_asked, clarifications
            )
            if resolved or fatigue:
                _log.info(
                    "[SocraticAgent] resolved=%s fatigue=%s -- stopping",
                    resolved,
                    fatigue,
                )
                if self._tom is not None:
                    self._tom.observe(
                        "socratic_resolved",
                        {
                            "agent": originating_agent,
                            "what_was_asked": what_was_asked,
                            "fatigue": fatigue,
                        },
                    )
                return None

        # ------------------------------------------------------------------
        # Transparency tracking
        # ------------------------------------------------------------------
        transparency_events: list[str] = []

        # ------------------------------------------------------------------
        # ToM proactive guidance -- evaluate recent Socratic history
        # ------------------------------------------------------------------
        tom_guidance = ""
        needs_dean = False
        if self._tom is not None:
            try:
                tom_guidance, needs_dean = await self._tom.evaluate_and_get_guidance(
                    clarifications
                )
                if tom_guidance:
                    transparency_events.append("Analyzing interaction history")
                    _log.info(
                        "[SocraticAgent] ToM guidance: needs_dean=%s guidance=%s",
                        needs_dean,
                        tom_guidance[:80],
                    )
            except Exception as exc:
                _log.debug("[SocraticAgent] ToM guidance failed: %s", exc)

        # ------------------------------------------------------------------
        # KB lookup
        # ------------------------------------------------------------------
        kb_query = f"{what_was_asked}\n{what_is_known[:300]}"
        kb_text, _refs = await self._kb_lookup(kb_query)
        if kb_text:
            transparency_events.append("Consulting Knowledge Base")

        # ------------------------------------------------------------------
        # Generate probe
        # ------------------------------------------------------------------
        try:
            result = await self._llm.predict(
                SocraticProbeGeneration,
                originating_agent=originating_agent,
                what_was_asked=what_was_asked,
                what_is_known=what_is_known,
                suspect_output=suspect_output,
                issue=issue,
                missing_facts=json.dumps(missing_facts),
                ungrounded_claims=json.dumps(ungrounded_claims),
                user_model=self._user_model_json(),
                tom_guidance=tom_guidance,
                conversation_history=self._format_history(clarifications),
                kb_context=kb_text,
            )
        except Exception as exc:
            _log.warning("[SocraticAgent] probe generation failed: %s", exc)
            return None

        probe_out = getattr(result, "probe", None)
        if probe_out is None:
            return None

        question = getattr(probe_out, "question", "").strip()
        if not question:
            return None

        kb_facts = getattr(probe_out, "kb_facts_to_present", "").strip()

        # ------------------------------------------------------------------
        # Dean review -- only when ToM flags frustration/overwhelm
        # ------------------------------------------------------------------
        if needs_dean:
            try:
                dean_result = await self._llm.predict(
                    DeanReview,
                    draft_question=question,
                    conversation_history=self._format_history(clarifications),
                    tom_guidance=tom_guidance,
                    user_model=self._user_model_json(),
                )
                dean_out = getattr(dean_result, "result", None)
                if dean_out is not None:
                    refined = getattr(dean_out, "refined_question", "").strip()
                    if refined:
                        question = refined
                        changes = getattr(dean_out, "changes_applied", [])
                        transparency_events.append(
                            "Dean review applied"
                            + (f": {', '.join(changes)}" if changes else "")
                        )
                        _log.info(
                            "[SocraticAgent] Dean refined probe changes=%s", changes
                        )
            except Exception as exc:
                _log.debug("[SocraticAgent] Dean review failed: %s", exc)

        probe = SocraticProbe(
            probe_id=f"{originating_agent}:{what_was_asked}:{turn}:{uuid.uuid4().hex[:6]}",
            question=question,
            style=getattr(probe_out, "style", "depth"),
            originating_agent=originating_agent,
            what_was_asked=what_was_asked,
            turn=turn,
            max_turns=self._max_turns,
            rag_grounding=getattr(probe_out, "rag_grounding", ""),
            kb_facts=kb_facts,
            transparency_events=transparency_events,
        )

        if self._tom is not None:
            self._tom.observe(
                "socratic_probe",
                {
                    "agent": originating_agent,
                    "field": what_was_asked,
                    "turn": turn,
                    "issue": issue,
                    "style": probe.style,
                    "question": probe.question,
                },
            )

        return probe

    def log_answer(
        self,
        originating_agent: str,
        what_was_asked: str,
        clarification: Clarification,
    ) -> None:
        """Log a user answer to the Tier-1 ToM trace."""
        if self._tom is None:
            return
        self._tom.observe(
            "socratic_answer",
            {
                "agent": originating_agent,
                "field": what_was_asked,
                "turn": clarification.turn,
                "question": clarification.question,
                "answer": clarification.answer,
            },
        )

    # -- Existing: post-hoc section review (expansion stage) ------------------

    async def review_section(
        self,
        section_content: str,
        conversation_history: str,
        tom_state: ToMState,
    ) -> AgentResult:
        """Ask a Socratic follow-up question about a completed section."""
        chunks = await self._kb.query(section_content[:200]) if self._kb else []
        kb_text = "\n".join(c.text for c in chunks if c.text)

        result = await self._llm.predict(
            SocraticReview,
            section_content=section_content,
            conversation_history=conversation_history,
            tom_state=json.dumps(
                {
                    "confidence": tom_state.confidence,
                    "scaffolding_level": tom_state.scaffolding_level,
                }
            ),
            kb_context=kb_text,
        )

        response = getattr(result, "response", None)
        if response is None:
            return AgentResult()

        question = getattr(response, "question", str(response))
        refs = [
            KBReference(source=c.source, chunk=c.text, relevance=c.relevance)
            for c in chunks
        ]

        if question.lower().strip().startswith("looks good"):
            return AgentResult(raw_text=question)

        return AgentResult(
            suggestions=[
                Suggestion(
                    field="socratic",
                    message=question,
                    severity="medium",
                    references=refs,
                )
            ],
            raw_text=getattr(response, "reasoning", ""),
        )

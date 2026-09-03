"""ADR Agent — reviews step-by-step ADR creation and provides suggestions.

Plain Python class — no protocol overhead.  Returns ``AgentResult``
dataclasses with typed suggestions from DSPy+BAMLAdapter.

When ToM is injected, the agent consults it for ambiguous or short
user inputs to adapt its review behaviour.

When the SocraticAgent is injected, ``review_step`` runs adequacy +
grounding judges and may return a :class:`SocraticProbe` instead of a
suggestion — pausing the wizard so the user can answer a
thought-provoking question before moving on.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sdlicit.agents.base import (
    ADRDirection,
    AgentResult,
    Clarification,
    KBReference,
    Suggestion,
)
from sdlicit.agents.llm.dspy_modules import (
    ADRFullReview,
    ADRStepReview,
    SuggestADRDirections,
    ToMADRStepConsult,
)
from sdlicit.agents.llm.gateway import LLMGateway
from sdlicit.helpers.context_budget import (
    assemble_context,
    context_budget_tokens,
    parse_adr_records,
    rank_prior_adrs,
)
from sdlicit.helpers.kb_access import retrieve_context
from sdlicit.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from sdlicit.expansion_stage.agents.socratic_agent import SocraticAgent
    from sdlicit.expansion_stage.agents.tom_agent import ToMAgent
    from sdlicit.expansion_stage.tools.kb_router import KBRouter
    from sdlicit.expansion_stage.tools.knowledge_base import KnowledgeBase

_log = get_logger("agent")


class ADRAgent:
    """Composing-stage agent for ADR step review and full review."""

    def __init__(
        self,
        llm: LLMGateway,
        kb: KnowledgeBase | None,
        tom: ToMAgent | None = None,
        socratic: SocraticAgent | None = None,
        kb_router: KBRouter | None = None,
        model_context_window: int = 8192,
        compact_threshold_pct: float = 0.4,
        agentic: bool = False,
        agentic_max_iters: int = 5,
        project_dir: Path | None = None,
    ) -> None:
        self._llm = llm
        self._kb = kb
        self._tom = tom
        self._socratic = socratic
        self._kb_router = kb_router
        self._context_budget = context_budget_tokens(model_context_window, compact_threshold_pct)
        self._agentic = agentic
        self._agentic_max_iters = agentic_max_iters
        self._project_dir = project_dir

    async def review_step(
        self,
        step_name: str,
        step_value: str | list[str],
        partial_adr: dict,
        clarifications: list[Clarification] | None = None,
    ) -> AgentResult:
        """Review a single ADR field and return suggestions.

        If a Socratic agent is wired in, the input is judged for
        adequacy first; an inadequate input causes the call to short-
        circuit with a :class:`SocraticProbe`.  After the LLM call, the
        output is judged for grounding; ungrounded output similarly
        produces a probe.
        """
        clarifications = clarifications or []
        value_str = step_value if isinstance(step_value, str) else ", ".join(step_value)
        query = f"{step_name}: {value_str}"

        # Fold prior Socratic clarifications into the effective input
        clarification_text = (
            "\n".join(f"Q: {c.question}\nA: {c.answer}" for c in clarifications)
            if clarifications
            else ""
        )
        effective_input = (
            f"{value_str}\n\n[user clarifications]\n{clarification_text}"
            if clarification_text
            else value_str
        )

        # ToM consultation — when input is very short / potentially ambiguous
        if self._tom is not None and len(value_str.strip()) < 30:
            try:
                rec = await self._tom.consult(
                    ToMADRStepConsult,
                    step_name=step_name,
                    step_value=value_str,
                    partial_adr=json.dumps(partial_adr)[:200],
                )
                _log.info("[ADRAgent] ToM consulted for short input on %s", step_name)
                _ = rec  # used for side-effect (state update inside ToM)
            except Exception as exc:
                _log.debug("[ADRAgent] ToM consultation failed: %s", exc)

        # Socratic input judge — may return a probe before LLM call
        if self._socratic is not None:
            adequate, missing_facts = await self._socratic.judge_input(
                agent_name="ADRAgent",
                what_was_asked=step_name,
                user_input=effective_input,
                prior_context=json.dumps(partial_adr)[:400],
            )
            if not adequate:
                probe = await self._socratic.consult(
                    originating_agent="ADRAgent",
                    what_was_asked=step_name,
                    what_is_known=effective_input,
                    clarifications=clarifications,
                    issue="ambiguous_input",
                    missing_facts=missing_facts,
                )
                if probe is not None:
                    _log.info(
                        "[ADRAgent] Socratic probe (input) field=%s turn=%d",
                        step_name,
                        probe.turn,
                    )
                    return AgentResult(socratic_probe=probe)

        # KB lookup (skipped when RAG disabled)
        kb_text = await retrieve_context(query, kb_router=self._kb_router, kb=self._kb)

        # DSPy predict — returns typed Pydantic via BAMLAdapter
        result = await self._llm.predict(
            ADRStepReview,
            step_name=step_name,
            step_value=(
                json.dumps(step_value)
                if not isinstance(step_value, str)
                else step_value
            ),
            current_artifact=json.dumps(partial_adr),
            kb_chunks=kb_text,
        )

        suggestion = getattr(result, "suggestion", None)
        if suggestion is None or not getattr(suggestion, "should_show", True):
            return AgentResult()

        suggestion_message = getattr(suggestion, "message", str(suggestion))

        # Auto-Socratic: if the DSPy step review flagged this field
        needs_socratic = getattr(suggestion, "needs_socratic", False)
        socratic_reason = getattr(suggestion, "socratic_reason", "")
        if needs_socratic and self._socratic is not None and socratic_reason:
            try:
                probe = await self._socratic.consult(
                    originating_agent="ADRAgent",
                    what_was_asked=step_name,
                    what_is_known=effective_input,
                    clarifications=clarifications,
                    suspect_output=suggestion_message,
                    issue="ungrounded_output",
                    ungrounded_claims=[socratic_reason],
                )
                if probe is not None:
                    _log.info(
                        "[ADRAgent] Auto-Socratic probe (flagged) field=%s turn=%d",
                        step_name,
                        probe.turn,
                    )
                    return AgentResult(socratic_probe=probe)
            except Exception as exc:
                _log.debug("[ADRAgent] Auto-Socratic failed for %s: %s", step_name, exc)

        # Socratic output judge — may return a probe instead of the suggestion
        if self._socratic is not None and suggestion_message:
            grounded, ungrounded_claims = await self._socratic.judge_output(
                user_input=effective_input,
                llm_output=suggestion_message,
                kb_context=kb_text,
            )
            if not grounded:
                probe = await self._socratic.consult(
                    originating_agent="ADRAgent",
                    what_was_asked=step_name,
                    what_is_known=effective_input,
                    clarifications=clarifications,
                    suspect_output=suggestion_message,
                    issue="ungrounded_output",
                    ungrounded_claims=ungrounded_claims,
                )
                if probe is not None:
                    _log.info(
                        "[ADRAgent] Socratic probe (output) field=%s turn=%d",
                        step_name,
                        probe.turn,
                    )
                    return AgentResult(socratic_probe=probe)

        refs: list[KBReference] = []
        if kb_text:
            refs = [KBReference(source="kb", chunk=kb_text[:500], relevance=1.0)]

        return AgentResult(
            suggestions=[
                Suggestion(
                    field=getattr(suggestion, "field", step_name),
                    message=suggestion_message,
                    severity=getattr(suggestion, "severity", "medium"),
                    should_show=getattr(suggestion, "should_show", True),
                    references=refs,
                )
            ],
        )

    async def full_review(
        self,
        adr_content: str,
        prior_adrs: list[str],
    ) -> AgentResult:
        """Full coherence/completeness review of a completed ADR.

        When ``agentic`` is enabled, the KB and traceability lookups are not
        fixed ahead of time — the LLM decides whether and how to call them
        via a ReAct loop (see ``_full_review_agentic``), matching the
        deterministic-pipeline-vs-tool-calling-agent axis from the thesis
        ablation surface (Table 7).
        """
        records = rank_prior_adrs(adr_content, parse_adr_records(prior_adrs))
        prior_context, dropped = assemble_context(records, self._context_budget)
        if dropped:
            _log.info("[ADRAgent] full_review: %d prior ADR(s) dropped past budget", dropped)

        if self._agentic:
            result, refs = await self._full_review_agentic(adr_content, prior_context)
        else:
            kb_text = await retrieve_context(
                adr_content[:200], kb_router=self._kb_router, kb=self._kb
            )
            result = await self._llm.predict(
                ADRFullReview,
                adr_content=adr_content,
                prior_artifacts=prior_context,
            )
            refs = (
                [KBReference(source="kb", chunk=kb_text[:500], relevance=1.0)]
                if kb_text
                else []
            )

        review = getattr(result, "review", None)
        if review is None:
            return AgentResult()

        suggestions = [
            Suggestion(
                field="full_review",
                message=s,
                severity="medium",
                references=refs,
            )
            for s in getattr(review, "suggestions", [])
        ]

        return AgentResult(
            suggestions=suggestions,
            raw_text=getattr(review, "summary", str(review)),
        )

    async def _full_review_agentic(
        self, adr_content: str, prior_context: str
    ) -> tuple[object, list[KBReference]]:
        """ReAct variant of ``full_review``: the LLM chooses its own tool calls.

        Wraps ``query_knowledge_base``/``check_traceability`` from
        ``rag_tools`` with this agent's KB router and project dir already
        bound, so the LLM only ever supplies the arguments it actually
        controls (the query text, the artifact id).
        """
        from sdlicit.expansion_stage.tools.rag_tools import (
            check_traceability,
            query_knowledge_base,
        )

        kb_calls: list[str] = []

        async def _query_kb(query: str, mode: str = "hybrid") -> str:
            kb_calls.append(query)
            return await query_knowledge_base(
                query, kb=self._kb, kb_router=self._kb_router, mode=mode
            )

        def _check_trace(artifact_id: str, artifact_type: str = "adr") -> str:
            if self._project_dir is None:
                return "No project directory available for traceability lookup."
            return check_traceability(artifact_id, artifact_type, str(self._project_dir))

        result = await self._llm.predict_react(
            ADRFullReview,
            tools=[_query_kb, _check_trace],
            max_iters=self._agentic_max_iters,
            adr_content=adr_content,
            prior_artifacts=prior_context,
        )

        refs = [KBReference(source="kb", chunk=q, relevance=1.0) for q in kb_calls[:3]]
        return result, refs

    async def suggest_directions(
        self,
        brief: str,
        prior_adrs: list[str],
        downstream_artifacts: str = "",
        uncovered_requirements: str = "",
    ) -> AgentResult:
        """Suggest WHICH ADR topics the user should write next.

        Grounded in the project SOW, already-written ADRs, uncovered
        requirements, and any downstream artifacts (personas / stories /
        Gherkin) the caller passes in.  When ToM is wired, the user
        model is consulted so priorities can be re-ranked against
        inferred user goals.

        Returns an :class:`AgentResult` whose ``adr_directions`` list
        contains :class:`ADRDirection` items.  Solutions are out of
        scope by design — only the *decision space* is surfaced.
        """
        # KB lookup over the brief — short query window keeps it cheap.
        kb_text = await retrieve_context(
            brief[:400], kb_router=self._kb_router, kb=self._kb
        )

        # ToM: pull the aggregated user model so the LLM can re-rank
        # by inferred user goals.  Falls back to an empty JSON when
        # ToM is disabled.
        user_model_json = "{}"
        if self._tom is not None and self._tom.memory is not None:
            try:
                user_model_json = self._tom.memory.load_user_model().model_dump_json()
            except Exception as exc:
                _log.debug("[ADRAgent] could not load user model: %s", exc)

        # Rank prior ADRs by relatedness to the brief and bound them to a
        # token budget instead of concatenating a fixed, oldest-first slice.
        records = rank_prior_adrs(brief, parse_adr_records(prior_adrs))
        prior_context, dropped = assemble_context(records, self._context_budget)
        if dropped:
            _log.info(
                "[ADRAgent] suggest_directions: %d prior ADR(s) dropped past budget", dropped
            )

        result = await self._llm.predict(
            SuggestADRDirections,
            brief=brief,
            prior_adrs=prior_context,
            downstream_artifacts=downstream_artifacts,
            uncovered_requirements=uncovered_requirements,
            kb_chunks=kb_text,
            user_model=user_model_json,
        )

        directions_out = getattr(result, "directions", None)
        if directions_out is None:
            return AgentResult()

        refs: list[KBReference] = []
        if kb_text:
            refs = [KBReference(source="kb", chunk=kb_text[:500], relevance=1.0)]

        items = [
            ADRDirection(
                title=getattr(d, "title", ""),
                rationale=getattr(d, "rationale", ""),
                priority=getattr(d, "priority", "medium"),
                gap_filled=getattr(d, "gap_filled", ""),
                references=refs,
            )
            for d in getattr(directions_out, "directions", [])
            if getattr(d, "title", "")
        ]

        _log.info(
            "[ADRAgent] suggest_directions → %d direction(s)",
            len(items),
        )

        return AgentResult(
            adr_directions=items,
            raw_text=getattr(directions_out, "summary", ""),
        )

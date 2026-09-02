"""Orchestrator — central composition of all agents.

Replaces the AgentRegistry with direct references.  The CLI imports
this directly (no HTTP needed).  The FastAPI server wraps it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sdlicit.agents.base import AgentResult, Clarification
from sdlicit.agents.llm.gateway import LLMGateway, create_gateway
from sdlicit.composing_stage.agents.adr_agent import ADRAgent
from sdlicit.composing_stage.agents.requirement_agent import RequirementAgent
from sdlicit.composing_stage.agents.sow_agent import SOWAgent
from sdlicit.config import SdlicitConfig
from sdlicit.expansion_stage.agents.socratic_agent import SocraticAgent
from sdlicit.expansion_stage.agents.tom_agent import ToMAgent
from sdlicit.generation_stage.agents.bdd_facilitator_agent import BDDAgent
from sdlicit.generation_stage.agents.user_story_agent import UserStoryAgent
from sdlicit.helpers.kb_access import retrieve_context
from sdlicit.helpers.project_files import (
    SESSIONS_CHAT_DIR,
    SESSIONS_SDLICIT_DIR,
    SESSIONS_USER_DIR,
)
from sdlicit.expansion_stage.tools.knowledge_base import KnowledgeBase
from sdlicit.expansion_stage.tools.kb_router import KBRouter, StaticContextProvider
from sdlicit.logging import get_logger

_log = get_logger("agent")


class Orchestrator:
    """Composition root for all Sdlicit agents."""

    def __init__(self, config: SdlicitConfig) -> None:
        self.config = config
        self.llm = create_gateway(model_type=config.model_type)

        # --- Knowledge base (disabled when enable_rag=False) ---
        if config.enable_rag:
            self.kb: KnowledgeBase | None = KnowledgeBase.from_config(config)
            _log.info("KnowledgeBase enabled (working_dir=%s)", config.kb_path)
        else:
            self.kb = None
            if config.context_override:
                _log.info("KnowledgeBase DISABLED — using context_override (%d chars)", len(config.context_override))
            else:
                _log.info("KnowledgeBase DISABLED by config (enable_rag=False)")

        # --- KB Router (store-aware retrieval layer) ---
        if self.kb is not None:
            self.kb_router: KBRouter | StaticContextProvider | None = KBRouter.from_config(
                self.kb, config, agent_name=""
            )
            _log.info(
                "KBRouter enabled (probe=%s, stores=%s)",
                config.kb_probe_method,
                config.kb_agent_stores,
            )
        elif config.context_override:
            self.kb_router = StaticContextProvider(config.context_override)
            _log.info("StaticContextProvider enabled (%d chars)", len(config.context_override))
        else:
            self.kb_router = None

        # --- Theory of Mind (cross-cutting service) ---
        if config.enable_tom:
            self.tom: ToMAgent | None = ToMAgent(
                self.llm,
                chat_sessions_dir=config.project_dir / SESSIONS_CHAT_DIR,
                sdlicit_sessions_dir=config.project_dir / SESSIONS_SDLICIT_DIR,
                user_dir=config.project_dir / SESSIONS_USER_DIR,
                focus=config.tom_focus,
            )
            _log.info("ToMAgent initialised (focus=%s)", config.tom_focus)
        else:
            self.tom = None
            _log.info("ToMAgent DISABLED by config")

        # --- Socratic (cross-cutting consultation) ---
        # Created before agents that consult it (ADR, SOW).
        if config.enable_socratic:
            socratic_router: KBRouter | StaticContextProvider | None
            if self.kb:
                socratic_router = KBRouter.from_config(self.kb, config, agent_name="socratic")
            elif config.context_override:
                socratic_router = StaticContextProvider(config.context_override)
            else:
                socratic_router = None

            self.socratic: SocraticAgent | None = SocraticAgent(
                self.llm,
                self.kb,
                tom=self.tom,
                judge_mode=config.socratic_judge_mode,
                max_turns=config.socratic_max_turns,
                kb_router=socratic_router,
            )
            _log.info(
                "SocraticAgent initialised  judge_mode=%s  max_turns=%d",
                config.socratic_judge_mode,
                config.socratic_max_turns,
            )
        else:
            self.socratic = None
            _log.info("SocraticAgent DISABLED by config")

        # --- Agents — direct references, no registry ---
        # ToM + Socratic are passed to agents so they can self-consult.
        # KBRouter instances have per-agent default store configured via config.
        # When context_override is set (no LightRAG), agents get a StaticContextProvider.
        def _agent_router(agent_name: str):
            if self.kb is not None:
                return KBRouter.from_config(self.kb, config, agent_name=agent_name)
            if config.context_override:
                return StaticContextProvider(config.context_override)
            return None

        self.sow = SOWAgent(
            self.llm,
            self.kb,
            tom=self.tom,
            socratic=self.socratic,
            kb_router=_agent_router("sow"),
        )
        _log.info("SOWAgent initialised")
        self.adr = ADRAgent(
            self.llm,
            self.kb,
            tom=self.tom,
            socratic=self.socratic,
            kb_router=_agent_router("adr"),
        )
        _log.info("ADRAgent initialised")
        self.requirement = RequirementAgent(
            self.llm,
            self.kb,
            tom=self.tom,
            kb_router=_agent_router("requirement"),
        )
        _log.info("RequirementAgent initialised")

        self.bdd = BDDAgent(self.llm, tom=self.tom)
        _log.info("BDDAgent initialised")
        self.user_story = UserStoryAgent(self.llm, tom=self.tom)
        _log.info("UserStoryAgent initialised")

        # --- Traceability service ---
        from sdlicit.expansion_stage.traceability import TraceService

        self.trace_service = TraceService.from_config(config, llm=self.llm, kb=self.kb)
        _log.info("TraceService initialised (mode=%s)", config.trace_check_mode)

        _log.info(
            "Orchestrator ready — model=%s provider=%s rag=%s tom=%s socratic=%s",
            config.model,
            config.provider,
            config.enable_rag,
            config.enable_tom,
            config.enable_socratic,
        )

    # -- Session lifecycle (delegates to ToM) ----------------------------------

    def start_session(self, stage: str = "unknown") -> str | None:
        """Start a ToM session.  Returns session_id or None if ToM disabled."""
        if self.tom is None:
            return None
        return self.tom.start_session(stage)

    async def end_session(self) -> dict[str, Any]:
        """End the current ToM session — returns Tier 1/2/3 artifacts.

        The CLI is responsible for persisting the returned artifacts;
        the backend never writes session files.
        """
        if self.tom is None:
            return {"status": "skipped", "reason": "tom disabled"}
        return await self.tom.end_session()

    async def compact_session(self) -> dict[str, Any] | None:
        """Mid-session compaction — returns a summary the CLI persists."""
        if self.tom is None:
            return None
        return await self.tom.compact_session()

    # -- Intake stage ----------------------------------------------------------

    async def create_sow(
        self,
        raw_brief: str,
        clarifications: list[Clarification] | None = None,
    ) -> AgentResult:
        """Stage 0: Extract structured SOW from raw text."""
        clarifications = clarifications or []
        # Log every newly received clarification to the ToM trace
        if self.socratic is not None and clarifications:
            for c in clarifications:
                self.socratic.log_answer("SOWAgent", "project brief", c)
        result = await self.sow.create_from_brief(
            raw_brief, clarifications=clarifications
        )
        # NOTE: SOW is NOT auto-ingested here.  The frontend ingests it
        # only after the user has reviewed and accepted the artifact.
        return result

    async def create_sow_stream(
        self,
        raw_brief: str,
        clarifications: list[Clarification] | None = None,
    ):
        """Stage 0 (streaming): Yield SOW section events one at a time."""
        clarifications = clarifications or []
        if self.socratic is not None and clarifications:
            for c in clarifications:
                self.socratic.log_answer("SOWAgent", "project brief", c)
        async for event in self.sow.generate_sections(
            raw_brief, clarifications=clarifications
        ):
            yield event

    # -- Composing stage -------------------------------------------------------

    async def review_step(
        self,
        step_name: str,
        step_value: str | list[str],
        partial_adr: dict[str, Any],
        prior_adrs: list[str],
        clarifications: list[Clarification] | None = None,
    ) -> AgentResult:
        """Stage 1: Review a single ADR field."""
        _log.info("[ADRAgent] review_step field=%s", step_name)
        clarifications = clarifications or []

        # Log clarification answers received from the CLI
        if self.socratic is not None and clarifications:
            for c in clarifications:
                self.socratic.log_answer("ADRAgent", step_name, c)

        # ToM observes (if enabled)
        if self.tom is not None:
            self.tom.observe(
                "step_input", {"field": step_name, "value": str(step_value)}
            )

        # ADR agent reviews
        result = await self.adr.review_step(
            step_name,
            step_value,
            partial_adr,
            prior_adrs,
            clarifications=clarifications,
        )
        _log.info("[ADRAgent] review_step → %d suggestions", len(result.suggestions))

        # ToM gates output (if enabled)
        if self.tom is not None and not self.tom.should_show(
            step_name, self.config.suggestion_threshold
        ):
            for s in result.suggestions:
                s.should_show = False
            _log.info("[ToMAgent] gated suggestions for field=%s", step_name)

        return result

    async def full_review(
        self,
        adr_content: str,
        prior_adrs: list[str],
    ) -> AgentResult:
        """Stage 1: Full review of a completed ADR."""
        return await self.adr.full_review(adr_content, prior_adrs)

    async def suggest_adr_directions(
        self,
        brief: str,
        prior_adrs: list[str],
        downstream_artifacts: str = "",
        uncovered_requirements: str = "",
    ) -> AgentResult:
        """Stage 1: Suggest WHICH ADR topics the user should write next.

        Pure ideation — never proposes solutions.  Uses the SOW/brief,
        prior ADRs, uncovered requirements, optional downstream artifacts,
        KB, and (when available) the ToM user model to re-rank by inferred
        goals.
        """
        _log.info(
            "[ADRAgent] suggest_directions  brief_chars=%d  prior=%d",
            len(brief),
            len(prior_adrs),
        )
        if self.tom is not None:
            self.tom.observe(
                "suggest_directions",
                {"brief_chars": len(brief), "prior_adrs": len(prior_adrs)},
            )
        return await self.adr.suggest_directions(
            brief=brief,
            prior_adrs=prior_adrs,
            downstream_artifacts=downstream_artifacts,
            uncovered_requirements=uncovered_requirements,
        )

    # -- Expansion stage -------------------------------------------------------

    async def expand_adr(
        self,
        adr_content: str,
        prior_adrs: list[str],
    ) -> dict[str, Any]:
        """Stage 2: Multi-agent expansion pipeline."""
        results: list[dict[str, Any]] = []

        # 1. ADR agent full review
        _log.info("[ADRAgent] expand_adr → full_review")
        adr_result = await self.adr.full_review(adr_content, prior_adrs)
        results.append(
            {
                "agent": "ADR Agent",
                "summary": "Full-sweep ADR review",
                "suggestions": [s.message for s in adr_result.suggestions],
            }
        )

        # 2. Requirement agent compliance check
        _log.info("[RequirementAgent] expand_adr → check")
        req_result = await self.requirement.check(adr_content)
        results.append(
            {
                "agent": "Requirement Agent",
                "summary": "Standards compliance check",
                "suggestions": [s.message for s in req_result.suggestions],
                "compliance": req_result.compliance_notes,
            }
        )

        # 3. ToM assessment (if enabled)
        tom_verdict = ""
        if self.tom is not None:
            _log.info("[ToMAgent] expand_adr → assess")
            tom_result = await self.tom.assess(
                [
                    {"agent": r["agent"], "output": json.dumps(r["suggestions"])}
                    for r in results
                ]
            )
            tom_verdict = tom_result.tom_observation
        else:
            _log.info("[ToMAgent] skipped (disabled)")

        return {
            "reviews": results,
            "tom_verdict": tom_verdict,
        }

    async def socratic_review(
        self,
        section_content: str,
        conversation_history: str = "",
    ) -> AgentResult:
        """Stage 2: Socratic follow-up question."""
        if self.socratic is None:
            _log.info("[SocraticAgent] skipped (disabled)")
            return AgentResult()
        from sdlicit.expansion_stage.agents.tom_agent import ToMState

        tom_state = self.tom.state if self.tom is not None else ToMState()
        _log.info("[SocraticAgent] socratic_review")
        return await self.socratic.review_section(
            section_content, conversation_history, tom_state
        )

    # -- ToM tools (exposed via MCP) -------------------------------------------

    async def consult_tom(
        self, agent_query: str, current_context: str = ""
    ) -> dict[str, Any]:
        """In-session ToM consultation — wraps ToMAgent.consult_freeform() for MCP."""
        if self.tom is None:
            return {"status": "disabled", "observation": "", "recommendation": ""}
        rec = await self.tom.consult_freeform(agent_query, current_context)
        return rec.model_dump()

    async def update_tom_memory(self, session_id: str | None = None) -> dict[str, Any]:
        """After-session ToM memory update — wraps ToMAgent.update_memory()."""
        if self.tom is None:
            return {"status": "disabled"}
        return await self.tom.update_memory(session_id)

    # -- Socratic tools (exposed via MCP) --------------------------------------

    async def consult_socratic_freeform(
        self,
        originating_agent: str,
        what_was_asked: str,
        what_is_known: str,
        suspect_output: str = "",
        issue: str = "ambiguous_input",
    ) -> dict[str, Any]:
        """Cross-cutting Socratic consultation — for MCP callers.

        External agents (e.g. a VS Code extension or another MCP
        consumer) can request a thought-provoking question.  Returns
        either a probe dict or ``{"status": "skipped"}`` if the
        Socratic agent is disabled or chose not to probe.
        """
        if self.socratic is None:
            return {"status": "disabled"}

        issue_typed: Any = (
            "ungrounded_output" if issue == "ungrounded_output" else "ambiguous_input"
        )
        probe = await self.socratic.consult(
            originating_agent=originating_agent,
            what_was_asked=what_was_asked,
            what_is_known=what_is_known,
            suspect_output=suspect_output,
            issue=issue_typed,
        )
        if probe is None:
            return {"status": "skipped"}
        return {
            "status": "probe",
            "probe_id": probe.probe_id,
            "question": probe.question,
            "style": probe.style,
            "turn": probe.turn,
            "max_turns": probe.max_turns,
            "rag_grounding": probe.rag_grounding,
        }

    # -- Generation stage ------------------------------------------------------

    async def generate_srs(self, sow_content: str) -> AgentResult:
        """Stage 2.5: Generate a structured SRS (requirements list) from a SOW.

        Uses the existing :class:`RequirementGeneration` DSPy signature
        via :class:`ValidatedRequirementProgram`.
        """
        from sdlicit.agents.llm.dspy_modules import RequirementGeneration

        _log.info("[RequirementAgent] generate_srs (sow=%d chars)", len(sow_content))

        # KB context: full LightRAG query, static override, or empty
        kb_text = await retrieve_context(
            "software requirements specification standards",
            kb_router=self.kb_router, kb=self.kb,
        )

        try:
            result = await self.llm.predict(
                RequirementGeneration,
                sow_content=sow_content,
                kb_context=kb_text,
            )
        except Exception as exc:
            _log.warning("[RequirementAgent] generate_srs prediction failed: %s", exc)
            return AgentResult()

        reqs_out = getattr(result, "requirements", None)
        if reqs_out is None:
            return AgentResult()
        items = getattr(reqs_out, "requirements", []) or []
        dumped = [r.model_dump() if hasattr(r, "model_dump") else r for r in items]
        payload: dict = {"requirements": dumped}
        # Include intro/scope if available
        for field in ("introduction", "scope"):
            val = getattr(reqs_out, field, "")
            if val:
                payload[field] = val
        return AgentResult(raw_text=json.dumps(payload))

    # -- Knowledge base --------------------------------------------------------

    async def query_kb(self, query: str, mode: str = "hybrid") -> list[dict[str, Any]]:
        """Query the knowledge base directly."""
        if self.kb is None:
            _log.info("[KnowledgeBase] skipped (disabled)")
            return []
        _log.info("[KnowledgeBase] query mode=%s", mode)
        chunks = await self.kb.query(query, mode=mode)
        return [
            {"text": c.text, "source": c.source, "relevance": c.relevance}
            for c in chunks
        ]

    async def ingest_artifact(self, text: str, artifact_type: str, name: str) -> int:
        """Auto-ingest a produced artifact into the artifact store namespace.

        Uses a structure-aware chunker (see
        :mod:`sdlicit.knowledge.chunkers`).  Returns the number of
        chunks ingested, or 0 if the KB router is disabled.
        """
        if self.kb_router is None:
            return 0
        return await self.kb_router.ingest_artifact(text, artifact_type, name)

    async def replace_artifact(self, text: str, artifact_type: str, name: str) -> int:
        """Delete existing chunks for ``(type, name)`` and re-ingest."""
        if self.kb_router is None:
            return 0
        return await self.kb_router.replace_artifact(text, artifact_type, name)

    async def supersede_adr(
        self, old_adr_id: str, new_text: str, new_adr_id: str
    ) -> dict[str, int]:
        """Handle ADR supersession in the KB.

        Removes old ADR chunks (the file remains on disk, just gets a
        ``superseded by …`` marker the user manages manually) and
        ingests the new ADR.  Returns ``{"removed": n, "added": m}``.
        """
        if self.kb_router is None:
            return {"removed": 0, "added": 0}
        removed = await self.kb_router.delete_artifact("adr", old_adr_id)
        added = await self.kb_router.ingest_artifact(new_text, "adr", new_adr_id)
        return {"removed": removed, "added": added}

    # -- Traceability --------------------------------------------------------

    def _build_trace_graph(self):
        """Build the traceability graph using the configured source.

        ``traceability_graph_source`` controls where edges come from:
        - ``"frontmatter"``: YAML frontmatter only (fast, no KB needed)
        - ``"lightrag"``: frontmatter + LightRAG's entity-relation graph
        """
        from sdlicit.expansion_stage.traceability.trace_dag import TraceGraph

        if self.config.traceability_graph_source == "lightrag" and self.kb is not None:
            return TraceGraph.from_project_and_kb(self.config.project_dir, kb=self.kb)
        return TraceGraph.from_project(self.config.project_dir)

    def get_traceability_graph(self) -> dict:
        """Build the traceability graph and return as JSON-serializable dict."""
        graph = self._build_trace_graph()
        return graph.to_dict()

    async def check_traceability(
        self, artifact_id: str, artifact_content: str | None = None
    ) -> dict:
        """Run traceability checks for an artifact.

        Returns a dict with ``issues``, ``impacted_nodes``, coverage data.
        """
        result = await self.trace_service.analyse_async(
            self.config.project_dir,
            mode=self.config.trace_check_mode,
        )

        # Extract issues relevant to this artifact
        artifact_issues = [
            i for i in result.graph_issues
            if i.get("source_id") == artifact_id or i.get("target_id") == artifact_id
        ]
        # Also include broken structural links for this artifact
        for link in result.broken_link_details:
            if link.source_id == artifact_id or link.target_id == artifact_id:
                artifact_issues.append({
                    "severity": "error",
                    "message": f"Broken link: {link.source_id} → {link.target_id} ({link.link_type})",
                    "source_id": link.source_id,
                    "target_id": link.target_id,
                })

        # Auto-detect implements links if artifact_content is provided
        suggested_implements: list[str] = []
        if artifact_content:
            suggested_implements = self.trace_service.detect_implements(
                artifact_content, self.config.project_dir
            )

        # Get impacted nodes from graph
        graph = self._build_trace_graph()
        impacted = graph.get_impacted(artifact_id) if artifact_id in graph.nodes else []

        return {
            "issues": artifact_issues,
            "impacted_nodes": impacted,
            "has_conflicts": result.has_conflicts,
            "coverage_assessment": result.conflict_assessment,
            "structural_coverage_pct": result.structural_coverage_pct,
            "semantic_coverage_pct": result.semantic_coverage_pct,
            "suggested_implements": suggested_implements,
        }

    def get_trace_coverage(self, mode: str | None = None) -> dict:
        """Get full trace coverage for the workspace. Used by extension."""
        from sdlicit.expansion_stage.traceability.trace_service import TraceCheckMode

        effective_mode: TraceCheckMode = mode or self.config.trace_check_mode  # type: ignore[assignment]
        result = self.trace_service.analyse(
            self.config.project_dir,
            mode=effective_mode,
        )
        return result.to_dict()

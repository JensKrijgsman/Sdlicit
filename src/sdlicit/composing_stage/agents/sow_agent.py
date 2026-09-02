"""SOW Agent — extracts structured Statement of Work from raw briefs.

Intake stage agent: takes unstructured text and produces a structured
SOW with open questions that seed the Socratic solicitor.

When ToM is injected, the agent consults it for very short briefs
to determine whether the user needs more guidance.

When the SocraticAgent is injected, the brief is judged for
adequacy first; if it is under-specified the agent returns a
:class:`SocraticProbe` instead of an SOW so the CLI can prompt the
user.  After extraction, the rendered SOW is judged for grounding
and may also produce a probe.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from sdlicit.agents.base import AgentResult, Clarification
from sdlicit.agents.llm.dspy_modules import (
    ExtractSOW,
    GenerateSOWSection,
    ToMSOWBriefConsult,
)
from sdlicit.agents.llm.gateway import LLMGateway
from sdlicit.helpers.kb_access import retrieve_context
from sdlicit.logging import get_logger

if TYPE_CHECKING:
    from sdlicit.expansion_stage.agents.socratic_agent import SocraticAgent
    from sdlicit.expansion_stage.agents.tom_agent import ToMAgent
    from sdlicit.expansion_stage.tools.kb_router import KBRouter
    from sdlicit.expansion_stage.tools.knowledge_base import KnowledgeBase

_log = get_logger("agent")


class SOWAgent:
    """Intake-stage agent for SOW extraction."""

    def __init__(
        self,
        llm: LLMGateway,
        kb: KnowledgeBase | None,
        tom: ToMAgent | None = None,
        socratic: SocraticAgent | None = None,
        kb_router: KBRouter | None = None,
    ) -> None:
        self._llm = llm
        self._kb = kb
        self._tom = tom
        self._socratic = socratic
        self._kb_router = kb_router

    async def create_from_brief(
        self,
        raw_text: str,
        clarifications: list[Clarification] | None = None,
    ) -> AgentResult:
        """Extract a structured SOW from a raw project brief."""
        clarifications = clarifications or []

        clarification_text = (
            "\n".join(f"Q: {c.question}\nA: {c.answer}" for c in clarifications)
            if clarifications
            else ""
        )
        effective_brief = (
            f"{raw_text}\n\n[user clarifications]\n{clarification_text}"
            if clarification_text
            else raw_text
        )

        # ToM consultation — for very short briefs, check if user needs help
        if self._tom is not None and len(raw_text.strip()) < 100:
            try:
                rec = await self._tom.consult(
                    ToMSOWBriefConsult,
                    brief_length=len(raw_text),
                )
                _log.info(
                    "[SOWAgent] ToM advises scaffolding=%s",
                    rec.scaffolding_level,
                )
            except Exception as exc:
                _log.debug("[SOWAgent] ToM consultation failed: %s", exc)

        # Socratic input judge — return a probe if the brief is inadequate
        if self._socratic is not None:
            adequate, missing_facts = await self._socratic.judge_input(
                agent_name="SOWAgent",
                what_was_asked="project brief",
                user_input=effective_brief,
            )
            if not adequate:
                probe = await self._socratic.consult(
                    originating_agent="SOWAgent",
                    what_was_asked="project brief",
                    what_is_known=effective_brief,
                    clarifications=clarifications,
                    issue="ambiguous_input",
                    missing_facts=missing_facts,
                )
                if probe is not None:
                    _log.info("[SOWAgent] Socratic probe (input) turn=%d", probe.turn)
                    return AgentResult(socratic_probe=probe)

        # Use KBRouter for store-aware retrieval (falls back to raw KB)
        kb_text = await retrieve_context(
            "statement of work template requirements",
            kb_router=self._kb_router, kb=self._kb,
        )

        result = await self._llm.predict(
            ExtractSOW,
            raw_brief=effective_brief,
            kb_context=kb_text,
        )

        sow = getattr(result, "sow", None)
        if sow is None:
            return AgentResult()

        sow_md = self._render_sow(sow)

        # Socratic output judge — return a probe if the SOW added unstated facts
        if self._socratic is not None and sow_md:
            grounded, ungrounded_claims = await self._socratic.judge_output(
                user_input=effective_brief,
                llm_output=sow_md,
                kb_context=kb_text,
            )
            if not grounded:
                probe = await self._socratic.consult(
                    originating_agent="SOWAgent",
                    what_was_asked="project brief",
                    what_is_known=effective_brief,
                    clarifications=clarifications,
                    suspect_output=sow_md,
                    issue="ungrounded_output",
                    ungrounded_claims=ungrounded_claims,
                )
                if probe is not None:
                    _log.info("[SOWAgent] Socratic probe (output) turn=%d", probe.turn)
                    return AgentResult(socratic_probe=probe, raw_text=sow_md)

        return AgentResult(raw_text=sow_md)

    @staticmethod
    def _render_sow(sow: object) -> str:
        """Render SOW as markdown artifact."""
        lines = [
            f"# {getattr(sow, 'project_name', 'Untitled')}",
            "",
            "## Problem Statement",
            "",
            getattr(sow, "problem_statement", ""),
            "",
        ]

        stakeholders = getattr(sow, "stakeholders", [])
        if stakeholders:
            lines += ["## Stakeholders", ""]
            for s in stakeholders:
                lines.append(f"### {getattr(s, 'role', '')}")
                for n in getattr(s, "needs", []):
                    lines.append(f"- {n}")
                lines.append("")

        for heading, attr in [
            ("Constraints", "constraints"),
            ("Out of Scope", "out_of_scope"),
            ("Open Questions", "open_questions"),
        ]:
            items = getattr(sow, attr, [])
            if items:
                lines += [f"## {heading}", ""]
                for item in items:
                    lines.append(f"- {item}")
                lines.append("")

        return "\n".join(lines)

    # -- Incremental per-section generation ------------------------------------

    # Ordered list of SOW sections with their markdown headings.
    SOW_SECTIONS = [
        ("project_name", "Project Name"),
        ("problem_statement", "Problem Statement"),
        ("stakeholders", "Stakeholders"),
        ("constraints", "Constraints"),
        ("out_of_scope", "Out of Scope"),
        ("open_questions", "Open Questions"),
    ]

    async def generate_sections(
        self,
        raw_text: str,
        clarifications: list[Clarification] | None = None,
    ):
        """Yield SOW sections one at a time as dicts (for SSE streaming).

        Each yielded dict has the shape:
            {"event": "section_start"|"section_complete"|"socratic_probe"|"kb_verification"|"complete",
             ...payload}

        The caller (service/router) wraps these as SSE events.
        """
        from collections.abc import AsyncGenerator  # noqa: F811

        clarifications = clarifications or []

        clarification_text = (
            "\n".join(f"Q: {c.question}\nA: {c.answer}" for c in clarifications)
            if clarifications
            else ""
        )
        effective_brief = (
            f"{raw_text}\n\n[user clarifications]\n{clarification_text}"
            if clarification_text
            else raw_text
        )

        # ToM consultation (short briefs)
        if self._tom is not None and len(raw_text.strip()) < 100:
            try:
                await self._tom.consult(
                    ToMSOWBriefConsult,
                    brief_length=len(raw_text),
                )
            except Exception:
                pass

        # Socratic input judge — if brief is inadequate, yield probe and stop
        if self._socratic is not None:
            adequate, missing_facts = await self._socratic.judge_input(
                agent_name="SOWAgent",
                what_was_asked="project brief",
                user_input=effective_brief,
            )
            if not adequate:
                probe = await self._socratic.consult(
                    originating_agent="SOWAgent",
                    what_was_asked="project brief",
                    what_is_known=effective_brief,
                    clarifications=clarifications,
                    issue="ambiguous_input",
                    missing_facts=missing_facts,
                )
                if probe is not None:
                    yield {
                        "event": "socratic_probe",
                        "section": "__input__",
                        "probe": asdict(probe),
                    }
                    return

        # KB retrieval (shared across sections)
        kb_text = await retrieve_context(
            "statement of work template requirements",
            kb_router=self._kb_router, kb=self._kb,
        )

        prior_md_parts: list[str] = []
        full_md_parts: list[str] = []

        for section_key, section_heading in self.SOW_SECTIONS:
            yield {
                "event": "section_start",
                "section": section_key,
                "heading": section_heading,
            }

            prior_sections = "\n\n".join(prior_md_parts)

            result = await self._llm.predict(
                GenerateSOWSection,
                raw_brief=effective_brief,
                section_name=section_key,
                prior_sections=prior_sections,
                kb_context=kb_text,
            )

            section_out = getattr(result, "section", None)
            content = getattr(section_out, "content", "") if section_out else ""
            needs_socratic = (
                getattr(section_out, "needs_socratic", False) if section_out else False
            )
            socratic_reason = (
                getattr(section_out, "socratic_reason", "") if section_out else ""
            )

            # Format section as markdown
            if section_key == "project_name":
                section_md = f"# {content}"
            else:
                section_md = f"## {section_heading}\n\n{content}"

            prior_md_parts.append(section_md)
            full_md_parts.append(section_md)

            yield {
                "event": "section_complete",
                "section": section_key,
                "heading": section_heading,
                "content": content,
                "markdown": section_md,
                "needs_socratic": needs_socratic,
                "socratic_reason": socratic_reason,
            }

            # Auto-Socratic for this section (non-blocking — yielded as event)
            if needs_socratic and self._socratic is not None:
                try:
                    probe = await self._socratic.consult(
                        originating_agent="SOWAgent",
                        what_was_asked=f"SOW section: {section_heading}",
                        what_is_known=effective_brief,
                        clarifications=clarifications,
                        suspect_output=content,
                        issue="ungrounded_output",
                        ungrounded_claims=[socratic_reason] if socratic_reason else [],
                    )
                    if probe is not None:
                        yield {
                            "event": "socratic_probe",
                            "section": section_key,
                            "probe": asdict(probe),
                        }
                except Exception as exc:
                    _log.debug(
                        "[SOWAgent] Socratic probe failed for %s: %s", section_key, exc
                    )

            # Async KB verification (non-blocking)
            if self._socratic is not None and content:
                try:
                    grounded, ungrounded_claims = await self._socratic.judge_output(
                        user_input=effective_brief,
                        llm_output=content,
                        kb_context=kb_text,
                    )
                    yield {
                        "event": "kb_verification",
                        "section": section_key,
                        "grounded": grounded,
                        "ungrounded_claims": ungrounded_claims,
                    }
                except Exception as exc:
                    _log.debug(
                        "[SOWAgent] KB verification failed for %s: %s", section_key, exc
                    )

        # Final complete event
        full_markdown = "\n\n".join(full_md_parts)
        yield {"event": "complete", "full_markdown": full_markdown}

"""Requirement Agent — RAG-augmented standards and literature checker.

Plain Python class — queries KB for relevant standards and assesses
the ADR against them via DSPy.

When ToM is injected, the agent consults it to calibrate the depth
of compliance feedback to the user's expertise level.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sdlicit.agents.base import AgentResult, KBReference, Suggestion
from sdlicit.agents.llm.dspy_modules import RequirementCheck, ToMComplianceConsult
from sdlicit.agents.llm.gateway import LLMGateway
from sdlicit.helpers.kb_access import retrieve_context
from sdlicit.logging import get_logger

if TYPE_CHECKING:
    from sdlicit.expansion_stage.agents.tom_agent import ToMAgent
    from sdlicit.expansion_stage.tools.kb_router import KBRouter
    from sdlicit.expansion_stage.tools.knowledge_base import KnowledgeBase

_log = get_logger("agent")


class RequirementAgent:
    """Composing-stage agent for requirements compliance checking."""

    def __init__(
        self,
        llm: LLMGateway,
        kb: KnowledgeBase | None,
        tom: ToMAgent | None = None,
        kb_router: KBRouter | None = None,
    ) -> None:
        self._llm = llm
        self._kb = kb
        self._tom = tom
        self._kb_router = kb_router

    async def check(self, adr_content: str) -> AgentResult:
        """Check an ADR against relevant standards and literature."""
        # ToM consultation — adapt compliance depth to user expertise
        if self._tom is not None:
            try:
                rec = await self._tom.consult(
                    ToMComplianceConsult,
                    adr_length=len(adr_content),
                )
                _log.info(
                    "[RequirementAgent] ToM advises scaffolding=%s",
                    rec.scaffolding_level,
                )
            except Exception as exc:
                _log.debug("[RequirementAgent] ToM consultation failed: %s", exc)

        # KB lookup — prefer router for store-aware (knowledge store) retrieval
        kb_text = await retrieve_context(
            adr_content[:300], kb_router=self._kb_router, kb=self._kb,
        )

        result = await self._llm.predict(
            RequirementCheck,
            adr_content=adr_content,
            retrieved_context=kb_text,
        )

        compliance = getattr(result, "compliance", None)
        if compliance is None:
            return AgentResult()

        refs = [
            KBReference(source=c.source, chunk=c.text, relevance=c.relevance)
            for c in chunks
        ]
        suggestions = [
            Suggestion(
                field="compliance", message=s, severity="medium", references=refs
            )
            for s in getattr(compliance, "suggestions", [])
        ]

        return AgentResult(
            suggestions=suggestions,
            compliance_notes=getattr(compliance, "assessment", str(compliance)),
        )

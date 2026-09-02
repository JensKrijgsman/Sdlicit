"""BDD Agent — persona generation and Gherkin scenario creation.

Plain Python class.  Uses gherkin-official for AST validation.

When ToM is injected, the agent consults it to adapt generation
detail level (e.g. fewer personas for an expert user, more for a novice).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sdlicit.agents.base import AgentResult, Suggestion
from sdlicit.agents.llm.dspy_modules import (
    GherkinGeneration,
    PersonaGeneration,
    ToMPersonaConsult,
)
from sdlicit.agents.llm.gateway import LLMGateway
from sdlicit.generation_stage.tools.gherkin_validator import validate_gherkin
from sdlicit.logging import get_logger

if TYPE_CHECKING:
    from sdlicit.expansion_stage.agents.tom_agent import ToMAgent

_log = get_logger("agent")


class BDDAgent:
    """Generation-stage agent for persona and Gherkin generation."""

    def __init__(
        self,
        llm: LLMGateway,
        tom: ToMAgent | None = None,
    ) -> None:
        self._llm = llm
        self._tom = tom

    async def generate_personas(self, artifacts: str) -> AgentResult:
        """Generate user personas from combined ADR + SRS content."""
        # ToM consultation — calibrate persona count/detail
        if self._tom is not None:
            try:
                rec = await self._tom.consult(
                    ToMPersonaConsult,
                    artifacts_length=len(artifacts),
                )
                _log.info(
                    "[BDDAgent] ToM advises scaffolding=%s for persona generation",
                    rec.scaffolding_level,
                )
            except Exception as exc:
                _log.debug("[BDDAgent] ToM consultation failed: %s", exc)

        try:
            result = await self._llm.predict(
                PersonaGeneration,
                artifacts=artifacts,
            )
        except Exception as exc:
            _log.warning("[BDDAgent] persona prediction failed: %s", exc)
            return AgentResult()

        personas = getattr(result, "personas", None)
        if personas is None:
            return AgentResult()

        # personas is now list[PersonaOutput] directly
        if isinstance(personas, list):
            dumped = [
                p.model_dump() if hasattr(p, "model_dump") else p for p in personas
            ]
            return AgentResult(raw_text=json.dumps({"personas": dumped}))

        return AgentResult(
            raw_text=json.dumps(
                personas.model_dump()
                if hasattr(personas, "model_dump")
                else str(personas)
            ),
        )

    async def generate_gherkin(
        self,
        persona: str,
        requirements: str,
    ) -> AgentResult:
        """Generate Gherkin scenarios for a single persona."""
        result = await self._llm.predict(
            GherkinGeneration,
            persona=persona,
            requirements=requirements,
        )

        gherkin_out = getattr(result, "gherkin", None)
        if gherkin_out is None:
            return AgentResult()

        gherkin_text = (
            getattr(gherkin_out, "gherkin", str(gherkin_out))
            if hasattr(gherkin_out, "gherkin")
            else str(gherkin_out)
        )

        # Validate with official parser
        validation = validate_gherkin(gherkin_text)
        suggestions = []
        if not validation["valid"]:
            for issue in validation["issues"]:
                suggestions.append(
                    Suggestion(
                        field="gherkin",
                        message=f"Validation: {issue}",
                        severity="high",
                    )
                )

        return AgentResult(
            suggestions=suggestions,
            raw_text=gherkin_text,
        )

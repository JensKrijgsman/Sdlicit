"""User Story Agent — generates user stories from personas + requirements.

Bridges the gap between persona generation and BDD/Gherkin creation.

When ToM is injected, the agent consults it to determine how many
stories to generate and at what detail level.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sdlicit.agents.base import AgentResult
from sdlicit.agents.llm.dspy_modules import ToMStoryConsult, UserStoryGeneration
from sdlicit.agents.llm.gateway import LLMGateway
from sdlicit.logging import get_logger

if TYPE_CHECKING:
    from sdlicit.expansion_stage.agents.tom_agent import ToMAgent

_log = get_logger("agent")


class UserStoryAgent:
    """Generation-stage agent for user story creation."""

    def __init__(
        self,
        llm: LLMGateway,
        tom: ToMAgent | None = None,
    ) -> None:
        self._llm = llm
        self._tom = tom

    async def generate(
        self,
        personas: str,
        requirements: str,
        adrs: str = "[]",
    ) -> AgentResult:
        """Generate user stories from personas, requirements, and ADR context."""
        # ToM consultation — determine story detail level
        if self._tom is not None:
            try:
                rec = await self._tom.consult(
                    ToMStoryConsult,
                    personas_length=len(personas),
                    requirements_length=len(requirements),
                )
                _log.info(
                    "[UserStoryAgent] ToM advises scaffolding=%s",
                    rec.scaffolding_level,
                )
            except Exception as exc:
                _log.debug("[UserStoryAgent] ToM consultation failed: %s", exc)

        try:
            result = await self._llm.predict(
                UserStoryGeneration,
                personas=personas,
                requirements=requirements,
                adrs=adrs,
            )
        except Exception as exc:
            _log.warning("[UserStoryAgent] prediction failed: %s", exc)
            return AgentResult()

        stories = getattr(result, "stories", None)
        if stories is None:
            return AgentResult()

        raw = stories.model_dump() if hasattr(stories, "model_dump") else str(stories)
        return AgentResult(raw_text=json.dumps(raw))

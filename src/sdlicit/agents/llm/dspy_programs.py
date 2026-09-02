"""Validated DSPy Programs — dspy.Module subclasses with dspy.Refine.

Programs use ``dspy.Refine`` to engage DSPy's feedback-driven retry
loop.  When a reward function scores output below threshold, Refine
auto-generates feedback from the reward function code and retries —
turning post-hoc error reporting into *in-loop repair*.

Reward functions encode two constraint tiers:
- Hard constraints (former ``dspy.Assert``): score → 0.0 on violation
- Soft constraints (former ``dspy.Suggest``): reduce score but don't zero it

These programs are the correct unit for DSPy optimizers:
  ``BootstrapFewShot`` / ``MIPROv2`` / ``SIMBA`` operate on
  ``dspy.Module`` instances, not raw signatures.

Registry pattern
~~~~~~~~~~~~~~~~
Programs are registered in the ``LLMGateway`` at bootstrap time via
``gateway.register_program(sig_name, program)``.  Calling agents
continue to use ``gateway.predict(SomeSignature, …)`` — the gateway
dispatches to the registered program transparently.
"""

from __future__ import annotations

import re
from typing import Literal

import dspy

from sdlicit.agents.llm.dspy_modules import (
    ADRGeneration,
    GherkinGeneration,
    RequirementGeneration,
    UserStoryGeneration,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_THRESHOLD = 1.0


# ---------------------------------------------------------------------------
# Reward functions
# ---------------------------------------------------------------------------


def _gherkin_reward(_args: dict, pred: dspy.Prediction) -> float:
    """Score Gherkin output: hard structure checks + soft step keywords."""
    gherkin_out = pred.gherkin
    gherkin_text = (
        getattr(gherkin_out, "gherkin", str(gherkin_out))
        if hasattr(gherkin_out, "gherkin")
        else str(gherkin_out)
    )

    # Hard constraints — score 0.0 on violation
    if "Feature:" not in gherkin_text:
        return 0.0
    if "Scenario:" not in gherkin_text and "Scenario Outline:" not in gherkin_text:
        return 0.0

    # Soft constraint — reduce score
    score = 1.0
    if not all(kw in gherkin_text for kw in ("Given", "When", "Then")):
        score -= 0.3

    return score


_USER_STORY_RE = re.compile(
    r"As an?\s+.+?,\s+I want\s+.+?,\s+so that\s+", re.IGNORECASE
)
_STORY_ID_RE = re.compile(r"^STORY-\d+$")


def _user_story_reward(_args: dict, pred: dspy.Prediction) -> float:
    """Score user stories: must exist, IDs valid, statement format."""
    stories = pred.stories
    story_list = (
        getattr(stories, "stories", stories)
        if not isinstance(stories, list)
        else stories
    )

    # Hard: must produce at least one story
    if not isinstance(story_list, list) or len(story_list) == 0:
        return 0.0

    score = 1.0
    for story in story_list:
        story_id = getattr(story, "story_id", "")
        # Hard: ID format
        if not _STORY_ID_RE.match(story_id):
            return 0.0

        # Soft: statement format
        statement = getattr(story, "statement", "")
        if not _USER_STORY_RE.search(statement):
            score -= 0.15

    return max(score, 0.1)


_REQ_ID_RE = re.compile(r"^REQ-[A-Z]+-\d+$")


def _requirement_reward(_args: dict, pred: dspy.Prediction) -> float:
    """Score requirements: must exist, IDs must match REQ-<DOMAIN>-<NN>."""
    reqs = pred.requirements
    req_list = (
        getattr(reqs, "requirements", reqs) if not isinstance(reqs, list) else reqs
    )

    # Hard: at least one requirement
    if not isinstance(req_list, list) or len(req_list) == 0:
        return 0.0

    # Hard: all IDs valid
    for req in req_list:
        req_id = getattr(req, "req_id", "")
        if not _REQ_ID_RE.match(req_id):
            return 0.0

    return 1.0


_ADR_ID_RE = re.compile(r"^ADR-\d{3}$")


def _adr_reward(_args: dict, pred: dspy.Prediction) -> float:
    """Score ADR: ID format (hard) + citation count (soft)."""
    adr = pred.adr
    adr_id = getattr(adr, "adr_id", "")

    # Hard: ID format
    if not _ADR_ID_RE.match(adr_id):
        return 0.0

    # Soft: should cite requirements
    score = 1.0
    cited = getattr(adr, "implements", [])
    if len(cited) == 0:
        score -= 0.3

    return score


# ---------------------------------------------------------------------------
# Validated Programs (dspy.Module subclasses wrapping dspy.Refine)
# ---------------------------------------------------------------------------


class ValidatedGherkinProgram(dspy.Module):
    """Gherkin generation with structural validation via dspy.Refine.

    Hard constraints (score → 0.0):
    - Output must contain ``Feature:``
    - Output must contain at least one ``Scenario:``

    Soft constraint (score reduction):
    - Scenarios should use Given / When / Then steps
    """

    def __init__(
        self, model_type: Literal["standard", "thinking"] = "standard"
    ) -> None:
        super().__init__()
        if model_type == "thinking":
            generate = dspy.Predict(GherkinGeneration)
        else:
            generate = dspy.ChainOfThought(GherkinGeneration)

        self.refine = dspy.Refine(
            module=generate,
            N=_MAX_RETRIES,
            reward_fn=_gherkin_reward,
            threshold=_THRESHOLD,
        )

    def forward(self, persona: str, requirements: str) -> dspy.Prediction:
        return self.refine(persona=persona, requirements=requirements)


class ValidatedUserStoryProgram(dspy.Module):
    """User story generation with format validation via dspy.Refine.

    Hard constraints (score → 0.0):
    - Must produce at least one story
    - Each story_id must match ``STORY-NN``

    Soft constraint (score reduction):
    - Statement should follow 'As a …, I want …, so that …'
    """

    def __init__(
        self, model_type: Literal["standard", "thinking"] = "standard"
    ) -> None:
        super().__init__()
        if model_type == "thinking":
            generate = dspy.Predict(UserStoryGeneration)
        else:
            generate = dspy.ChainOfThought(UserStoryGeneration)

        self.refine = dspy.Refine(
            module=generate,
            N=_MAX_RETRIES,
            reward_fn=_user_story_reward,
            threshold=_THRESHOLD,
        )

    def forward(
        self, personas: str, requirements: str, adrs: str = "[]"
    ) -> dspy.Prediction:
        return self.refine(personas=personas, requirements=requirements, adrs=adrs)


class ValidatedRequirementProgram(dspy.Module):
    """Requirement generation with ID-format validation via dspy.Refine.

    Hard constraints (score → 0.0):
    - Must produce at least one requirement
    - Each req_id must match ``REQ-<DOMAIN>-<NN>``
    """

    def __init__(
        self, model_type: Literal["standard", "thinking"] = "standard"
    ) -> None:
        super().__init__()
        if model_type == "thinking":
            generate = dspy.Predict(RequirementGeneration)
        else:
            generate = dspy.ChainOfThought(RequirementGeneration)

        self.refine = dspy.Refine(
            module=generate,
            N=_MAX_RETRIES,
            reward_fn=_requirement_reward,
            threshold=_THRESHOLD,
        )

    def forward(self, sow_content: str, kb_context: str) -> dspy.Prediction:
        return self.refine(sow_content=sow_content, kb_context=kb_context)


class ValidatedADRProgram(dspy.Module):
    """ADR generation with ID and citation validation via dspy.Refine.

    Hard constraint (score → 0.0):
    - adr_id must match ``ADR-NNN`` (three digits)

    Soft constraint (score reduction):
    - Should cite at least one requirement
    """

    def __init__(
        self, model_type: Literal["standard", "thinking"] = "standard"
    ) -> None:
        super().__init__()
        if model_type == "thinking":
            generate = dspy.Predict(ADRGeneration)
        else:
            generate = dspy.ChainOfThought(ADRGeneration)

        self.refine = dspy.Refine(
            module=generate,
            N=_MAX_RETRIES,
            reward_fn=_adr_reward,
            threshold=_THRESHOLD,
        )

    def forward(
        self,
        topic: str,
        requirements: str,
        prior_adrs: str,
        kb_context: str,
    ) -> dspy.Prediction:
        return self.refine(
            topic=topic,
            requirements=requirements,
            prior_adrs=prior_adrs,
            kb_context=kb_context,
        )

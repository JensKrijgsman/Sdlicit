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
    ExtractSOW,
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

_USER_STORY_RE = re.compile(
    r"As an?\s+.+?,\s+I want\s+.+?,\s+so that\s+", re.IGNORECASE
)
_STORY_ID_RE = re.compile(r"^STORY-\d+$")
_REQ_ID_RE = re.compile(r"^REQ-[A-Z]+-\d+$")
# Accept both 3-digit (ADR-NNN) and 4-digit (ADR-NNNN) widths. The replay
# pipeline renumbers ADRs to 4 digits, so the reward must tolerate both.
_ADR_ID_RE = re.compile(r"^ADR-\d{3,4}$")

# Patterns used to harvest valid IDs from the input arguments for resolution.
_RE_REQ_ID = re.compile(r"REQ-[A-Z]+-\d+")
_RE_PERSONA_ID = re.compile(r"PERSONA-\d+")
_CANON_RE = re.compile(r"^([A-Za-z]+(?:-[A-Za-z]+)*)-(\d+)$")

# ISO 29148 requirement categories (normalised forms accepted).
_VALID_CATEGORIES = frozenset(
    {"functional", "non_functional", "nonfunctional", "non-functional"}
)


def _canon(raw: str) -> str:
    """Width-insensitive ID canonicalisation so ADR-002 == ADR-0002."""
    m = _CANON_RE.match(str(raw).strip())
    if not m:
        return str(raw).strip().upper()
    return f"{m.group(1).upper()}-{int(m.group(2))}"


def _ids_in(arg: object, pattern: re.Pattern[str]) -> set[str]:
    """Canonical set of IDs matching *pattern* inside a stringified arg."""
    return {_canon(m) for m in pattern.findall(str(arg or ""))}


def _parse_gherkin_feature(text: str) -> tuple[dict | None, bool]:
    """Parse Gherkin text with the official Cucumber parser.

    Returns ``(feature_dict_or_None, parser_available)``. When the
    gherkin-official package is missing, ``parser_available`` is False and the
    caller falls back to a substring check.
    """
    try:
        from gherkin.parser import Parser
        from gherkin.token_scanner import TokenScanner
    except ImportError:
        return None, False
    try:
        doc = Parser().parse(TokenScanner(text))
    except Exception:
        # Parser is available but the text is not valid Gherkin.
        return None, True
    return doc.get("feature"), True


def _gherkin_reward(_args: dict, pred: dspy.Prediction) -> float:
    """Score Gherkin output via the official Cucumber parser.

    Hard (score -> 0.0):
      - Output must parse with the gherkin-official parser.
      - Parsed document must contain a Feature with at least one Scenario.

    Soft (score reduction):
      - Scenarios should use Given / When / Then step keywords.
      - cites_story / cites_adr, when present, should be well-formed IDs.
    """
    gherkin_out = pred.gherkin
    gherkin_text = (
        getattr(gherkin_out, "gherkin", str(gherkin_out))
        if hasattr(gherkin_out, "gherkin")
        else str(gherkin_out)
    )

    feature, parser_ok = _parse_gherkin_feature(gherkin_text)

    if parser_ok:
        if feature is None:
            return 0.0
        scenarios = [
            child["scenario"]
            for child in feature.get("children", [])
            if child.get("scenario")
        ]
        if not scenarios:
            return 0.0
        score = 1.0
        step_keywords = {
            (step.get("keyword", "") or "").strip().lower()
            for scenario in scenarios
            for step in scenario.get("steps", [])
        }
        if not {"given", "when", "then"}.issubset(step_keywords):
            score -= 0.3
    else:
        # Parser unavailable — legacy substring structural check.
        if "Feature:" not in gherkin_text:
            return 0.0
        if "Scenario:" not in gherkin_text and "Scenario Outline:" not in gherkin_text:
            return 0.0
        score = 1.0
        if not all(kw in gherkin_text for kw in ("Given", "When", "Then")):
            score -= 0.3

    # Soft: well-formed trace citations when supplied.
    cites_story = (getattr(gherkin_out, "cites_story", "") or "").strip()
    if cites_story and not re.fullmatch(r"STORY-\d+", cites_story):
        score -= 0.1
    cites_adr = (getattr(gherkin_out, "cites_adr", "") or "").strip()
    if cites_adr and not re.fullmatch(r"ADR-\d{3,4}", cites_adr):
        score -= 0.1

    return max(score, 0.1)


def _user_story_reward(_args: dict, pred: dspy.Prediction) -> float:
    """Score user stories: IDs (hard), statement + citation resolution (soft)."""
    stories = pred.stories
    story_list = (
        getattr(stories, "stories", stories)
        if not isinstance(stories, list)
        else stories
    )

    # Hard: must produce at least one story
    if not isinstance(story_list, list) or len(story_list) == 0:
        return 0.0

    valid_personas = _ids_in(_args.get("personas"), _RE_PERSONA_ID)
    valid_reqs = _ids_in(_args.get("requirements"), _RE_REQ_ID)

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

        # Soft: persona must resolve to a provided persona
        persona_id = getattr(story, "persona_id", "") or ""
        if valid_personas and _canon(persona_id) not in valid_personas:
            score -= 0.2

        # Soft: requirement citations must resolve to provided requirements
        req_ids = getattr(story, "requirement_ids", []) or []
        if valid_reqs:
            if any(_canon(r) not in valid_reqs for r in req_ids):
                score -= 0.2
            if not req_ids:
                score -= 0.1

    return max(score, 0.1)


def _requirement_reward(_args: dict, pred: dspy.Prediction) -> float:
    """Score requirements: ID + ISO category (hard), domain + criteria (soft).

    Note: the RequirementItem schema deliberately keeps source traceability out
    of the statement text, so this reward validates ISO 29148 structure rather
    than per-requirement KB citations. KB grounding enters through the
    ``kb_context`` input, and source traceability is recorded at the SRS level.
    """
    reqs = pred.requirements
    req_list = (
        getattr(reqs, "requirements", reqs) if not isinstance(reqs, list) else reqs
    )

    # Hard: at least one requirement
    if not isinstance(req_list, list) or len(req_list) == 0:
        return 0.0

    score = 1.0
    for req in req_list:
        req_id = getattr(req, "req_id", "")
        # Hard: ID format
        if not _REQ_ID_RE.match(req_id):
            return 0.0
        # Hard: ISO 29148 functional / non-functional classification
        category = (getattr(req, "category", "") or "").strip().lower()
        if category not in _VALID_CATEGORIES:
            return 0.0
        # Soft: domain tag present
        if not (getattr(req, "domain", "") or "").strip():
            score -= 0.1
        # Soft: measurable acceptance criteria present
        if not (getattr(req, "acceptance_criteria", "") or "").strip():
            score -= 0.1

    return max(score, 0.1)


def _adr_reward(_args: dict, pred: dspy.Prediction) -> float:
    """Score ADR: ID format (hard) + citation resolution + MADR shape (soft).

    The ``implements`` citations are validated against the requirement IDs in
    the ``requirements`` input. Unresolved (hallucinated) citations incur a
    strong penalty so the dspy.Refine loop repairs them. A deterministic
    resolution filter in the pipeline remains the final guarantee.
    """
    adr = pred.adr
    adr_id = getattr(adr, "adr_id", "")

    # Hard: ID format (ADR-NNN or ADR-NNNN)
    if not _ADR_ID_RE.match(adr_id):
        return 0.0

    score = 1.0

    # Soft (strong): cited requirements must resolve to the provided list.
    valid_reqs = _ids_in(_args.get("requirements"), _RE_REQ_ID)
    implements = getattr(adr, "implements", []) or []
    if valid_reqs:
        if any(_canon(r) not in valid_reqs for r in implements):
            score -= 0.5
        if len(implements) == 0:
            score -= 0.3
    elif implements:
        # Claims to implement requirements when none were provided.
        score -= 0.5

    # Soft: MADR structural completeness.
    if not (getattr(adr, "title", "") or "").strip():
        score -= 0.2
    if not (getattr(adr, "context", "") or "").strip():
        score -= 0.2
    if not (getattr(adr, "decision", "") or "").strip():
        score -= 0.2
    if len(getattr(adr, "alternatives", []) or []) == 0:
        score -= 0.2

    return max(score, 0.1)


def _sow_reward(_args: dict, pred: dspy.Prediction) -> float:
    """Score SOW extraction: core fields (hard) + completeness (soft)."""
    sow = pred.sow
    project_name = (getattr(sow, "project_name", "") or "").strip()
    problem = (getattr(sow, "problem_statement", "") or "").strip()

    # Hard: a SOW without a project name or problem statement is unusable.
    if not project_name:
        return 0.0
    if not problem:
        return 0.0

    score = 1.0
    # Soft: should identify stakeholders.
    if len(getattr(sow, "stakeholders", []) or []) == 0:
        score -= 0.25
    # Soft: should capture ambiguities for the Socratic stage.
    if len(getattr(sow, "open_questions", []) or []) == 0:
        score -= 0.25

    return max(score, 0.1)


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
    - adr_id must match ``ADR-NNN`` or ``ADR-NNNN`` (3- or 4-digit)

    Soft constraints (score reduction):
    - Cited requirements (``implements``) must resolve to the provided list
    - MADR shape: title, context, decision, and at least one alternative
    - Should cite at least one requirement when requirements are provided
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


class ValidatedSOWProgram(dspy.Module):
    """Statement-of-Work extraction with structural validation via dspy.Refine.

    Hard constraints (score → 0.0):
    - project_name must be non-empty
    - problem_statement must be non-empty

    Soft constraints (score reduction):
    - Should identify at least one stakeholder
    - Should capture at least one open question (ambiguity for the Socratic stage)

    SOW extraction is a structured extraction task, so this program always uses
    ``dspy.Predict`` (chain-of-thought adds no benefit and risks invention).
    """

    def __init__(
        self, model_type: Literal["standard", "thinking"] = "standard"
    ) -> None:
        super().__init__()
        generate = dspy.Predict(ExtractSOW)

        self.refine = dspy.Refine(
            module=generate,
            N=_MAX_RETRIES,
            reward_fn=_sow_reward,
            threshold=_THRESHOLD,
        )

    def forward(self, raw_brief: str, kb_context: str) -> dspy.Prediction:
        return self.refine(raw_brief=raw_brief, kb_context=kb_context)

"""DSPy signatures with Pydantic-typed outputs for BAMLAdapter.

All prompt engineering goes through DSPy signatures so that prompts
can be optimised with MIPRO / BootstrapFewShot without touching agent
code.  BAMLAdapter gives structured output in a single LLM call.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

import dspy
from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Output models (Pydantic — used by BAMLAdapter for structured extraction)
# ---------------------------------------------------------------------------


def _coerce_to_str_list(value: object) -> list[str]:
    """Coerce a list-typed field into a list of strings.

    Small instruction-tuned models (e.g. the 1.2B local fine-tune) often emit
    list-typed fields as one delimited string instead of a JSON array, which
    fails strict Pydantic list validation and aborts the stage. This
    BeforeValidator rescues that output by splitting on newline/semicolon
    delimiters. It is a no-op for values that are already lists, so capable
    models that emit proper arrays are unaffected.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        parts = re.split(r"[\n;]+", text)
        cleaned = [p.strip(" \t-*•").strip() for p in parts]
        cleaned = [p for p in cleaned if p]
        return cleaned or [text]
    return [str(value).strip()]


# A list[str] that tolerates a single delimited string from weaker models.
StrList = Annotated[list[str], BeforeValidator(_coerce_to_str_list)]


class StepSuggestionOutput(BaseModel):
    """Structured step-review output."""

    field: str = Field(desc="MADR field this suggestion targets")
    message: str = Field(desc="Constructive improvement suggestion (max 100 words)")
    severity: Literal["low", "medium", "high"] = Field(desc="Impact level")
    should_show: bool = Field(desc="False if the user's input is already adequate")
    references: StrList = Field(default_factory=list, desc="KB source references")
    needs_socratic: bool = Field(
        default=False,
        desc="True if this field contains ambiguities, assumptions, or gaps "
        "that would benefit from Socratic probing before proceeding",
    )
    socratic_reason: str = Field(
        default="",
        desc="Why Socratic probing is needed — the specific ambiguity or gap. "
        "Empty if needs_socratic is False.",
    )


class ReviewOutput(BaseModel):
    """Full ADR review output."""

    summary: str = Field(desc="Overall assessment")
    issues: list[str] = Field(default_factory=list, desc="Specific issues found")
    suggestions: list[str] = Field(default_factory=list, desc="Actionable improvements")


class ComplianceOutput(BaseModel):
    """Requirements compliance output."""

    assessment: str = Field(desc="Compliance summary")
    gaps: list[str] = Field(default_factory=list, desc="Missing or weak areas")
    suggestions: list[str] = Field(default_factory=list, desc="Improvement suggestions")


class ToMOutput(BaseModel):
    """Theory-of-Mind assessment output."""

    user_goals: list[str] = Field(default_factory=list, desc="Inferred user goals")
    confidence: float = Field(
        default=0.5, desc="Confidence that user is satisfied (0-1)"
    )
    scaffolding_level: Literal["minimal", "moderate", "detailed"] = Field(
        default="moderate", desc="How much guidance the user needs"
    )
    observation: str = Field(desc="Summary of user's mental model")


class PersonaOutput(BaseModel):
    """A single generated persona."""

    persona_id: str = Field(default="", desc="ID in PERSONA-NN format")
    name: str
    role: str
    goals: StrList = Field(default_factory=list)
    frustrations: StrList = Field(default_factory=list)


class PersonasOutput(BaseModel):
    """Container for generated personas."""

    personas: list[PersonaOutput] = Field(default_factory=list)


class GherkinOutput(BaseModel):
    """Generated Gherkin feature content."""

    scenario_id: str = Field(default="", desc="ID in BDD-NNN format")
    cites_story: str = Field(default="", desc="Story ID this scenario tests (STORY-NN)")
    cites_adr: str = Field(default="", desc="ADR ID this scenario relates to (ADR-NNN)")
    feature_name: str = Field(desc="Name of the Gherkin feature")
    gherkin: str = Field(desc="Complete Gherkin feature file content")

    @model_validator(mode="before")
    @classmethod
    def _coerce_str_to_model(cls, data: object) -> object:
        """Rescue weak models that emit the whole feature as a bare string.

        Sub-2B models sometimes return the raw .feature text instead of a
        structured object, which fails model validation and aborts the BDD
        stage. Wrapping the string preserves the generated Gherkin. No-op when
        the value is already a mapping, so capable models are unaffected.
        """
        if isinstance(data, str):
            text = data.strip()
            match = re.search(r"Feature:\s*(.+)", text)
            feature_name = match.group(1).strip() if match else "Generated Feature"
            return {"feature_name": feature_name, "gherkin": text}
        return data


class UserStoryItem(BaseModel):
    """A single user story."""

    story_id: str = Field(desc="ID in STORY-NN format")
    persona_id: str = Field(desc="References one of the provided persona ids")
    requirement_ids: StrList = Field(
        default_factory=list, desc="Subset of provided requirement ids"
    )
    statement: str = Field(desc="As a <persona>, I want <goal>, so that <benefit>")


class UserStoriesOutput(BaseModel):
    """Container for generated user stories."""

    stories: list[UserStoryItem] = Field(default_factory=list)


class RequirementItem(BaseModel):
    """A single structured requirement (ISO 29148 aligned)."""

    req_id: str = Field(desc="ID in REQ-<DOMAIN>-<NN> format")
    category: str = Field(
        desc="'functional' or 'non_functional'",
    )
    domain: str = Field(desc="Domain tag, e.g. AUTH, TIME, RPT, USABILITY, PERF")
    statement: str = Field(
        desc="The requirement statement — plain text only, do NOT embed source references",
    )
    rationale: str = Field(
        default="",
        desc="Why this requirement exists — traceability to stakeholder needs",
    )
    acceptance_criteria: str = Field(
        default="",
        desc="Measurable criterion for verification (effectiveness, efficiency, etc.)",
    )


class RequirementsOutput(BaseModel):
    """Container for generated requirements (ISO 29148 aligned)."""

    introduction: str = Field(
        default="",
        desc="1-2 paragraph introduction describing the purpose and audience of this SRS",
    )
    scope: str = Field(
        default="",
        desc="Description of the system scope, boundaries, and key interfaces",
    )
    requirements: list[RequirementItem] = Field(default_factory=list)


class ADRGenerationOutput(BaseModel):
    """Structured ADR generation output with traceable IDs."""

    adr_id: str = Field(desc="ID in ADR-NNN format")
    title: str = Field(desc="ADR title")
    context: str = Field(desc="Decision context")
    decision: str = Field(desc="The decision made")
    alternatives: StrList = Field(
        default_factory=list, desc="Considered alternatives"
    )
    consequences: str = Field(default="", desc="Consequences of the decision")
    # Trace-graph aligned fields (match MADR frontmatter conventions)
    implements: StrList = Field(
        default_factory=list,
        desc="Requirement IDs (REQ-XXX-NN format) from the provided requirements "
        "list that this ADR addresses. ONLY use IDs that appear in the "
        "requirements input. Leave empty if no matching requirement exists.",
    )
    supersedes: str = Field(
        default="", desc="ADR ID this decision supersedes (ADR-NNN)"
    )
    cited_kb_sources: StrList = Field(
        default_factory=list, desc="Referenced KB sources (KB:<source>:<chunk>)"
    )

    @field_validator("consequences", "supersedes", "context", "decision", mode="before")
    @classmethod
    def _coerce_to_str(cls, v: object) -> str:
        if v is None:
            return ""
        if isinstance(v, list):
            return "\n".join(str(item) for item in v)
        return str(v)


class SOWStakeholder(BaseModel):
    """A stakeholder in the Statement of Work."""

    role: str = Field(desc="e.g. 'Project Manager', 'Developer', 'Finance'")
    needs: StrList = Field(desc="What this stakeholder needs from the system")


class SOWOutput(BaseModel):
    """Structured Statement of Work."""

    project_name: str
    problem_statement: str = Field(desc="1-2 sentence problem description")
    stakeholders: list[SOWStakeholder] = Field(default_factory=list)
    constraints: StrList = Field(default_factory=list)
    out_of_scope: StrList = Field(default_factory=list)
    open_questions: StrList = Field(desc="Ambiguities that need clarification")


class SocraticOutput(BaseModel):
    """Socratic solicitor output."""

    question: str = Field(desc="Follow-up question or 'looks good' if complete")
    reasoning: str = Field(desc="Why this question matters")
    scaffolding_level: Literal["minimal", "moderate", "detailed"] = Field(
        default="moderate", desc="Adapted to user expertise"
    )


# ---------------------------------------------------------------------------
# Socratic cross-cutting consultation — judges + probe + resolution
# ---------------------------------------------------------------------------


class AdequacyVerdictOutput(BaseModel):
    """Verdict on whether user input is adequate for the requested artifact."""

    adequate: bool = Field(
        desc="True if the input contains enough grounded facts to proceed"
    )
    missing_facts: list[str] = Field(
        default_factory=list,
        desc="Specific facts the user did NOT provide that are needed",
    )
    rationale: str = Field(default="", desc="Brief explanation")


class GroundingVerdictOutput(BaseModel):
    """Verdict on whether LLM output is grounded in user input + KB."""

    grounded: bool = Field(
        desc="True if every claim in the output is supported by user input or KB"
    )
    ungrounded_claims: list[str] = Field(
        default_factory=list,
        desc="Specific claims the LLM added that are not in user input or KB",
    )
    rationale: str = Field(default="", desc="Brief explanation")


class SocraticProbeOutput(BaseModel):
    """A single Socratic probe — a thought-provoking question.

    NEVER propose solutions, candidates, or examples to choose from.
    NEVER lead the user toward an answer.  The probe must make the
    user think — by exposing hidden assumptions, pointing out a
    contradiction, asking why a quick decision was made, or surfacing
    a perspective the user has not yet considered.
    """

    question: str = Field(
        desc="One open-ended Socratic question. No suggestions, no candidates."
    )
    style: Literal["assumption", "contradiction", "depth", "perspective"] = Field(
        desc="What the probe targets"
    )
    rag_grounding: str = Field(
        default="",
        desc="Optional brief KB-grounded fact that informs the probe",
    )
    kb_facts_to_present: str = Field(
        default="",
        desc="Raw verbatim KB facts to surface to the user verbatim before the question. "
        "Only populate when KB context is genuinely relevant and the user lacks this "
        "information. Leave empty when the user already has sufficient context.",
    )


class ResolutionVerdictOutput(BaseModel):
    """Verdict on whether the user has resolved the issue and probing should stop."""

    resolved: bool = Field(
        desc="True if the user has demonstrated genuine reflection on the issue"
    )
    fatigue_detected: bool = Field(
        default=False,
        desc="True if the user appears tired, dismissive, or disengaged",
    )
    rationale: str = Field(default="")


class InputAdequacyJudge(dspy.Signature):
    """Judge whether the user's input contains enough grounded
    information for the requested artifact, OR whether key facts are
    missing or ambiguous so that Socratic clarification is needed.

    Be conservative: only flag inadequacy when concrete facts are
    missing.  Brevity alone does not imply inadequacy — an experienced
    user may state things tersely."""

    agent_name: str = dspy.InputField(desc="The agent making the request")
    what_was_asked: str = dspy.InputField(desc="The task or field being filled")
    user_input: str = dspy.InputField(desc="What the user provided")
    prior_context: str = dspy.InputField(
        desc="Existing artifact / partial state for context"
    )
    user_model: str = dspy.InputField(
        desc="Aggregated user model JSON from ToM (Tier 3)"
    )
    verdict: AdequacyVerdictOutput = dspy.OutputField()


class OutputGroundingJudge(dspy.Signature):
    """Judge whether the LLM output contains claims that are not
    supported by the user's input or KB context.  Hallucinations or
    over-reach (e.g. inventing requirements the user did not ask for)
    must be flagged as ungrounded claims."""

    user_input: str = dspy.InputField(desc="What the user originally provided")
    kb_context: str = dspy.InputField(desc="Retrieved KB chunks (may be empty)")
    llm_output: str = dspy.InputField(desc="The LLM-generated artifact text")
    verdict: GroundingVerdictOutput = dspy.OutputField()


class SocraticProbeGeneration(dspy.Signature):
    """Generate ONE Socratic probe — a thought-provoking question.

    LANGUAGE AND STYLE — read every rule carefully:
    * Write ONE direct question. No follow-up sub-questions.
    * No bullet points, numbered lists, or hyphens inside the question text.
    * Do NOT open with "When you say X" or "What do you mean by X".
    * Do NOT propose solutions, options, or examples to pick from.
    * Do NOT answer your own question or hint at the answer.
    * Use plain conversational language. Avoid jargon unless user_model
      confirms the user is an expert in that exact domain.
    * Keep the question short — prefer one sentence over two.

    COGNITIVE STRATEGY — pick ONE angle per turn:
    * assumption  — surface a hidden assumption the user is making.
    * contradiction — point out a tension with a prior decision or KB fact.
    * depth        — probe why a decision was made so quickly.
    * perspective  — raise a stakeholder angle the user has not considered.
    * Check conversation_history and DO NOT repeat an angle already used.

    TOM GUIDANCE — if tom_guidance is non-empty it overrides strategy:
    * If it signals frustration or overwhelm: ask the simplest possible
      question that focuses ONLY on the smallest first sub-problem. Validate
      the user's progress so far before probing.
    * If it signals expert level: skip definitions, probe execution logic.
    * If it signals fatigue: one short empathetic question, nothing complex.

    KB RECOLLECTION — if kb_context contains facts the user is missing:
    * Put the raw verbatim KB fact(s) in kb_facts_to_present (shown to user
      before the question). Then ask how that specific fact changes their
      approach — do NOT interpret the fact for them.
    """

    originating_agent: str = dspy.InputField(desc="The agent that requested probing")
    what_was_asked: str = dspy.InputField(desc="The task or field being filled")
    what_is_known: str = dspy.InputField(
        desc="The user's input + any structured state extracted so far"
    )
    suspect_output: str = dspy.InputField(
        desc="LLM output that triggered the probe (empty if input-side trigger)"
    )
    issue: str = dspy.InputField(desc="'ambiguous_input' or 'ungrounded_output'")
    missing_facts: str = dspy.InputField(
        desc="JSON list of missing facts identified by the judge"
    )
    ungrounded_claims: str = dspy.InputField(
        desc="JSON list of ungrounded LLM claims identified by the judge"
    )
    user_model: str = dspy.InputField(desc="ToM user model JSON (Tier 3)")
    tom_guidance: str = dspy.InputField(
        desc="Session-specific ToM guidance about the user's current emotional and "
        "cognitive state. Empty string means no special guidance — use defaults. "
        "Non-empty overrides the default cognitive strategy (see docstring)."
    )
    conversation_history: str = dspy.InputField(
        desc="Prior Socratic Q&A turns in this dialogue"
    )
    kb_context: str = dspy.InputField(desc="KB chunks for grounding the probe")
    probe: SocraticProbeOutput = dspy.OutputField()


class SocraticResolutionJudge(dspy.Signature):
    """Decide whether the user has reflected enough and probing should stop.

    The user is "resolved" when their answers show genuine engagement
    and the original ambiguity / ungrounded claim has been addressed.
    Detect fatigue from terse, dismissive, or repetitive answers."""

    what_was_asked: str = dspy.InputField()
    conversation_history: str = dspy.InputField(
        desc="All Socratic Q&A turns so far (most recent last)"
    )
    user_model: str = dspy.InputField(desc="ToM user model JSON")
    verdict: ResolutionVerdictOutput = dspy.OutputField()


# ---------------------------------------------------------------------------
# Dean — oversight and simplification of Socratic probes
# ---------------------------------------------------------------------------


class DeanReviewOutput(BaseModel):
    """Dean-polished Socratic question ready to show the user."""

    refined_question: str = Field(
        desc="The final question one direct sentence, no bullets/hyphens, "
        "no 'When you say X' opening, no jargon unless user is confirmed expert. "
        "Validates the user's progress when they are frustrated or overwhelmed."
    )
    changes_applied: list[str] = Field(
        default_factory=list,
        desc="Brief tags for changes made, e.g. ['simplified', 'removed_bullets', "
        "'reframed_opener']. Used for transparency logging.",
    )


class DeanReview(dspy.Signature):
    """Review and refine a draft Socratic question before it reaches the student.

    The Dean MUST enforce all of these rules on the refined_question:
    1. Exactly ONE question -- remove any secondary or follow-up sub-questions.
    2. No bullet points, numbered lists, or hyphens inside the question text.
    3. No opening with "When you say" or "What do you mean by".
    4. Plain language -- replace jargon with everyday terms unless the user
       model confirms the user is an expert in that specific area.
    5. If tom_guidance signals frustration or overwhelm: begin with a brief
       validating phrase ("You have already identified X -- now ...") before
       the question.
    6. Keep it short -- one sentence is ideal, two at most.
    7. Preserve the Socratic intent: the refined question must still make the
       user think, not lead them to an answer.
    """

    draft_question: str = dspy.InputField(desc="Draft Socratic question to review")
    conversation_history: str = dspy.InputField(
        desc="Recent Q&A turns used to check for repeated openers or angles"
    )
    tom_guidance: str = dspy.InputField(
        desc="ToM guidance about the user's current emotional and cognitive state"
    )
    user_model: str = dspy.InputField(desc="Aggregated user model JSON (Tier 3)")
    result: DeanReviewOutput = dspy.OutputField()


# ---------------------------------------------------------------------------
# Composing Stage ADR Agent
# ---------------------------------------------------------------------------


class ADRStepReview(dspy.Signature):
    """Review a single ADR field and suggest improvements.
    Only suggest when there is a genuine issue. Set should_show=False
    if the user's input is already adequate."""

    step_name: str = dspy.InputField(desc="MADR field name")
    step_value: str = dspy.InputField(desc="What the user entered")
    current_artifact: str = dspy.InputField(desc="Partial ADR JSON")
    kb_chunks: str = dspy.InputField(desc="Retrieved knowledge base references")
    suggestion: StepSuggestionOutput = dspy.OutputField()


class ADRFullReview(dspy.Signature):
    """Full review of a completed ADR — coherence, completeness, consistency."""

    adr_content: str = dspy.InputField(desc="Full ADR markdown text")
    prior_artifacts: str = dspy.InputField(desc="Summaries of existing ADRs")
    review: ReviewOutput = dspy.OutputField()


class ADRDirectionItem(BaseModel):
    """One suggested ADR topic the user should consider writing.

    NEVER propose how to solve the decision — only WHAT decision must
    be made and WHY it matters now, given the Brief and prior ADRs.
    """

    title: str = Field(
        desc="Short proposed ADR title — names the decision, not the answer "
        "(e.g. 'Choose primary datastore', NOT 'Use PostgreSQL')"
    )
    rationale: str = Field(
        desc="Why this decision is needed now, grounded in the Brief / prior "
        "ADRs / personas / stories. 1–3 sentences. No solution hints."
    )
    priority: Literal["high", "medium", "low"] = Field(
        desc="high = blocks downstream work; medium = soon; low = nice-to-have"
    )
    gap_filled: str = Field(
        default="",
        desc="Specific unresolved question or dependency this ADR closes "
        "(e.g. 'SOW lists multi-tenancy but no isolation strategy decided')",
    )


class ADRDirectionsOutput(BaseModel):
    """Container for suggested ADR directions, ranked by priority."""

    directions: list[ADRDirectionItem] = Field(default_factory=list)
    summary: str = Field(
        default="",
        desc="One-sentence overall assessment of decision-coverage so far",
    )


class SuggestADRDirections(dspy.Signature):
    """Suggest WHICH ADR topics the user should write next, given the
    project SOW, requirements (SRS), any already-written ADRs, and
    downstream artifacts (personas / user stories / Gherkin) when present.

    HARD CONSTRAINTS — read carefully:
    * NEVER propose a solution, technology, or chosen option.
    * NEVER phrase a title as an answer ("Use Kafka") — phrase it as
      a decision to be made ("Choose async messaging backbone").
    * Each suggestion must point to a concrete gap in the SOW/SRS or a
      dependency surfaced by prior ADRs / personas / stories.
    * PRIORITIZE uncovered requirements — the uncovered_requirements
      field lists REQ-IDs not yet addressed by any ADR. Focus new
      directions on filling those gaps.
    * Skip topics already covered by an existing ADR.
    * Re-rank priorities using the inferred user goals from the ToM
      user model when available — decisions that unblock the user's
      stated goals come first.
    * Return between 3 and 7 directions; fewer if there is little
      undecided ground left.
    """

    brief: str = dspy.InputField(
        desc="Statement of Work (SOW) — structured project scope and goals"
    )
    prior_adrs: str = dspy.InputField(
        desc="Concatenated existing ADR markdown (may be empty)"
    )
    downstream_artifacts: str = dspy.InputField(
        desc="SRS requirements and optional personas / user stories / Gherkin (may be empty)"
    )
    uncovered_requirements: str = dspy.InputField(
        desc="REQ-IDs from the SRS NOT yet referenced in any ADR implements field. "
        "Prioritize directions that would address these gaps. May be empty if all covered."
    )
    kb_chunks: str = dspy.InputField(
        desc="Retrieved KB references on architecture decision patterns (may be empty)"
    )
    user_model: str = dspy.InputField(
        desc="Aggregated ToM user model JSON — use to re-rank by inferred goals"
    )
    directions: ADRDirectionsOutput = dspy.OutputField()


# ---------------------------------------------------------------------------
# Expansion Stage — Requirement Agent
# ---------------------------------------------------------------------------


class RequirementCheck(dspy.Signature):
    """Check an ADR against relevant standards and literature (RAG-augmented)."""

    adr_content: str = dspy.InputField(desc="Full ADR markdown")
    retrieved_context: str = dspy.InputField(
        desc="Retrieved chunks from ISO / design-pattern corpus"
    )
    compliance: ComplianceOutput = dspy.OutputField()


# ---------------------------------------------------------------------------
# Expansion Stage — Theory of Mind Agent
# ---------------------------------------------------------------------------


class ToMRecommendationOutput(BaseModel):
    """Recommendation returned to a consulting agent (in-session)."""

    observation: str = Field(desc="Summary of the user's inferred mental state")
    recommendation: str = Field(desc="Suggested behavioural adjustment for the agent")
    scaffolding_level: Literal["minimal", "moderate", "detailed"] = Field(
        default="moderate"
    )
    confidence: float = Field(default=0.5, desc="Confidence in the assessment (0-1)")
    inferred_goals: list[str] = Field(default_factory=list)


class SessionAnalysisOutput(BaseModel):
    """Extracted per-session user model (after-session)."""

    inferred_intents: list[str] = Field(default_factory=list)
    interaction_patterns: list[str] = Field(default_factory=list)
    preferences: dict[str, str] = Field(default_factory=dict)
    emotional_states: list[str] = Field(default_factory=list)
    scaffolding_level: Literal["minimal", "moderate", "detailed"] = Field(
        default="moderate"
    )


class UserModelOutput(BaseModel):
    """Updated aggregated user model (after-session)."""

    preference_clusters: dict[str, list[str]] = Field(default_factory=dict)
    interaction_style: str = Field(default="unknown")
    scaffolding_preference: Literal["minimal", "moderate", "detailed"] = Field(
        default="moderate"
    )
    expertise_areas: list[str] = Field(default_factory=list)
    common_patterns: list[str] = Field(default_factory=list)


class ToMAssessment(dspy.Signature):
    """Assess user intent, satisfaction, and alignment across agent outputs."""

    user_interactions: str = dspy.InputField(
        desc="Chronological log of user inputs + agent suggestions"
    )
    agent_outputs: str = dspy.InputField(desc="Aggregated outputs from other agents")
    assessment: ToMOutput = dspy.OutputField()


class ToMConsultation(dspy.Signature):
    """In-session consultation: an agent asks the ToM about user intent.

    Used when an agent is uncertain about what the user wants, detects
    frustration, or needs to adapt its scaffolding level."""

    agent_query: str = dspy.InputField(
        desc="What the consulting agent is uncertain about"
    )
    current_context: str = dspy.InputField(
        desc="Current interaction context (recent events)"
    )
    user_model: str = dspy.InputField(desc="Aggregated user model JSON (Tier 3)")
    session_history: str = dspy.InputField(desc="Recent session interactions summary")
    recommendation: ToMRecommendationOutput = dspy.OutputField()


# -- Per-agent ToM consultation signatures ------------------------------------
# Each agent scenario gets its own signature with structured inputs.
# The docstring IS the prompt — no hardcoded strings in agent code.


class ToMADRStepConsult(dspy.Signature):
    """The user provided a short answer while creating an ADR field.
    Determine if this is intentional brevity (expert confidence) or
    uncertainty that needs scaffolding.  Recommend how the ADR agent
    should adjust its review — more probing or lighter touch."""

    step_name: str = dspy.InputField(desc="MADR field name being filled")
    step_value: str = dspy.InputField(desc="What the user entered (short)")
    partial_adr: str = dspy.InputField(desc="Partial ADR JSON so far")
    user_model: str = dspy.InputField(desc="Aggregated user model JSON (Tier 3)")
    session_history: str = dspy.InputField(desc="Recent session interactions summary")
    recommendation: ToMRecommendationOutput = dspy.OutputField()


class ToMSOWBriefConsult(dspy.Signature):
    """The user provided a short project brief for SOW extraction.
    Determine whether the user needs clarifying questions before
    extraction, or whether this is a deliberate minimal brief from
    an experienced user.  Recommend the appropriate scaffolding level."""

    brief_length: int = dspy.InputField(desc="Character count of the raw brief")
    user_model: str = dspy.InputField(desc="Aggregated user model JSON (Tier 3)")
    session_history: str = dspy.InputField(desc="Recent session interactions summary")
    recommendation: ToMRecommendationOutput = dspy.OutputField()


class ToMComplianceConsult(dspy.Signature):
    """About to run a compliance check on an ADR against standards.
    Determine the appropriate depth of compliance feedback based on
    the user's expertise level — detailed for novices, concise for
    experts.  Recommend the scaffolding level."""

    adr_length: int = dspy.InputField(desc="Character count of the ADR content")
    user_model: str = dspy.InputField(desc="Aggregated user model JSON (Tier 3)")
    session_history: str = dspy.InputField(desc="Recent session interactions summary")
    recommendation: ToMRecommendationOutput = dspy.OutputField()


class ToMSocraticConsult(dspy.Signature):
    """About to ask a Socratic follow-up question about a completed section.
    Determine if the user would benefit from more probing, or if they
    are showing signs of fatigue or confidence.  If the user is very
    confident, recommend skipping the follow-up entirely."""

    conversation_turns: int = dspy.InputField(
        desc="Number of Q&A turns so far in the Socratic dialogue"
    )
    user_model: str = dspy.InputField(desc="Aggregated user model JSON (Tier 3)")
    session_history: str = dspy.InputField(desc="Recent session interactions summary")
    recommendation: ToMRecommendationOutput = dspy.OutputField()


class ToMPersonaConsult(dspy.Signature):
    """About to generate user personas from project artifacts.
    Determine the appropriate level of detail for generated personas
    — detailed with rich backgrounds for novice users, concise for
    experts who just need the essentials."""

    artifacts_length: int = dspy.InputField(
        desc="Character count of combined ADR + SRS artifacts"
    )
    user_model: str = dspy.InputField(desc="Aggregated user model JSON (Tier 3)")
    session_history: str = dspy.InputField(desc="Recent session interactions summary")
    recommendation: ToMRecommendationOutput = dspy.OutputField()


class ToMStoryConsult(dspy.Signature):
    """About to generate user stories from personas and requirements.
    Determine whether stories should include detailed acceptance
    criteria (novice) or be concise (expert).  Recommend the
    appropriate scaffolding level for story generation."""

    personas_length: int = dspy.InputField(desc="Character count of personas JSON")
    requirements_length: int = dspy.InputField(
        desc="Character count of requirements content"
    )
    user_model: str = dspy.InputField(desc="Aggregated user model JSON (Tier 3)")
    session_history: str = dspy.InputField(desc="Recent session interactions summary")
    recommendation: ToMRecommendationOutput = dspy.OutputField()


class ToMSessionAnalysis(dspy.Signature):
    """After-session analysis: extract user preferences and emotional states
    from raw session data.  Produces a Tier-2 session model."""

    raw_session: str = dspy.InputField(desc="Raw session interactions JSON (Tier 1)")
    previous_user_model: str = dspy.InputField(
        desc="Current aggregated user model for context"
    )
    analysis: SessionAnalysisOutput = dspy.OutputField()


class ToMUserModelUpdate(dspy.Signature):
    """Aggregate a new session analysis into the overall user model.

    Merges new observations without duplicating existing patterns."""

    current_user_model: str = dspy.InputField(desc="Current user model JSON (Tier 3)")
    new_session_analysis: str = dspy.InputField(
        desc="Latest session analysis JSON (Tier 2)"
    )
    updated_model: UserModelOutput = dspy.OutputField()


# ---------------------------------------------------------------------------
class ToMChatObservation(dspy.Signature):
    """Passively observe a chat message exchange and infer user state.

    Analyse the user's message + assistant response for:
    - Emotional state (frustrated, confused, confident, engaged, etc.)
    - Whether the response adequately addressed the user's need
    - Whether a follow-up Socratic probe would improve clarity
    - Satisfaction signals (explicit or implicit)

    Return an observation and optionally recommend a Socratic probe."""

    user_message: str = dspy.InputField(desc="The user's chat message")
    assistant_response: str = dspy.InputField(desc="The assistant's response")
    chat_history_summary: str = dspy.InputField(
        desc="Summary of recent chat history (last 5 exchanges)"
    )
    user_model: str = dspy.InputField(desc="Aggregated user model JSON (Tier 3)")
    session_history: str = dspy.InputField(desc="Recent session interactions summary")
    observation: ToMRecommendationOutput = dspy.OutputField()


class ToMGiveSuggestionsOutput(BaseModel):
    """Structured output for proactive ToM suggestions."""

    should_probe: bool = Field(
        default=False, desc="Whether a Socratic probe should be injected"
    )
    probe_question: str = Field(
        default="", desc="The Socratic question to ask (if should_probe=True)"
    )
    probe_style: str = Field(
        default="clarification",
        desc="Probe style: clarification, assumption, decomposition, reframe",
    )
    satisfaction_score: float = Field(
        default=0.5, desc="Estimated user satisfaction (0=frustrated, 1=happy)"
    )
    reasoning: str = Field(default="", desc="Brief reasoning for the suggestion")
    needs_dean: bool = Field(
        default=False,
        desc="True when the Socratic Solicitor's next question should be reviewed by "
        "the Dean before being shown — e.g. user is frustrated, overwhelmed, or the "
        "conversation is becoming too complex.",
    )
    socratic_guidance: str = Field(
        default="",
        desc="Direct instruction for the Socratic Solicitor's next probe. "
        "Examples: 'User is frustrated with the foundational logic — simplify and "
        "validate their current understanding before probing further.' or "
        "'User appears overwhelmed by the full scope — focus only on the first "
        "and smallest decision that must be made.' Leave empty if no adaptation needed.",
    )


class ToMGiveSuggestions(dspy.Signature):
    """Proactively assess user state and guide the Socratic Solicitor.

    Called after every Socratic exchange to re-evaluate the user's
    cognitive and emotional state.  Uses both:
    - Recent session exchanges (short-term: frustration, fatigue, confusion)
    - Long-term user model (preferences, expertise, interaction style)

    Derive actionable guidance for the Solicitor's NEXT question:
    * If the user is frustrated or overwhelmed: set needs_dean=True and
      write concrete simplification guidance in socratic_guidance.
    * If the user repeats the same misunderstanding: flag it and suggest
      breaking down to a smaller sub-problem.
    * If the user's long-term profile shows strong expertise: write guidance
      to skip fundamentals and probe execution decisions instead.
    * If the user is confident and progressing: leave socratic_guidance empty.

    Only set should_probe=True when there is genuine ambiguity or the
    user seems stuck.  Avoid over-probing confident users."""

    recent_exchanges: str = dspy.InputField(
        desc="Last 3-5 chat exchanges (user+assistant)"
    )
    user_model: str = dspy.InputField(desc="Aggregated user model JSON (Tier 3)")
    current_state: str = dspy.InputField(
        desc="Current inferred state: frustration, engagement, confidence"
    )
    suggestion: ToMGiveSuggestionsOutput = dspy.OutputField()


# ---------------------------------------------------------------------------
# Expansion Stage — Socratic Solicitor
# ---------------------------------------------------------------------------


class SocraticReview(dspy.Signature):
    """Ask a Socratic follow-up question about a completed section.
    If the section is already thorough, respond with 'looks good'."""

    section_content: str = dspy.InputField(desc="The completed section text")
    conversation_history: str = dspy.InputField(desc="Prior Q&A turns")
    tom_state: str = dspy.InputField(desc="Current ToM confidence/scaffolding")
    kb_context: str = dspy.InputField(desc="Relevant KB references")
    response: SocraticOutput = dspy.OutputField()


# ---------------------------------------------------------------------------
# Generation Stage — BDD Agent
# ---------------------------------------------------------------------------


class PersonaGeneration(dspy.Signature):
    """Generate user personas from ADR + SRS context.
    Assign each persona a unique persona_id in PERSONA-NN format."""

    artifacts: str = dspy.InputField(desc="Combined ADR + SRS content")
    personas: list[PersonaOutput] = dspy.OutputField()


class GherkinGeneration(dspy.Signature):
    """Generate BDD Gherkin scenarios from personas + requirements.
    Include scenario_id (BDD-NNN), cites_story (STORY-NN), and cites_adr (ADR-NNN)."""

    persona: str = dspy.InputField(desc="Single persona JSON object")
    requirements: str = dspy.InputField(
        desc="Relevant requirements / acceptance criteria"
    )
    gherkin: GherkinOutput = dspy.OutputField()


class RequirementGeneration(dspy.Signature):
    """Generate structured requirements from a SOW (ISO 29148 aligned).

    Each requirement gets a unique req_id in REQ-<DOMAIN>-<NN> format.
    Classify each requirement as 'functional' or 'non_functional'.
    Functional requirements cover system operation capabilities.
    Non-functional requirements cover usability, quality-in-use
    (measurable effectiveness, efficiency, satisfaction, avoidance of harm),
    performance, security, and other quality attributes.
    Include rationale and measurable acceptance_criteria where possible.
    IMPORTANT: The statement field must contain ONLY the requirement text.
    Do NOT embed source references, citations, or SOW section names in the
    statement.  Source traceability is handled separately."""

    sow_content: str = dspy.InputField(desc="Statement of Work markdown")
    kb_context: str = dspy.InputField(desc="Relevant standards from knowledge base")
    requirements: RequirementsOutput = dspy.OutputField()


class ADRGeneration(dspy.Signature):
    """Generate a structured ADR (Architecture Decision Record) in MADR format.

    The ADR must address a specific decision topic. The `implements` field
    must ONLY contain requirement IDs (REQ-XXX-NN) that exist in the provided
    requirements list. Do NOT invent requirement IDs. If no requirements are
    provided or none match, leave `implements` empty.

    Cite relevant KB sources using KB:<source>:<chunk> format.
    Reference prior ADRs only if superseding one."""

    topic: str = dspy.InputField(desc="Decision topic or question")
    requirements: str = dspy.InputField(
        desc="JSON list of requirements with IDs (REQ-XXX-NN). "
        "Only these IDs may appear in the implements field."
    )
    prior_adrs: str = dspy.InputField(desc="JSON list of prior ADR summaries")
    kb_context: str = dspy.InputField(desc="Retrieved knowledge base references")
    adr: ADRGenerationOutput = dspy.OutputField()


class ADRDomainClassification(dspy.Signature):
    """Classify an ADR's decision area.

    Return exactly one of: auth, storage, api, deployment, frontend,
    integration, monitoring, testing, data-model, other."""

    adr_title: str = dspy.InputField()
    adr_context: str = dspy.InputField()
    domain_tag: str = dspy.OutputField()


class UserStoryGeneration(dspy.Signature):
    """Generate user stories in 'As a ..., I want ..., so that ...' format.

    Each story must reference one persona and one or more requirements."""

    personas: str = dspy.InputField(desc="JSON list of available personas with ids")
    requirements: str = dspy.InputField(desc="JSON list of requirements with ids")
    adrs: str = dspy.InputField(desc="JSON list of accepted ADRs (context only)")
    stories: UserStoriesOutput = dspy.OutputField()


# ---------------------------------------------------------------------------
# Intake Stage — SOW Agent
# ---------------------------------------------------------------------------


class ExtractSOW(dspy.Signature):
    """Extract a structured Statement of Work from a raw project brief.
    Identify ambiguities as open_questions — these will be explored
    through Socratic dialogue in the next stage."""

    raw_brief: str = dspy.InputField(desc="Unstructured client brief or meeting notes")
    kb_context: str = dspy.InputField(desc="Relevant templates and standards from KB")
    sow:    SOWOutput = dspy.OutputField()


# ---------------------------------------------------------------------------
# Per-section SOW generation (incremental)
# ---------------------------------------------------------------------------


class SOWSectionOutput(BaseModel):
    """Output for a single SOW section generated incrementally."""

    content: str = Field(desc="The generated content for this section (markdown)")
    needs_socratic: bool = Field(
        default=False,
        desc="True if this section contains ambiguities, assumptions, or "
        "gaps that would benefit from Socratic probing",
    )
    socratic_reason: str = Field(
        default="",
        desc="Why Socratic probing is needed — the specific ambiguity, "
        "assumption, or gap identified. Empty if needs_socratic is False.",
    )


class GenerateSOWSection(dspy.Signature):
    """Generate ONE section of a Statement of Work incrementally.

    Given the raw brief, the section to generate, any prior sections
    already produced, and relevant KB context, produce the content for
    this specific section.

    Also judge whether this section contains ambiguities, unstated
    assumptions, or knowledge gaps that would benefit from Socratic
    probing.  Set needs_socratic=True with a reason if so.

    Section types and their expected content:
    - project_name: A concise project title (1 line)
    - problem_statement: 1-2 sentence problem description
    - stakeholders: Markdown list of stakeholders with roles and needs
      (use ### for each role, - for needs)
    - constraints: Bullet list of constraints
    - out_of_scope: Bullet list of items explicitly out of scope
    - open_questions: Bullet list of ambiguities needing clarification
    """

    raw_brief: str = dspy.InputField(desc="Unstructured client brief or meeting notes")
    section_name: str = dspy.InputField(
        desc="Which section to generate: project_name | problem_statement | "
        "stakeholders | constraints | out_of_scope | open_questions"
    )
    prior_sections: str = dspy.InputField(
        desc="Already-generated sections as markdown (for coherence). "
        "Empty string if this is the first section."
    )
    kb_context: str = dspy.InputField(
        desc="Relevant templates and standards from KB (may be empty)"
    )
    section: SOWSectionOutput = dspy.OutputField()


# ---------------------------------------------------------------------------
# Traceability Agent — Conflict & Overlap Detection
# ---------------------------------------------------------------------------


class TraceabilityIssue(BaseModel):
    """A single traceability finding."""

    severity: Literal["info", "warning", "error"] = Field(desc="Issue severity")
    message: str = Field(desc="Human-readable description of the issue")
    source_id: str = Field(default="", desc="ID of the source artifact")
    target_id: str = Field(default="", desc="ID of the related artifact")
    suggestion: str = Field(default="", desc="Suggested action to resolve")


class TraceabilityCheckOutput(BaseModel):
    """Structured output for traceability analysis."""

    issues: list[TraceabilityIssue] = Field(
        default_factory=list, desc="List of traceability issues found"
    )
    has_conflicts: bool = Field(
        default=False, desc="True if semantic conflicts detected"
    )
    coverage_assessment: str = Field(
        default="", desc="Brief assessment of traceability coverage"
    )


class ConflictDetection(dspy.Signature):
    """Detect semantic conflicts and overlaps between a new artifact and existing related artifacts.

    Given a new or modified artifact and a set of existing artifacts that are
    linked (directly or transitively) to it, detect:
    1. Semantic conflicts: contradicting decisions or requirements
    2. Overlaps: duplicate coverage that should be consolidated
    3. Stale references: links that no longer make sense given the new content
    4. Coverage gaps: requirements that lost coverage due to changes
    """

    new_artifact: str = dspy.InputField(
        desc="The new or modified artifact content (full markdown)"
    )
    new_artifact_id: str = dspy.InputField(
        desc="ID of the new/modified artifact (e.g. ADR-0003)"
    )
    related_artifacts: str = dspy.InputField(
        desc="Markdown-formatted list of related artifacts with their content "
        "(each prefixed with its ID and type)"
    )
    graph_context: str = dspy.InputField(
        desc="Summary of the traceability graph edges involving these artifacts"
    )
    result: TraceabilityCheckOutput = dspy.OutputField()

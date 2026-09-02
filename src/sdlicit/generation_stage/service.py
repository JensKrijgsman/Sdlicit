"""Generation stage service — delegates to the Orchestrator.

Each generation endpoint follows the same Socratic-aware pattern as
``intake_stage`` SOW creation:

1. **Input adequacy check** — if the user-provided context is too thin,
   return a ``socratic_probe`` and let the CLI ask the user. The CLI
   re-calls the same endpoint with the answer in ``clarifications``.
2. **Generate** the artifact with the Orchestrator.
3. **Output grounding check** — if the generated artifact contains
   claims not in the user's input, return both the artifact and a
   probe so the user can decide whether to accept or revise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from sdlicit.agents.base import Clarification, SocraticProbe
from sdlicit.helpers.artifact_naming import (
    resolve_bdd_meta,
    resolve_personas_meta,
    resolve_srs_meta,
    resolve_stories_meta,
)
from sdlicit.helpers.project_files import read_all_adrs
from sdlicit.helpers.socratic_envelope import to_clarifications, to_probe_out

from .models import (
    ConsultSocraticRequest,
    ConsultSocraticResponse,
    GenerateGherkinRequest,
    GenerateGherkinResponse,
    GeneratePersonasRequest,
    GeneratePersonasResponse,
    GenerateSRSRequest,
    GenerateSRSResponse,
    GenerateStoriesRequest,
    GenerateStoriesResponse,
    Persona,
    Requirement,
    UserStory,
)

if TYPE_CHECKING:
    from sdlicit.orchestrator import Orchestrator


# ---------------------------------------------------------------------------
# Helpers — shared Socratic envelope
# ---------------------------------------------------------------------------


def _format_history(clarifications: list[Clarification]) -> str:
    if not clarifications:
        return ""
    return "\n".join(f"Q: {c.question}\nA: {c.answer}" for c in clarifications)


async def _input_probe(
    orchestrator: "Orchestrator",
    agent_name: str,
    what_was_asked: str,
    user_input: str,
    clarifications: list[Clarification],
) -> SocraticProbe | None:
    """Run input-adequacy judge; return a probe if input is thin."""
    socratic = orchestrator.socratic
    if socratic is None or not user_input.strip():
        return None
    adequate, missing = await socratic.judge_input(
        agent_name=agent_name,
        what_was_asked=what_was_asked,
        user_input=user_input,
    )
    if adequate:
        return None
    return await socratic.consult(
        originating_agent=agent_name,
        what_was_asked=what_was_asked,
        what_is_known=user_input,
        clarifications=clarifications,
        issue="ambiguous_input",
        missing_facts=missing,
    )


async def _output_probe(
    orchestrator: "Orchestrator",
    agent_name: str,
    what_was_asked: str,
    user_input: str,
    output: str,
    clarifications: list[Clarification],
) -> SocraticProbe | None:
    """Run output-grounding judge; return a probe if output is ungrounded."""
    socratic = orchestrator.socratic
    if socratic is None or not output.strip() or not user_input.strip():
        return None
    grounded, claims = await socratic.judge_output(
        user_input=user_input,
        llm_output=output,
        kb_context="",
    )
    if grounded:
        return None
    return await socratic.consult(
        originating_agent=agent_name,
        what_was_asked=what_was_asked,
        what_is_known=user_input,
        clarifications=clarifications,
        suspect_output=output,
        issue="ungrounded_output",
        ungrounded_claims=claims,
    )


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------


def _parse_personas(raw: str) -> list[Persona]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    items = parsed.get("personas", []) if isinstance(parsed, dict) else parsed
    out: list[Persona] = []
    for p in items or []:
        try:
            out.append(Persona(**p))
        except (TypeError, ValueError):
            continue
    return out


async def generate_personas(
    request: GeneratePersonasRequest,
    orchestrator: "Orchestrator",
) -> GeneratePersonasResponse:
    """Generate user personas from project artifacts (Socratic-aware)."""
    clarifications = to_clarifications(request.clarifications)
    if orchestrator.socratic is not None:
        for c in clarifications:
            orchestrator.socratic.log_answer("BDDAgent", "personas", c)

    adr_contents = read_all_adrs(Path(request.project_dir))
    user_input = "\n\n".join(
        [request.srs_content or ""] + [c[:500] for c in adr_contents[:3]]
    ).strip()
    history = _format_history(clarifications)
    effective_input = (
        f"{user_input}\n\n[clarifications]\n{history}" if history else user_input
    )

    probe = await _input_probe(
        orchestrator, "BDDAgent", "personas", effective_input, clarifications
    )
    if probe is not None:
        return GeneratePersonasResponse(socratic_probe=to_probe_out(probe))

    result = await orchestrator.bdd.generate_personas(
        json.dumps({"adrs": adr_contents[:5], "srs": request.srs_content})
    )
    personas = _parse_personas(result.raw_text)

    out_probe = await _output_probe(
        orchestrator,
        "BDDAgent",
        "personas",
        effective_input,
        result.raw_text,
        clarifications,
    )
    return GeneratePersonasResponse(
        personas=personas,
        raw_suggestion=result.raw_text,
        artifact_meta=resolve_personas_meta() if personas else None,
        socratic_probe=to_probe_out(out_probe),
    )


# ---------------------------------------------------------------------------
# Gherkin
# ---------------------------------------------------------------------------


async def generate_gherkin(
    request: GenerateGherkinRequest,
    orchestrator: "Orchestrator",
) -> GenerateGherkinResponse:
    """Generate Gherkin scenarios from validated personas (Socratic-aware)."""
    clarifications = to_clarifications(request.clarifications)
    if orchestrator.socratic is not None:
        for c in clarifications:
            orchestrator.socratic.log_answer("BDDAgent", "gherkin", c)

    personas_data = (
        [p.model_dump() for p in request.personas] if request.personas else []
    )
    user_input = json.dumps(
        {"personas": personas_data, "requirements": request.requirements}
    )
    history = _format_history(clarifications)
    effective_input = (
        f"{user_input}\n\n[clarifications]\n{history}" if history else user_input
    )

    probe = await _input_probe(
        orchestrator, "BDDAgent", "gherkin", effective_input, clarifications
    )
    if probe is not None:
        return GenerateGherkinResponse(socratic_probe=to_probe_out(probe))

    result = await orchestrator.bdd.generate_gherkin(
        json.dumps(personas_data[0] if personas_data else {}),
        request.requirements,
    )

    out_probe = await _output_probe(
        orchestrator,
        "BDDAgent",
        "gherkin",
        effective_input,
        result.raw_text,
        clarifications,
    )
    # Determine persona name for the BDD filename
    persona_name = "scenarios"
    if request.personas:
        persona_name = request.personas[0].name

    return GenerateGherkinResponse(
        gherkin=result.raw_text,
        raw_suggestion=result.raw_text,
        artifact_meta=resolve_bdd_meta(persona_name) if result.raw_text else None,
        socratic_probe=to_probe_out(out_probe),
    )


# ---------------------------------------------------------------------------
# User stories
# ---------------------------------------------------------------------------


def _parse_stories(raw: str) -> list[UserStory]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    items = parsed.get("stories", []) if isinstance(parsed, dict) else parsed
    out: list[UserStory] = []
    for s in items or []:
        try:
            out.append(UserStory(**s))
        except (TypeError, ValueError):
            continue
    return out


async def generate_stories(
    request: GenerateStoriesRequest,
    orchestrator: "Orchestrator",
) -> GenerateStoriesResponse:
    """Generate user stories (Socratic-aware)."""
    clarifications = to_clarifications(request.clarifications)
    if orchestrator.socratic is not None:
        for c in clarifications:
            orchestrator.socratic.log_answer("UserStoryAgent", "stories", c)

    adr_contents = read_all_adrs(Path(request.project_dir))
    user_input = f"personas={request.personas}\nrequirements={request.requirements}"
    history = _format_history(clarifications)
    effective_input = (
        f"{user_input}\n\n[clarifications]\n{history}" if history else user_input
    )

    probe = await _input_probe(
        orchestrator, "UserStoryAgent", "stories", effective_input, clarifications
    )
    if probe is not None:
        return GenerateStoriesResponse(socratic_probe=to_probe_out(probe))

    result = await orchestrator.user_story.generate(
        request.personas,
        request.requirements,
        json.dumps(adr_contents[:5]),
    )
    stories = _parse_stories(result.raw_text)

    out_probe = await _output_probe(
        orchestrator,
        "UserStoryAgent",
        "stories",
        effective_input,
        result.raw_text,
        clarifications,
    )
    return GenerateStoriesResponse(
        stories=stories,
        raw_suggestion=result.raw_text,
        artifact_meta=resolve_stories_meta() if stories else None,
        socratic_probe=to_probe_out(out_probe),
    )


# ---------------------------------------------------------------------------
# SRS — generated from a SOW
# ---------------------------------------------------------------------------


def _parse_requirements(raw: str) -> list[Requirement]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    items = parsed.get("requirements", []) if isinstance(parsed, dict) else parsed
    out: list[Requirement] = []
    for r in items or []:
        try:
            out.append(Requirement(**r))
        except (TypeError, ValueError):
            continue
    return out


def _render_srs_markdown(
    requirements: list[Requirement],
    introduction: str = "",
    scope: str = "",
) -> str:
    lines = ["# Software Requirements Specification", ""]

    fr = [r for r in requirements if r.category == "functional"]
    nfr = [r for r in requirements if r.category != "functional"]

    if fr:
        lines += ["## Functional Requirements", ""]
        for r in fr:
            lines.append(f"- [{r.req_id}] {r.statement}")
            if r.rationale:
                lines.append(f"  - _Rationale:_ {r.rationale}")
            if r.acceptance_criteria:
                lines.append(f"  - _Acceptance:_ {r.acceptance_criteria}")
        lines.append("")

    if nfr:
        lines += ["## Non-Functional Requirements", ""]
        for r in nfr:
            lines.append(f"- [{r.req_id}] {r.statement}")
            if r.rationale:
                lines.append(f"  - _Rationale:_ {r.rationale}")
            if r.acceptance_criteria:
                lines.append(f"  - _Acceptance:_ {r.acceptance_criteria}")
        lines.append("")

    return "\n".join(lines)


def _latest_sow_content(project_dir: str) -> str:
    artifacts = Path(project_dir) / ".sdlicit" / "artifacts"
    if not artifacts.is_dir():
        return ""
    # Canonical single-file location
    canonical = artifacts / "sow.md"
    if canonical.is_file():
        try:
            return canonical.read_text(encoding="utf-8")
        except OSError:
            return ""
    # Legacy fallback: SOW-*.md
    sows = sorted(artifacts.glob("SOW-*.md"))
    if not sows:
        return ""
    try:
        return sows[-1].read_text(encoding="utf-8")
    except OSError:
        return ""


async def generate_srs(
    request: GenerateSRSRequest,
    orchestrator: "Orchestrator",
) -> GenerateSRSResponse:
    """Generate a structured SRS from the SOW (Socratic-aware)."""
    clarifications = to_clarifications(request.clarifications)
    if orchestrator.socratic is not None:
        for c in clarifications:
            orchestrator.socratic.log_answer("RequirementAgent", "SRS", c)

    sow_content = request.sow_content or _latest_sow_content(request.project_dir)
    history = _format_history(clarifications)
    effective_input = (
        f"{sow_content}\n\n[clarifications]\n{history}" if history else sow_content
    )

    probe = await _input_probe(
        orchestrator, "RequirementAgent", "SRS", effective_input, clarifications
    )
    if probe is not None:
        return GenerateSRSResponse(socratic_probe=to_probe_out(probe))

    result = await orchestrator.generate_srs(sow_content)
    requirements = _parse_requirements(result.raw_text)

    # Extract introduction / scope from SOW context
    introduction = ""
    scope = ""
    try:
        parsed = json.loads(result.raw_text) if result.raw_text else {}
        introduction = parsed.get("introduction", "")
        scope = parsed.get("scope", "")
    except (json.JSONDecodeError, TypeError):
        pass

    # KB verification — per-requirement query via kb_router (preferred) or raw kb
    kb_source = orchestrator.kb_router or orchestrator.kb
    if kb_source is not None:
        for req in requirements:
            try:
                if orchestrator.kb_router is not None:
                    chunks = await orchestrator.kb_router.query(
                        req.statement[:200],
                        probe_first=False,
                    )
                else:
                    chunks = await orchestrator.kb.query(
                        req.statement[:200], mode="hybrid"
                    )
                if chunks:
                    best_score = max((getattr(c, "score", 0.0) or 0.0) for c in chunks)
                    # Check text overlap for stronger grounding signal
                    stmt_lower = req.statement.lower()[:200]
                    text_match = any(
                        stmt_lower[:40] in (c.text or "").lower()[:200]
                        or (c.text or "").lower()[:40] in stmt_lower
                        for c in chunks
                        if c.text
                    )
                    req.kb_grounded = text_match or best_score >= 0.25
                else:
                    req.kb_grounded = False
                if not req.kb_grounded:
                    req.kb_ungrounded_claims = [req.statement[:120]]
            except Exception:
                pass  # leave as None

    srs_md = _render_srs_markdown(requirements, introduction, scope)

    out_probe = await _output_probe(
        orchestrator,
        "RequirementAgent",
        "SRS",
        effective_input,
        srs_md or result.raw_text,
        clarifications,
    )
    return GenerateSRSResponse(
        introduction=introduction,
        scope=scope,
        requirements=requirements,
        srs_markdown=srs_md,
        raw_suggestion=result.raw_text,
        artifact_meta=resolve_srs_meta() if requirements else None,
        socratic_probe=to_probe_out(out_probe),
    )


# ---------------------------------------------------------------------------
# Cross-cutting Socratic consultation (REST mirror of MCP tool)
# ---------------------------------------------------------------------------


async def consult_socratic(
    request: ConsultSocraticRequest,
    orchestrator: "Orchestrator",
) -> ConsultSocraticResponse:
    """Free-form Socratic probe — used by the guided flow."""
    socratic = orchestrator.socratic
    if socratic is None:
        return ConsultSocraticResponse(status="disabled")

    clarifications = to_clarifications(request.clarifications)
    issue: Literal["ambiguous_input", "ungrounded_output"] = (
        "ungrounded_output"
        if request.issue == "ungrounded_output"
        else "ambiguous_input"
    )
    probe = await socratic.consult(
        originating_agent=request.originating_agent,
        what_was_asked=request.what_was_asked,
        what_is_known=request.what_is_known,
        clarifications=clarifications,
        suspect_output=request.suspect_output,
        issue=issue,
    )
    if probe is None:
        return ConsultSocraticResponse(status="skipped")
    return ConsultSocraticResponse(status="probe", probe=to_probe_out(probe))

"""Business logic for the composing stage.

Delegates to the Orchestrator directly — no A2A wrapping.
The backend reads prior ADRs from disk using project_dir.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING

from sdlicit.helpers.artifact_naming import resolve_adr_meta, resolve_sow_meta
from sdlicit.helpers.kb_access import retrieve_context
from sdlicit.helpers.project_files import list_adr_files, read_all_adrs
from sdlicit.helpers.socratic_envelope import to_clarifications
from sdlicit.logging import get_logger

from .models import (
    ADRDirectionItem,
    AnalyseRequest,
    AnalyseResponse,
    CreateSOWRequest,
    CreateSOWResponse,
    RegenerateSectionRequest,
    RegenerateSectionResponse,
    SocraticProbeOut,
    StepEventRequest,
    StepEventResponse,
    StepSuggestion,
    SuggestDirectionsRequest,
    SuggestDirectionsResponse,
    SupersedesHint,
)

if TYPE_CHECKING:
    from sdlicit.orchestrator import Orchestrator

_log = get_logger("route")

# Stop-words excluded from title similarity matching
_STOP_WORDS = frozenset(
    ["a", "an", "the", "and", "or", "of", "for", "in", "on", "to", "with", "by", "is", "are", "was", "were", "be", "been", "use", "using", "choose", "select", "decision", "record"]
)


def _title_tokens(title: str) -> set[str]:
    """Extract meaningful lowercase tokens from an ADR title."""
    import re

    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if w not in _STOP_WORDS and len(w) > 2}


def _detect_supersession(new_title: str, project_dir: Path) -> SupersedesHint | None:
    """Compare the new ADR title against existing ADRs for topic overlap.

    Uses Jaccard similarity on title tokens.  A threshold of 0.4
    (less than half the words match) keeps false positives low while
    catching clear topic collisions like "Use PostgreSQL for storage"
    vs "Select PostgreSQL as primary database".
    """
    new_tokens = _title_tokens(new_title)
    if len(new_tokens) < 2:
        return None

    existing = list_adr_files(project_dir)
    best_score = 0.0
    best_entry: dict[str, str | None] | None = None

    for entry in existing:
        existing_title = entry.get("title") or ""
        existing_tokens = _title_tokens(existing_title)
        if not existing_tokens:
            continue
        intersection = new_tokens & existing_tokens
        union = new_tokens | existing_tokens
        jaccard = len(intersection) / len(union) if union else 0.0
        if jaccard > best_score:
            best_score = jaccard
            best_entry = entry

    if best_score >= 0.4 and best_entry is not None:
        adr_id = (best_entry.get("filename") or "").replace(".md", "")
        adr_title = best_entry.get("title") or adr_id
        _log.info(
            "[supersession] Detected overlap (%.2f) between '%s' and '%s'",
            best_score,
            new_title,
            adr_title,
        )
        return SupersedesHint(
            adr_id=adr_id,
            adr_title=adr_title,
            reason=f"Topic overlap ({best_score:.0%}) with existing ADR",
        )
    return None


async def handle_step_event(
    request: StepEventRequest,
    orchestrator: Orchestrator,
) -> StepEventResponse:
    """Forward a single step to the Orchestrator and collect suggestions.

    Does not read prior ADRs from disk: a single field review never used
    them (see ADRAgent.review_step), so the read was pure waste.
    """
    clarifications = to_clarifications(request.clarifications)

    result = await orchestrator.review_step(
        step_name=request.step_name,
        step_value=request.step_value,
        partial_adr=request.partial_fields,
        clarifications=clarifications,
    )

    suggestion: StepSuggestion | None = None
    compliance: str | None = None
    probe_out: SocraticProbeOut | None = None

    if result.socratic_probe is not None:
        p = result.socratic_probe
        probe_out = SocraticProbeOut(
            probe_id=p.probe_id,
            question=p.question,
            style=p.style,
            originating_agent=p.originating_agent,
            what_was_asked=p.what_was_asked,
            turn=p.turn,
            max_turns=p.max_turns,
            rag_grounding=p.rag_grounding,
        )

    for s in result.suggestions:
        if not s.should_show:
            continue
        if suggestion is None:
            suggestion = StepSuggestion(
                agent_name="ADR Agent",
                field=s.field,
                message=s.message,
                severity=s.severity,
            )

    if result.compliance_notes:
        compliance = result.compliance_notes

    # Resolve artifact_meta when the title step is processed
    artifact_meta = None
    supersedes_hint = None
    if request.step_name == "title" and request.step_value and request.project_dir:
        artifact_meta = resolve_adr_meta(
            Path(request.project_dir), str(request.step_value)
        )
        supersedes_hint = _detect_supersession(
            str(request.step_value), Path(request.project_dir)
        )

    return StepEventResponse(
        suggestion=suggestion,
        compliance=compliance,
        artifact_meta=artifact_meta,
        socratic_probe=probe_out,
        supersedes_hint=supersedes_hint,
    )


async def analyse_input(
    request: AnalyseRequest,
    orchestrator: Orchestrator,
) -> AnalyseResponse:
    """Full-sweep analysis of user input / completed ADR."""
    prior_adrs: list[str] = []
    if request.project_dir:
        prior_adrs = read_all_adrs(Path(request.project_dir))

    result = await orchestrator.full_review(request.user_input, prior_adrs)
    texts = [s.message for s in result.suggestions]
    if result.raw_text:
        texts.insert(0, result.raw_text)
    return AnalyseResponse(
        ok=True,
        suggestions=texts or ["No suggestions — looks good."],
    )


async def suggest_adr_directions(
    request: SuggestDirectionsRequest,
    orchestrator: Orchestrator,
) -> SuggestDirectionsResponse:
    """Return a ranked list of ADR topics the user should consider writing."""
    prior_adrs: list[str] = []
    if request.project_dir:
        prior_adrs = read_all_adrs(Path(request.project_dir))

    result = await orchestrator.suggest_adr_directions(
        brief=request.brief,
        prior_adrs=prior_adrs,
        downstream_artifacts=request.downstream_artifacts,
        uncovered_requirements=request.uncovered_requirements,
    )

    items = [
        ADRDirectionItem(
            title=d.title,
            rationale=d.rationale,
            priority=d.priority,
            gap_filled=d.gap_filled,
        )
        for d in result.adr_directions
    ]
    return SuggestDirectionsResponse(
        ok=True,
        summary=result.raw_text,
        directions=items,
    )


# ---------------------------------------------------------------------------
# SOW creation (merged from intake_stage)
# ---------------------------------------------------------------------------


async def create_sow(
    request: CreateSOWRequest,
    orchestrator: Orchestrator,
) -> CreateSOWResponse:
    """Run the SOW agent on a raw brief."""
    clarifications = to_clarifications(request.clarifications)
    result = await orchestrator.create_sow(
        request.raw_brief, clarifications=clarifications
    )

    probe_out: SocraticProbeOut | None = None
    if result.socratic_probe is not None:
        p = result.socratic_probe
        probe_out = SocraticProbeOut(
            probe_id=p.probe_id,
            question=p.question,
            style=p.style,
            originating_agent=p.originating_agent,
            what_was_asked=p.what_was_asked,
            turn=p.turn,
            max_turns=p.max_turns,
            rag_grounding=p.rag_grounding,
            kb_facts=p.kb_facts,
            transparency_events=list(p.transparency_events),
        )

    return CreateSOWResponse(
        sow_markdown=result.raw_text or "",
        artifact_meta=resolve_sow_meta() if result.raw_text else None,
        socratic_probe=probe_out,
    )


def _sse(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"


async def create_sow_stream(
    request: CreateSOWRequest,
    orchestrator: Orchestrator,
) -> AsyncGenerator[str, None]:
    """Yield SSE events for incremental SOW generation."""
    clarifications = to_clarifications(request.clarifications)
    async for event in orchestrator.create_sow_stream(
        request.raw_brief, clarifications=clarifications
    ):
        yield _sse(event)


async def regenerate_section(
    request: RegenerateSectionRequest,
    orchestrator: Orchestrator,
) -> RegenerateSectionResponse:
    """Regenerate a single SOW section with user feedback folded in."""
    from sdlicit.agents.llm.dspy_modules import GenerateSOWSection

    effective_brief = request.raw_brief
    if request.user_feedback:
        effective_brief += (
            f"\n\n[user feedback on {request.section_name}]\n{request.user_feedback}"
        )
    if request.current_content:
        effective_brief += f"\n\n[current {request.section_name} to improve]\n{request.current_content}"

    # Get KB context
    kb_text = await retrieve_context(
        f"statement of work {request.section_name}",
        kb_router=orchestrator.kb_router,
        kb=orchestrator.kb,
    )

    result = await orchestrator.sow._llm.predict(
        GenerateSOWSection,
        raw_brief=effective_brief,
        section_name=request.section_name,
        prior_sections=request.prior_sections,
        kb_context=kb_text,
    )

    section_out = getattr(result, "section", None)
    content = getattr(section_out, "content", "") if section_out else ""
    needs_socratic = (
        getattr(section_out, "needs_socratic", False) if section_out else False
    )
    socratic_reason = getattr(section_out, "socratic_reason", "") if section_out else ""

    # Build a probe object when the section still needs questioning
    probe_out = None
    turn = len(request.clarifications) + 1
    if needs_socratic and socratic_reason and turn <= 7:
        import uuid

        probe_out = SocraticProbeOut(
            probe_id=str(uuid.uuid4()),
            question=socratic_reason,
            style="depth",
            turn=turn,
            max_turns=7,
            originating_agent="sow_regenerate",
            what_was_asked=request.user_feedback or "",
            rag_grounding=kb_text[:500] if kb_text else "",
            kb_facts="",
            transparency_events=[],
        )

    return RegenerateSectionResponse(
        section_name=request.section_name,
        content=content,
        needs_socratic=needs_socratic,
        socratic_reason=socratic_reason,
        socratic_probe=probe_out,
    )

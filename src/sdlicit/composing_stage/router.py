"""FastAPI router for the composing stage.

No mutable module-level state.  The Orchestrator is injected via
``app.state.orchestrator`` using FastAPI's ``Depends()``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from sdlicit.logging import get_logger
from sdlicit.orchestrator import Orchestrator

from .models import (
    AnalyseRequest,
    AnalyseResponse,
    CreateSOWRequest,
    CreateSOWResponse,
    RegenerateSectionRequest,
    RegenerateSectionResponse,
    StepEventRequest,
    StepEventResponse,
    SuggestDirectionsRequest,
    SuggestDirectionsResponse,
)
from .service import (
    analyse_input,
    create_sow,
    create_sow_stream,
    handle_step_event,
    regenerate_section,
    suggest_adr_directions,
)

_log = get_logger("route")

router = APIRouter(prefix="/composing", tags=["composing"])


def _get_orchestrator(request: Request) -> Orchestrator:
    orch = request.app.state.orchestrator
    if orch is None:
        raise HTTPException(
            status_code=503, detail="Not initialised — POST /api/v1/init first"
        )
    return orch


@router.post("/step", response_model=StepEventResponse)
async def post_step_event(
    request: StepEventRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> StepEventResponse:
    _log.info("POST /composing/step  step=%s", request.step_name)
    result = await handle_step_event(request, orchestrator)
    _log.info(
        "POST /composing/step → suggestion=%s compliance=%s",
        result.suggestion is not None,
        result.compliance is not None,
    )
    return result


@router.post("/analyse", response_model=AnalyseResponse)
async def post_analyse(
    request: AnalyseRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> AnalyseResponse:
    _log.info("POST /composing/analyse")
    result = await analyse_input(request, orchestrator)
    _log.info("POST /composing/analyse → %d suggestion(s)", len(result.suggestions))
    return result


@router.post("/adr/suggest-directions", response_model=SuggestDirectionsResponse)
async def post_suggest_directions(
    request: SuggestDirectionsRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> SuggestDirectionsResponse:
    _log.info(
        "POST /composing/adr/suggest-directions  brief_chars=%d",
        len(request.brief),
    )
    result = await suggest_adr_directions(request, orchestrator)
    _log.info(
        "POST /composing/adr/suggest-directions → %d direction(s)",
        len(result.directions),
    )
    return result


# ---------------------------------------------------------------------------
# SOW routes (merged from intake_stage — kept under /intake prefix for compat)
# ---------------------------------------------------------------------------

intake_router = APIRouter(prefix="/intake", tags=["intake"])


def _get_orchestrator_intake(request: Request) -> Orchestrator:
    orch = request.app.state.orchestrator
    if orch is None:
        raise HTTPException(
            status_code=503, detail="Not initialised — POST /api/v1/init first"
        )
    return orch


@intake_router.post("/sow", response_model=CreateSOWResponse)
async def post_create_sow(
    request: CreateSOWRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator_intake),
) -> CreateSOWResponse:
    _log.info("POST /intake/sow")
    return await create_sow(request, orchestrator)


@intake_router.post("/sow/stream")
async def post_create_sow_stream(
    request: CreateSOWRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator_intake),
) -> StreamingResponse:
    _log.info("POST /intake/sow/stream")
    return StreamingResponse(
        create_sow_stream(request, orchestrator),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@intake_router.post("/sow/regenerate-section", response_model=RegenerateSectionResponse)
async def post_regenerate_section(
    request: RegenerateSectionRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator_intake),
) -> RegenerateSectionResponse:
    _log.info("POST /intake/sow/regenerate-section  section=%s", request.section_name)
    return await regenerate_section(request, orchestrator)

"""FastAPI router for the generation stage."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from sdlicit.logging import get_logger
from sdlicit.orchestrator import Orchestrator

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
)
from .service import (
    consult_socratic,
    generate_gherkin,
    generate_personas,
    generate_srs,
    generate_stories,
)
from .tools.gherkin_validator import validate_gherkin

_log = get_logger("route")

router = APIRouter(prefix="/generation", tags=["generation"])


def _get_orchestrator(request: Request) -> Orchestrator:
    orch = request.app.state.orchestrator
    if orch is None:
        raise HTTPException(
            status_code=503, detail="Not initialised — POST /api/v1/init first"
        )
    return orch


@router.post("/personas", response_model=GeneratePersonasResponse)
async def post_personas(
    request: GeneratePersonasRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> GeneratePersonasResponse:
    _log.info("POST /generation/personas")
    return await generate_personas(request, orchestrator)


@router.post("/gherkin", response_model=GenerateGherkinResponse)
async def post_gherkin(
    request: GenerateGherkinRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> GenerateGherkinResponse:
    _log.info("POST /generation/gherkin")
    return await generate_gherkin(request, orchestrator)


@router.post("/stories", response_model=GenerateStoriesResponse)
async def post_stories(
    request: GenerateStoriesRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> GenerateStoriesResponse:
    _log.info("POST /generation/stories")
    return await generate_stories(request, orchestrator)


@router.post("/srs", response_model=GenerateSRSResponse)
async def post_srs(
    request: GenerateSRSRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> GenerateSRSResponse:
    _log.info("POST /generation/srs")
    return await generate_srs(request, orchestrator)


class ValidateGherkinRequest(BaseModel):
    gherkin_text: str


@router.post("/validate-gherkin")
async def post_validate_gherkin(request: ValidateGherkinRequest) -> dict:
    """Validate Gherkin syntax without requiring orchestrator."""
    _log.info("POST /generation/validate-gherkin")
    return validate_gherkin(request.gherkin_text)


# Cross-cutting Socratic consultation — REST mirror of the MCP tool.
# The public path is /api/v1/socratic/consult — the guided flow uses it.
socratic_router = APIRouter(prefix="/socratic", tags=["socratic"])


@socratic_router.post("/consult", response_model=ConsultSocraticResponse)
async def post_consult_socratic(
    request: ConsultSocraticRequest,
    orchestrator: Orchestrator = Depends(_get_orchestrator),
) -> ConsultSocraticResponse:
    _log.info("POST /socratic/consult agent=%s", request.originating_agent)
    return await consult_socratic(request, orchestrator)

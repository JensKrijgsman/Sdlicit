"""Artifacts router — CRUD API for the artifact store.

All frontends (extension, CLI, pipeline) use these endpoints to save
and retrieve structured artifacts.  The backend is the single source
of truth for artifact format, naming, and storage.

Endpoints::

    POST   /artifacts/save          Save structured artifact data (JSON primary)
    GET    /artifacts/{type}        Load artifact(s) by type
    GET    /artifacts/{type}/markdown  Render artifact as markdown
    GET    /artifacts/list          List all artifacts in the workspace
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from sdlicit.helpers.artifact_naming import ArtifactType
from sdlicit.helpers.artifact_store import (
    ADRArtifact,
    ArtifactStore,
    BDDArtifact,
    PersonasArtifact,
    SOWArtifact,
    SRSArtifact,
    StoriesArtifact,
)

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


# ── Request/Response models ────────────────────────────────────────────────


class SaveArtifactRequest(BaseModel):
    """Save an artifact — the backend determines file path and format."""

    artifact_type: ArtifactType
    data: dict[str, Any] = Field(..., description="Structured artifact payload")
    project_dir: str = Field(default="", description="Project root (uses init project_dir if empty)")
    render_markdown: bool = Field(default=True, description="Also write .md alongside .json")


class SaveArtifactResponse(BaseModel):
    json_path: str
    markdown_path: str | None = None
    artifact_meta: dict[str, str] = Field(default_factory=dict)
    suggested_implements: list[str] = Field(default_factory=list, description="Auto-detected requirement links")


class LoadArtifactResponse(BaseModel):
    artifact_type: str
    data: dict[str, Any]
    markdown: str | None = None


class ListArtifactItem(BaseModel):
    type: str
    filename: str
    relative_path: str
    format: str


# ── Helpers ────────────────────────────────────────────────────────────────


def _get_project_dir(project_dir: str = "", request: Request | None = None) -> Path:
    """Resolve project directory from request or app state."""
    if project_dir:
        return Path(project_dir).resolve()
    if request is not None:
        config = request.app.state.config
        if config is not None and hasattr(config, "project_dir"):
            return Path(config.project_dir).resolve()
    raise HTTPException(status_code=400, detail="No project_dir provided and no project initialized")


def _get_store(project_dir: str = "", request: Request | None = None) -> ArtifactStore:
    return ArtifactStore(_get_project_dir(project_dir, request))


def _build_artifact(artifact_type: ArtifactType, data: dict[str, Any]):
    """Construct typed artifact model from raw dict."""
    type_map = {
        "sow": SOWArtifact,
        "srs": SRSArtifact,
        "personas": PersonasArtifact,
        "stories": StoriesArtifact,
        "adr": ADRArtifact,
        "bdd": BDDArtifact,
    }
    cls = type_map.get(artifact_type)
    if cls is None:
        raise HTTPException(status_code=400, detail=f"Unknown artifact type: {artifact_type}")
    try:
        return cls.model_validate(data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid artifact data: {e}") from e


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.post("/save", response_model=SaveArtifactResponse)
def save_artifact(req: SaveArtifactRequest, request: Request) -> SaveArtifactResponse:
    """Save structured artifact data to the workspace.

    For ADRs: auto-detects which requirements the ADR addresses
    (semantic matching via TraceService) and injects ``implements``
    into the artifact before saving. The detected links are also
    returned in the response so the frontend can display them.
    """
    store = _get_store(req.project_dir, request)
    artifact = _build_artifact(req.artifact_type, req.data)

    # Auto-detect implements links for ADRs (before saving)
    suggested_implements: list[str] = []
    if isinstance(artifact, ADRArtifact) and not artifact.implements:
        suggested_implements = _detect_adr_implements(artifact, request)
        if suggested_implements:
            artifact.implements = suggested_implements

    if req.render_markdown:
        json_path, md_path = store.save_both(artifact)
        meta = store._resolve_meta(artifact)
        return SaveArtifactResponse(
            json_path=str(json_path),
            markdown_path=str(md_path),
            artifact_meta=meta.model_dump(),
            suggested_implements=suggested_implements,
        )
    else:
        json_path = store.save(artifact)
        meta = store._resolve_meta(artifact)
        return SaveArtifactResponse(
            json_path=str(json_path),
            artifact_meta=meta.model_dump(),
            suggested_implements=suggested_implements,
        )


def _detect_adr_implements(artifact: ADRArtifact, request: Request) -> list[str]:
    """Use TraceService to find which requirements an ADR addresses."""
    try:
        orchestrator = request.app.state.orchestrator
        if orchestrator is None:
            return []
        # Build the ADR text for semantic comparison
        content_parts = [artifact.title, artifact.context, artifact.decision,
                         artifact.rationale, artifact.consequences]
        content = "\n".join(p for p in content_parts if p)
        if not content:
            return []
        project_dir = orchestrator.config.project_dir
        return orchestrator.trace_service.detect_implements(content, project_dir)
    except Exception:
        return []


@router.get("/list", response_model=list[ListArtifactItem])
def list_artifacts(request: Request, project_dir: str = "") -> list[ListArtifactItem]:
    """List all artifacts in the workspace."""
    store = _get_store(project_dir, request)
    items = store.list_artifacts()
    return [ListArtifactItem(**item) for item in items]


@router.get("/{artifact_type}", response_model=LoadArtifactResponse)
def load_artifact(artifact_type: ArtifactType, request: Request, project_dir: str = "", filename: str | None = None) -> LoadArtifactResponse:
    """Load an artifact by type. Returns structured JSON data."""
    store = _get_store(project_dir, request)
    artifact = store.load(artifact_type, filename)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"No {artifact_type} artifact found")
    return LoadArtifactResponse(
        artifact_type=artifact_type,
        data=artifact.model_dump(mode="json"),
    )


@router.get("/{artifact_type}/all")
def load_all_artifacts(artifact_type: ArtifactType, request: Request, project_dir: str = "") -> list[dict[str, Any]]:
    """Load all artifacts of a given type (e.g., all ADRs)."""
    store = _get_store(project_dir, request)
    artifacts = store.load_all(artifact_type)
    return [a.model_dump(mode="json") for a in artifacts]


@router.get("/{artifact_type}/markdown")
def render_artifact_markdown(artifact_type: ArtifactType, request: Request, project_dir: str = "", filename: str | None = None) -> dict[str, str]:
    """Render an artifact as markdown."""
    store = _get_store(project_dir, request)
    artifact = store.load(artifact_type, filename)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"No {artifact_type} artifact found")
    md = store.render_markdown(artifact)
    return {"artifact_type": artifact_type, "markdown": md}

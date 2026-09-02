"""Sdlicit FastAPI application — unified REST API + single MCP server.

The server starts *uninitialised*.  The CLI (or any client) must call
``POST /api/v1/init`` with a ``project_dir`` before using any stage
endpoint.  That call loads ``.sdlicit/config.yaml``, configures DSPy,
and creates the Orchestrator.

Routers pull the Orchestrator via ``Depends()`` and return 503 if it
has not been set up yet.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel
from starlette.routing import Mount

from sdlicit.bootstrap import create_system
from sdlicit.logging import get_logger
from sdlicit.logging.usage import UsageCounter, bind, unbind

if TYPE_CHECKING:
    from sdlicit.orchestrator import Orchestrator

load_dotenv()

_log = get_logger("main")


@asynccontextmanager
async def _lifespan(application: FastAPI):
    """Graceful shutdown: persist ToM session if still open."""
    yield
    orch = application.state.orchestrator
    if orch is not None and orch.tom is not None and orch.tom._raw_session is not None:
        _log.info("Shutdown: ending open ToM session")
        await orch.tom.end_session()


# ---------------------------------------------------------------------------
# FastAPI app — starts uninitialised; POST /api/v1/init bootstraps it.
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Sdlicit",
    version="0.4.0",
    description="Knowledge-grounded SDLC artifact framework — DSPy agents, MCP tools",
    lifespan=_lifespan,
)

app.state.orchestrator = None
app.state.config = None


# ── Per-request token-usage middleware ────────────────────────────────────
@app.middleware("http")
async def _usage_middleware(request, call_next):
    """Bind a fresh ``UsageCounter`` for the request and surface totals as headers.

    Downstream code (LLM gateway, LightRAG wrapper) records usage on the
    bound counter via ``sdlicit.logging.usage``.  Header schema::

        X-Sdlicit-Tokens-Prompt:     <int>
        X-Sdlicit-Tokens-Completion: <int>
        X-Sdlicit-Tokens-Total:      <int>
        X-Sdlicit-Tokens-Calls:      <int>
        X-Sdlicit-Tokens-By-Agent:   <json>
    """
    cfg = app.state.config
    counter = UsageCounter(model=getattr(cfg, "model", None) if cfg else None)
    token = bind(counter)
    try:
        response = await call_next(request)
    finally:
        unbind(token)

    response.headers["X-Sdlicit-Tokens-Prompt"] = str(counter.prompt_tokens)
    response.headers["X-Sdlicit-Tokens-Completion"] = str(counter.completion_tokens)
    response.headers["X-Sdlicit-Tokens-Total"] = str(counter.total_tokens)
    response.headers["X-Sdlicit-Tokens-Calls"] = str(counter.calls)
    if counter.by_agent:
        import json as _json

        response.headers["X-Sdlicit-Tokens-By-Agent"] = _json.dumps(
            {k: v.as_dict() for k, v in counter.by_agent.items()}
        )
    return response


class InitRequest(BaseModel):
    project_dir: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


async def _run_staleness_check(
    project_path: Path,
    orchestrator: Orchestrator,
) -> None:
    """Background task: purge LightRAG chunks for deleted files/artifacts."""
    from sdlicit.expansion_stage.document_scanner import cleanup_stale_documents

    try:
        if orchestrator.kb is not None:
            doc_result = await cleanup_stale_documents(project_path, orchestrator.kb)
            if doc_result["removed_files"]:
                _log.info("Staleness check (docs): %s", doc_result)

        if orchestrator.kb_router is not None:
            art_result = await orchestrator.kb_router.cleanup_stale_artifacts(
                project_path
            )
            if art_result["removed"]:
                _log.info("Staleness check (artifacts): %s", art_result)
    except Exception as exc:
        _log.warning("Background staleness check failed: %s", exc)


@app.post("/api/v1/init")
def init_project(
    req: InitRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Receive the project root from the CLI and bootstrap the system."""
    project_path = Path(req.project_dir).resolve()
    config_file = project_path / ".sdlicit" / "config.yaml"
    if not config_file.exists():
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail=f".sdlicit/config.yaml not found in {project_path}",
        )

    config, orchestrator = create_system(project_path)
    app.state.orchestrator = orchestrator
    app.state.config = config

    # Fire-and-forget async staleness check (runs after the response is sent)
    if orchestrator.kb is not None:
        background_tasks.add_task(_run_staleness_check, project_path, orchestrator)

    return {"status": "ok", "project_dir": str(project_path)}


class SessionRequest(BaseModel):
    stage: str = "unknown"


@app.post("/api/v1/session/start")
def start_session(req: SessionRequest) -> dict[str, str | None]:
    """Start a ToM session — call at the beginning of a CLI run."""
    orch = app.state.orchestrator
    if orch is None:
        return {"session_id": None}
    session_id = orch.start_session(req.stage)
    return {"session_id": session_id}


@app.post("/api/v1/session/end")
async def end_session() -> dict:
    """End the current ToM session.

    Returns the Tier 1 raw session, Tier 2 session analysis and Tier 3
    updated user model as JSON.  The CLI persists them — the backend
    never writes session files.
    """
    orch = app.state.orchestrator
    if orch is None:
        return {"status": "not_initialised"}
    return {"status": "ok", **(await orch.end_session())}


@app.post("/api/v1/session/compact")
async def compact_session() -> dict:
    """Mid-session compaction.

    Triggered by the CLI when the running token total approaches the
    configured threshold (``compact_threshold_pct`` of
    ``model_context_window``).  Returns a JSON summary that the CLI
    writes to ``sdlicit/<sid>/compact.json``.
    """
    orch = app.state.orchestrator
    if orch is None:
        return {"status": "not_initialised"}
    result = await orch.compact_session()
    if result is None:
        return {"status": "empty"}
    return {"status": "ok", **result}


class PreferenceRequest(BaseModel):
    key: str
    value: str
    note: str = ""


@app.post("/api/v1/preference")
def save_preference(req: PreferenceRequest) -> dict:
    """Capture an explicit user preference and persist to disk."""
    orch = app.state.orchestrator
    if orch is None or orch.tom is None or orch.tom.memory is None:
        return {"status": "not_initialised"}

    current = orch.tom.memory.load_user_model()
    clusters = {k: list(v) for k, v in (current.preference_clusters or {}).items()}
    bucket = clusters.setdefault(req.key, [])
    if req.value not in bucket:
        bucket.append(req.value)
    updated = current.model_copy(update={"preference_clusters": clusters})
    orch.tom.memory.save_user_model(updated.model_dump())
    return {
        "status": "ok",
        "key": req.key,
        "value": req.value,
        "note": req.note,
        "user_model": updated.model_dump(),
    }


@app.get("/api/v1/config")
def get_config() -> dict[str, object]:
    """Return non-secret config fields for CLI header display."""
    cfg = app.state.config
    if cfg is None:
        return {"status": "not_initialised"}
    return {
        "provider": cfg.provider,
        "model": cfg.model,
        "model_overrides": cfg.model_overrides,
        "enable_rag": cfg.enable_rag,
        "enable_tom": cfg.enable_tom,
        "enable_socratic": cfg.enable_socratic,
        "socratic_judge_mode": cfg.socratic_judge_mode,
        "socratic_max_turns": cfg.socratic_max_turns,
        "model_context_window": cfg.model_context_window,
        "compact_threshold_pct": cfg.compact_threshold_pct,
        "tom_focus": cfg.tom_focus,
        "log_prompts": cfg.log_prompts,
    }


# -- Chat observation / ToM suggestion endpoints ------------------------------


class ChatObserveRequest(BaseModel):
    user_message: str
    assistant_response: str
    chat_history: list[dict[str, str]] = []


@app.post("/api/v1/tom/observe-chat")
async def observe_chat(req: ChatObserveRequest) -> dict:
    """Observe a chat exchange for ToM state updates.

    Called by the extension after each chat message exchange.
    Returns the ToM observation (inferred state, recommendation).
    """
    orch = app.state.orchestrator
    if orch is None or orch.tom is None:
        return {"status": "disabled"}

    rec = await orch.tom.observe_chat(
        req.user_message,
        req.assistant_response,
        req.chat_history,
    )
    return {
        "status": "ok",
        "observation": rec.observation,
        "recommendation": rec.recommendation,
        "scaffolding_level": rec.scaffolding_level,
        "confidence": rec.confidence,
        "inferred_goals": rec.inferred_goals,
    }


class GiveSuggestionsRequest(BaseModel):
    chat_history: list[dict[str, str]] = []


@app.post("/api/v1/tom/give-suggestions")
async def give_suggestions(req: GiveSuggestionsRequest) -> dict:
    """Proactively check if a Socratic probe should be injected.

    Called by the extension periodically (e.g. every 3 exchanges).
    Returns whether to inject a probe and the probe text.
    """
    orch = app.state.orchestrator
    if orch is None or orch.tom is None:
        return {"status": "disabled", "should_probe": False}

    result = await orch.tom.give_suggestions(req.chat_history)
    return {"status": "ok", **result}


# ---------------------------------------------------------------------------
# REST routes — routers use Depends() to get orchestrator from app.state
# ---------------------------------------------------------------------------

from sdlicit.composing_stage.router import intake_router  # noqa: E402
from sdlicit.composing_stage.router import router as composing_router  # noqa: E402
from sdlicit.expansion_stage.router import router as expansion_router  # noqa: E402
from sdlicit.generation_stage.router import router as generation_router  # noqa: E402
from sdlicit.generation_stage.router import socratic_router  # noqa: E402
from sdlicit.helpers.artifact_router import router as artifacts_router  # noqa: E402

app.include_router(intake_router, prefix="/api/v1")
app.include_router(composing_router, prefix="/api/v1")
app.include_router(expansion_router, prefix="/api/v1")
app.include_router(generation_router, prefix="/api/v1")
app.include_router(socratic_router, prefix="/api/v1")
app.include_router(artifacts_router, prefix="/api/v1")

# ---------------------------------------------------------------------------
# MCP server — single instance with all tools
# ---------------------------------------------------------------------------

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("Sdlicit", stateless_http=False)


@mcp.tool()
async def query_knowledge_base(query: str, mode: str = "hybrid") -> list[dict]:
    """Search the architectural knowledge base (legacy — prefer query_rag)."""
    return await app.state.orchestrator.query_kb(query, mode=mode)


@mcp.tool()
async def query_rag(
    query: str,
    store: str = "all",
    mode: str = "hybrid",
    probe_first: bool = False,
    top_k: int = 5,
) -> dict:
    """Query the RAG knowledge base with store-aware routing.

    This is the primary tool for retrieving context from ingested documents.
    Two logical stores exist within one unified graph:

    - ``knowledge``: Reference documents (ISO standards, design patterns, PDFs)
    - ``artifacts``: Project artifacts produced by Sdlicit (SOW, ADR, etc.)
    - ``all``: Search across both stores (uses full graph connectivity)

    Use ``probe_first=True`` to cheaply check if the graph contains relevant
    entities before executing the full retrieval (avoids wasted LLM calls).

    Retrieval modes:
    - ``naive``: Vector similarity on raw chunks (supports source filtering)
    - ``local``: Graph neighborhood traversal from matched entities
    - ``global``: High-level community summaries
    - ``hybrid``: Combines naive + local (recommended for filtered queries)
    - ``mix``: Combines local + global
    """
    orchestrator = app.state.orchestrator
    if orchestrator.kb_router is None:
        return {"results": [], "rag_enabled": False, "probed": False}

    chunks = await orchestrator.kb_router.query(
        text=query,
        store=store,  # type: ignore[arg-type]
        mode=mode,
        top_k=top_k,
        probe_first=probe_first,
    )
    return {
        "results": [
            {
                "text": c.text,
                "source": c.source,
                "relevance": c.relevance,
                "mode": c.mode,
                "store": c.store,
            }
            for c in chunks
        ],
        "rag_enabled": True,
        "probed": probe_first,
        "store": store,
    }


@mcp.tool()
async def validate_gherkin_syntax(gherkin_text: str) -> dict:
    """Validate Gherkin syntax using the official Cucumber parser."""
    from sdlicit.generation_stage.tools.gherkin_validator import validate_gherkin

    return validate_gherkin(gherkin_text)


@mcp.tool()
async def summarize_adr(adr_content: str) -> str:
    """Summarize an ADR."""
    from sdlicit.helpers.parsers import summarise_adr

    return summarise_adr(adr_content)


@mcp.tool()
async def review_adr_step(
    step_name: str,
    step_value: str,
    partial_fields: str = "{}",
) -> dict:
    """Send a single ADR creation step to the agent and get suggestions."""
    import json

    result = await app.state.orchestrator.review_step(
        step_name,
        step_value,
        json.loads(partial_fields),
    )
    return {
        "suggestions": [
            {"field": s.field, "message": s.message, "severity": s.severity}
            for s in result.suggestions
            if s.should_show
        ],
    }


@mcp.tool()
async def suggest_adr_directions(
    brief: str,
    prior_adr_contents: str = "[]",
    downstream_artifacts: str = "",
    uncovered_requirements: str = "",
) -> dict:
    """Suggest WHICH ADR topics the user should write next.

    Pure ideation — never proposes solutions.  Use this when the user
    has a Brief / SOW but is unsure which architectural decisions to
    document, or after some ADRs exist and you want to surface gaps.
    Pass prior ADR contents as a JSON-encoded list of strings.  When
    available, downstream artifacts (personas / stories / Gherkin)
    can be concatenated as plain text in ``downstream_artifacts``.
    Pass ``uncovered_requirements`` as a comma-separated list of REQ-IDs
    not yet addressed by existing ADRs to guide topic prioritization.
    The Theory-of-Mind user model is consulted automatically when
    enabled to re-rank priorities by inferred user goals.
    """
    import json

    try:
        prior = json.loads(prior_adr_contents) if prior_adr_contents else []
    except json.JSONDecodeError:
        prior = []

    result = await app.state.orchestrator.suggest_adr_directions(
        brief=brief,
        prior_adrs=prior,
        downstream_artifacts=downstream_artifacts,
        uncovered_requirements=uncovered_requirements,
    )
    return {
        "summary": result.raw_text,
        "directions": [
            {
                "title": d.title,
                "rationale": d.rationale,
                "priority": d.priority,
                "gap_filled": d.gap_filled,
            }
            for d in result.adr_directions
        ],
    }


@mcp.tool()
async def consult_tom(agent_query: str, current_context: str = "") -> dict:
    """In-session Theory of Mind consultation.

    Call this when uncertain about user intent, when the user seems
    frustrated, or when you need to adapt scaffolding level.  Returns
    an observation of the user's mental state and a recommendation.
    """
    return await app.state.orchestrator.consult_tom(agent_query, current_context)


@mcp.tool()
async def update_tom_memory(session_id: str = "") -> dict:
    """After-session Theory of Mind memory update.

    Processes the completed session through the three-tier memory:
    Tier 1 (raw log) → Tier 2 (session analysis) → Tier 3 (user profile).
    Call this when a session or task is finished.
    """
    return await app.state.orchestrator.update_tom_memory(
        session_id if session_id else None
    )


@mcp.tool()
async def consult_socratic(
    originating_agent: str,
    what_was_asked: str,
    what_is_known: str,
    suspect_output: str = "",
    issue: str = "ambiguous_input",
) -> dict:
    """Cross-cutting Socratic consultation.

    Call this when an agent's input is under-specified, or when an
    LLM output appears bloated / hallucinated / contains claims not
    grounded in the user's input.  Returns a thought-provoking
    question that the user can answer — the Socratic agent never
    proposes solutions, it only probes.

    ``issue`` should be ``"ambiguous_input"`` (default) when the user
    input is the problem, or ``"ungrounded_output"`` when the LLM
    output is the problem (pass it as ``suspect_output``).
    """
    return await app.state.orchestrator.consult_socratic_freeform(
        originating_agent=originating_agent,
        what_was_asked=what_was_asked,
        what_is_known=what_is_known,
        suspect_output=suspect_output,
        issue=issue,
    )


app.routes.append(Mount("/mcp", app=mcp.streamable_http_app()))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _run_server() -> None:
    """Entry-point for ``uv run sdlicit-server``."""
    uvicorn.run("sdlicit.main:app", host="127.0.0.1", port=8000, reload=True)

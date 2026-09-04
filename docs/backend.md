# Backend

FastAPI app at `src/sdlicit/main.py`. The server holds exactly one project's state at a time: it starts uninitialised, and `POST /api/v1/init` loads `<project_dir>/.sdlicit/config.yaml`, configures DSPy for the chosen provider, and builds one `Orchestrator` instance stored on `app.state`. There is no per request project switching or multi tenancy, a running server serves one project until it is re initialised or restarted. Run it with `uv run sdlicit-server` (binds `127.0.0.1:8000` with autoreload) or `uvicorn sdlicit.main:app`.

## Request flow

A client (CLI or extension) always follows the same sequence: `POST /api/v1/init` with a `project_dir`, then any stage endpoint. Every stage router pulls the orchestrator through a FastAPI `Depends()` and returns `503` if `init` has not run yet, so there is no risk of a stage endpoint operating on a half built system.

Two things happen automatically once a project is initialised:

- If a knowledge base was built (`enable_rag=True`), a background task runs a staleness check right after `init` returns: it diffs the project's files and artifacts against what is already ingested and purges LightRAG chunks for anything deleted since the last run.
- A per request middleware binds a fresh `UsageCounter` for the duration of the request and reports totals as response headers (`X-Sdlicit-Tokens-Prompt`, `-Completion`, `-Total`, `-Calls`, and `-By-Agent` as a JSON breakdown), so a client can show live token spend without the backend persisting anything itself.

## REST surface

| Router | Prefix | Covers |
|---|---|---|
| Core (`main.py`) | `/api/v1` | `init`, `session/start`, `session/end`, `session/compact`, `preference`, `config`, `tom/observe-chat`, `tom/give-suggestions` |
| `composing_stage/router.py` | `/api/v1/intake`, `/api/v1` | SOW creation and streaming, SOW section regeneration, ADR step review, ADR full review, suggest ADR directions |
| `expansion_stage/router.py` | `/api/v1` | ADR expand, KB query/ingest/status, artifact ingest/supersede/delete, traceability graph/check/coverage, chunk location |
| `generation_stage/router.py` | `/api/v1`, `/api/v1/socratic` | Personas, Gherkin, stories, SRS generation, Gherkin validation, Socratic consult |
| `helpers/artifact_router.py` | `/api/v1/artifacts` | Save/load/list artifacts, render canonical markdown |

`GET /api/v1/config` returns the non secret configuration a client needs to render correctly: provider and model, the three feature flags (`enable_rag`, `enable_tom`, `enable_socratic`), the full ablation surface (`socratic_judge_mode`, `tom_focus`, `trace_check_mode`, `traceability_graph_source`, `agentic`, `agentic_max_iters`, `context_override_active`), and the shared token budget fields. See [Architecture](architecture.md#ablation-surface) for what each of those actually changes at runtime.

## Session lifecycle and Theory of Mind

`session/start` begins a ToM session for the CLI stage the client is about to run. `session/end` closes it and returns three tiers of state as JSON: the raw session log, a Tier 2 session level analysis, and the Tier 3 updated user model, all of which the CLI writes to disk itself under the project's `.sdlicit/sessions/` (the backend never persists session history, only produces it on request). `session/compact` is a mid session equivalent, triggered by a client when the running token total approaches `compact_threshold_pct` of `model_context_window`, the same budget concept `helpers/context_budget.py` uses for bounding prior artifact context (see [Architecture](architecture.md#prior-artifact-context)). `tom/observe-chat` and `tom/give-suggestions` back the VS Code extension's free form chat panel: the first records an exchange for ToM state tracking, the second is polled periodically to decide whether a Socratic probe should be injected proactively.

## LLM gateway

`agents/llm/gateway.py` is the single point every agent calls through, never DSPy directly. Two responsibilities live there. First, model aware module selection: thinking models (o1/o3 class, DeepSeek R1) get plain `dspy.Predict` since their native hidden reasoning makes an explicit chain of thought harmful rather than helpful, standard models get `dspy.Predict` for inherently simple signatures (classification, extraction) and `dspy.ChainOfThought` for signatures that benefit from an explicit scratchpad. Second, an optional validated program registry: a signature name can have a `dspy.Module` subclass (typically built on `dspy.Refine` for hard or soft validation retries) registered against it, and `predict()` dispatches to that program transparently, so calling agents never need to change when a signature moves from a bare predictor to a validated, retrying one. `predict_react` is the same idea for tool calling: it wraps `dspy.ReAct` with a fixed toolset and iteration cap, used when `config.agentic=True` (see the ablation surface entry in [Architecture](architecture.md#ablation-surface) for exactly which agent currently uses it).

## Knowledge base and traceability

`TraceService` (`expansion_stage/traceability/`) supports three modes: structural (ID cross reference validation only), semantic (adds TF-IDF/Jaccard/NMF content similarity, needs scikit-learn), and full (adds graph based conflict detection). `TraceGraph` builds a directed graph from artifact frontmatter (`implements`, `supersedes`, `tested_by`), optionally enriched with LightRAG's own entity relation graph when `traceability_graph_source=lightrag`. See [Architecture](architecture.md#knowledge-base) for how `KnowledgeBase`, `KBRouter`, and the structure aware chunkers fit together.

## MCP tool surface

The same FastAPI process also mounts an MCP server at `/mcp` (`mcp.server.fastmcp.FastMCP`, streamable HTTP transport), so an external MCP capable client (an IDE agent, a coding assistant) can drive Sdlicit as a set of tools instead of going through the CLI or extension. The tools are thin wrappers over the same orchestrator methods the REST routers call: `query_rag` (the primary retrieval tool, store and mode aware, with an optional cheap probe before a full query), `query_knowledge_base` (an older, simpler retrieval tool kept for compatibility), `validate_gherkin_syntax` (the official Cucumber parser, not a heuristic), `summarize_adr`, `review_adr_step`, `suggest_adr_directions`, `consult_tom`, `update_tom_memory`, and `consult_socratic`. An external agent using these tools gets the same knowledge grounding, Theory of Mind adaptation, and Socratic intervention that the CLI and extension get, without reimplementing any of it.

## Testing

```bash
uv run pytest
```

Backend tests live under `test/` and mock the LLM gateway and knowledge base boundaries rather than making live model calls, so the suite runs without any API key or network access. See the repository's `test/` directory for the current layout.

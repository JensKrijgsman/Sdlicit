# Backend

FastAPI app at `src/sdlicit/main.py`. Starts uninitialised until `POST /api/v1/init`.

## REST surface

| Router | Prefix | Covers |
|---|---|---|
| Core (`main.py`) | `/api/v1` | `init`, `session/start`, `session/end`, `session/compact`, `preference`, `config`, `tom/*` |
| `composing_stage/router.py` | `/api/v1/intake`, `/api/v1` | SOW creation and streaming, SOW section regeneration, ADR step review, ADR full review, suggest ADR directions |
| `expansion_stage/router.py` | `/api/v1` | ADR expand, KB query/ingest/status, artifact ingest/supersede/delete, traceability graph/check/coverage, chunk location |
| `generation_stage/router.py` | `/api/v1`, `/api/v1/socratic` | Personas, Gherkin, stories, SRS generation, Gherkin validation, Socratic consult |
| `helpers/artifact_router.py` | `/api/v1/artifacts` | Save/load/list artifacts, render canonical markdown |

An MCP tool surface is also exposed from `main.py` (`@mcp.tool()`), covering the same ADR review/suggestion/summarisation operations for external MCP clients.

## Configuration

`SdlicitConfig` (`src/sdlicit/config.py`) loads `<project_dir>/.sdlicit/config.yaml`, with `OPENROUTER_API_KEY` / `OLLAMA_HOST` environment variable overrides for secrets. Key fields:

| Field | Default | Meaning |
|---|---|---|
| `provider` | `openrouter` | `openrouter` or `ollama` |
| `model` | `openai/gpt-5.4-nano` | Model identifier |
| `enable_rag` / `enable_tom` / `enable_socratic` | `true` | Feature flags, useful for ablation |
| `kb_agent_stores` | per-agent | Which KB namespace (`knowledge`, `artifacts`, `all`) each agent queries by default |
| `trace_check_mode` | `structural` | `structural`, `semantic`, or `full` |
| `model_context_window` / `compact_threshold_pct` | `8192` / `0.4` | Shared token budget for both ToM session compaction and prior-artifact context bounding |

## Traceability

`TraceService` (`expansion_stage/traceability/`) supports three modes: structural (ID cross-reference validation only), semantic (adds TF-IDF/Jaccard/NMF content similarity, needs scikit-learn), and full (adds graph-based conflict detection). `TraceGraph` builds a directed graph from artifact frontmatter (`implements`, `supersedes`, `tested_by`), optionally enriched with LightRAG's own entity-relation graph.

## Testing

```bash
uv run pytest
```

`test/test_context_budget.py` covers the prior-artifact ranking and budgeting logic.

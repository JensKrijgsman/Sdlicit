# `src/` — Sdlicit backend

FastAPI application and orchestration layer for the Sdlicit framework
(thesis SQ1: technical implementation).

## Layout

```
src/sdlicit/
├── main.py              REST API + MCP server entry point
├── orchestrator.py      Stage routing and agent construction order
├── bootstrap.py         Project init from .sdlicit/config.yaml
├── composing_stage/     ADR creation (MADR, step-by-step)
├── expansion_stage/     KB ingestion, ToM, Socratic agent, traceability
├── generation_stage/    SRS, personas, user stories, Gherkin
├── agents/              LLM gateway, DSPy signatures and programs
└── helpers/             Artifact store, KB router, naming, config
```

## Usage

The server starts uninitialised. Point it at a workspace, then call stage
endpoints:

```bash
uv run sdlicit-server
# POST /api/v1/init  { "project_dir": "examples/demo/" }
```

Clients: `cli/cli_client.py` and `extension/` (both speak REST).

## Design notes (thesis)

- **Traceability** — post-hoc grading reads artefacts from disk; agents never
  score their own output.
- **Structured outputs** — DSPy + Pydantic via `BAMLAdapter`; `dspy.Refine`
  for hard/soft validation retries.
- **Knowledge grounding** — per-agent `KBRouter` over LightRAG (hybrid mode).
- **MCP** — traceability and stage tools exposed for external agents.

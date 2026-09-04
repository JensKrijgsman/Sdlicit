# CLI

`cli/cli_client.py` is a `rich` based terminal client. It is a pure presentation layer: every action goes through `cli/api_client.py` (`SdlicitClient`) over REST, no direct import of the `sdlicit` package. The CLI is the only writer of session data under `.sdlicit/sessions/` — the backend reads but never writes there.

## Running it

```bash
cd cli
python cli_client.py
```

Prompts for a working directory (defaults to `examples/demo`) and a server URL (defaults to `http://127.0.0.1:8000`). If no server is reachable and `uv` is on `PATH`, it starts one automatically and waits up to 30 seconds, logging to `.sdlicit-server.log` in the repo root.

## Menu structure

Stages are wired in dynamically from `cli/stages/*/__init__.py`:

- **Intake** — create a SOW from a brief, regenerate a SOW section.
- **Composing** — create a new ADR (a step by step wizard with live agent suggestions and Socratic follow up questions), list/view ADRs, suggest ADR directions.
- **Expansion** — multi agent artifact review, KB query/ingest/management, traceability dashboard, per artifact link check.
- **Generation** — SRS, personas, user stories, Gherkin scenarios, Gherkin validation.
- **Guided** — sequences the above end to end.

Global hotkeys: `[t]` token stats, `[c]` force ToM compaction, `[p]` save a preference, `[q]` quit.

## ADR creation

`stages/composing/create_adr.py` runs a split pane wizard: fields on the left, live agent suggestions on the right, updated as background threads return. If a new ADR's title looks similar to an existing one (a Jaccard title overlap check run by the server), the CLI surfaces a suggestion and offers to mark the old ADR as superseded on save (`client.supersede_adr`), which removes the old ADR's knowledge base chunks and ingests the new one in their place, the file on disk is left for you to annotate.

## Token usage and compaction

The header shows a running token meter against `model_context_window * compact_threshold_pct` (from `GET /api/v1/config`). Compaction (`[c]`, or automatic once the threshold is crossed) summarises the current Theory of Mind session log via `POST /api/v1/session/compact`.

## Testing

```bash
uv run pytest
```

CLI tests mock `SdlicitClient`'s HTTP calls rather than talking to a live server, and use `tmp_path` for anything that touches the filesystem (the journal, session files, ADR templates), so the suite runs without a running backend. See the repository's `test/` directory for the current layout.

## Known limitation

There is no free form chat surface in the CLI, it is a structured wizard rather than a chat UI, so the extension's automatic Socratic suggestion loop over an open conversation has no CLI equivalent yet.

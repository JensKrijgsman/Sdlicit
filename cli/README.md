# `cli/` — Terminal client

A rich menu CLI that drives the Sdlicit backend over HTTP. It is a pure
presentation layer: no direct imports from `src/sdlicit/`.

## Layout

```
cli/
├── cli_client.py     Main menu and session management
├── api_client.py     SdlicitClient (httpx REST wrapper)
├── shared/           File I/O, journal, Socratic loop helpers
└── stages/           One module per SDLC stage
    ├── intake/       Statement of Work
    ├── composing/    ADRs
    ├── expansion/    KB ingest, query, traceability
    ├── generation/   SRS, personas, stories, Gherkin
    └── guided/       End to end guided flow
```

## Usage

```bash
# Server must be running (uv run sdlicit-server)
cd cli
python cli_client.py
```

Prompts for a working directory (defaults to `examples/demo`) and a server
URL. If no server is reachable and `uv` is on `PATH`, it starts one for you.

The CLI is the **only writer** of `.sdlicit/sessions/`; the backend reads
session history but does not persist it.

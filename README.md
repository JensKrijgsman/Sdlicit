# Sdlicit

Sdlicit is a knowledge grounded LLM framework for structured SDLC artifact creation: Statements of Work, Architecture Decision Records, SRS documents, personas, user stories, and BDD/Gherkin scenarios. It is built around RAG grounded generation, Socratic elicitation, and end to end requirement traceability.

Sdlicit is thesis research by Jens Krijgsman. The research questions, system design, and evaluation are the author's own work. An LLM coding agent (Claude Code) was used as an implementation tool, under the author's direction and review, for portions of the backend, CLI, and extension code, and for this documentation — it did not originate the research ideas.

It's split into three parts that all talk to one backend:

| Component | Path | What it is |
|---|---|---|
| **Backend** | [`src/sdlicit/`](src/sdlicit/) | FastAPI server + orchestrator: stages (intake → expansion → generation → composing), a knowledge base (LightRAG backed RAG), an MCP tool surface, and traceability analysis. |
| **CLI (TUI)** | [`cli/`](cli/) | A terminal client built on `rich`. Pure presentation layer, talks to the backend exclusively over REST, no direct imports of the `sdlicit` package. |
| **VS Code extension (GUI)** | [`extension/`](extension/) | The graphical frontend: artifact panels, a BDD and trace coverage explorer, a guided flow wizard, and a knowledge base browser, all backed by the same REST API. |

Full documentation, including architecture diagrams, is in [`docs/`](docs/) — build it locally with `uv sync --group docs && uv run mkdocs serve`.

## How it fits together

```
CLI (rich) ──┐
             ├──httpx──▶ FastAPI server (src/sdlicit) ──▶ Orchestrator ──▶ Agents ──▶ LLM Gateway
VS Code ext ─┘                                                       └──▶ MCP tool servers
```

The backend starts *uninitialised*. A client (CLI or extension) calls `POST /api/v1/init` with a `project_dir`, which loads `<project_dir>/.sdlicit/config.yaml` and spins up the Orchestrator for that project. Everything the system produces or ingests lives under that project's `.sdlicit/` folder (config, knowledge base, generated artifacts, session logs).

## Getting started

### 1. Environment

With [Nix](https://nixos.org/) + [direnv](https://direnv.net/) (recommended):

```bash
direnv allow      # or: nix-shell
```

This provisions Python 3.12, `uv`, and Node 22, and (once a `.venv` exists) automatically patches a couple of wheel linking quirks specific to NixOS. Without Nix, just make sure you have Python 3.12+ and [`uv`](https://docs.astral.sh/uv/) installed.

```bash
uv sync                 # installs the sdlicit package + backend deps into .venv/
```

Copy `.env.example` to `.env` and fill in your key (OpenRouter by default; Ollama works too — see `provider`/`ollama_host` in `.sdlicit/config.yaml`):

```bash
cp .env.example .env
```

### 2. Run the backend

```bash
uv run sdlicit-server
# or, for autoreload during development:
uv run uvicorn sdlicit.main:app --reload
```

The server listens on `http://127.0.0.1:8000`. It stays idle until a client sends `POST /api/v1/init`.

Optional supporting services (Postgres, Ollama, a standalone LightRAG server) are defined in [`services/compose.yaml`](services/compose.yaml):

```bash
docker compose -f services/compose.yaml up -d
```

### 3. Use it — pick a client

**TUI:**

```bash
cd cli
python cli_client.py
```

**VS Code extension:**

```bash
cd extension
npm install
npm run compile
```

Then open the `extension/` folder in VS Code and press `F5` to launch an Extension Development Host, or package it with `vsce package` and install the `.vsix`. The extension starts the backend server for you automatically (`sdlicit.autoStartServer` setting) if one isn't already running.

## Try it without setting up a project

[`examples/`](examples/) has two ways in:

- [`examples/demo/`](examples/demo/) — a finished example project (`.sdlicit/` workspace) you can point the CLI or extension at directly, with a generated SOW/SRS/ADRs/personas/stories and an ingested knowledge base already in place.
- [`examples/jabref_replay/`](examples/jabref_replay/) — a notebook that runs the real pipeline against a real client brief and shows the generated ADRs next to actual historical decisions from the [JabRef](https://github.com/JabRef/jabref) open source project. Run `uv sync --group examples` first.

## License

AGPL-3.0-or-later — see [`LICENSE`](LICENSE).

# Getting started

## 1. Environment

With [Nix](https://nixos.org/) + [direnv](https://direnv.net/) (recommended):

```bash
direnv allow      # or: nix-shell
```

This provisions Python 3.12, `uv`, and Node 22, and (once a `.venv` exists) patches a couple of NixOS specific wheel linking quirks. Without Nix, just make sure you have Python 3.12+ and [`uv`](https://docs.astral.sh/uv/) installed.

```bash
uv sync
```

Copy `.env.example` to `.env` and fill in your key (OpenRouter by default; Ollama also works, see `provider` and `ollama_host` in `.sdlicit/config.yaml`):

```bash
cp .env.example .env
```

## 2. Run the backend

```bash
uv run sdlicit-server
# or, for autoreload during development:
uv run uvicorn sdlicit.main:app --reload
```

The server listens on `http://127.0.0.1:8000` and stays idle until a client sends `POST /api/v1/init`.

Optional supporting services (Postgres, Ollama, a standalone LightRAG server) are defined in [`services/compose.yaml`](https://github.com/JensKrijgsman/Sdlicit-public/blob/master/services/compose.yaml):

```bash
docker compose -f services/compose.yaml up -d
```

## 3. Pick a client

### CLI (TUI)

```bash
cd cli
python cli_client.py
```

If no server is reachable at the configured URL and `uv` is on your `PATH`, the CLI starts one for you (log at `.sdlicit-server.log` in the repo root).

See the [CLI guide](cli.md) for the menu structure.

### VS Code extension (GUI)

```bash
cd extension
npm install
npm run compile
```

Open the `extension/` folder in VS Code and press `F5` to launch an Extension Development Host, or package it with `vsce package` and install the resulting `.vsix`. The extension auto-starts the backend for you (`sdlicit.autoStartServer` setting) if one isn't already running.

See the [Extension guide](extension.md) for the sidebar views and commands.

## 4. Point a client at a project

Both clients need a project directory containing `.sdlicit/config.yaml`. [`examples/demo/`](https://github.com/JensKrijgsman/Sdlicit-public/tree/master/examples/demo) is a ready made one — the CLI defaults to it.

A minimal `.sdlicit/config.yaml` for a new project:

```yaml
provider: openrouter
model: openai/gpt-5.4-nano
```

# `services/` — Backing infrastructure

Docker Compose stack for the optional runtime services Sdlicit connects to.

## Layout

```
services/
├── compose.yaml    Orchestrates all services
├── lightrag/       LightRAG HTTP service (graph + vector retrieval)
├── ollama/         Local LLM inference (models/ not shipped)
└── db/             PostgreSQL init scripts (demo time tracking schema)
```

## Usage

```bash
docker compose -f services/compose.yaml up -d
```

Configure endpoints in the project `.sdlicit/config.yaml` and root `.env`.
Model weights under `services/ollama/models/` are excluded from this archive;
pull models locally with Ollama before use.

# `examples/demo/` — Example workspace

An already populated Sdlicit project: a time allocation pilot for subsidised
research projects. Point the CLI or extension at this directory to explore
a finished run without generating anything yourself.

## Layout

```
examples/demo/
└── .sdlicit/
    ├── config.yaml       Workspace and KB settings
    ├── kb_manifest.json  Ingested knowledge sources
    ├── knowledge/        LightRAG document store (demo scope)
    ├── artifacts/        SOW, SRS, ADRs, personas, stories
    └── sessions/         CLI session history (sample runs)
```

## Usage

```bash
uv run sdlicit-server
cd cli && python cli_client.py   # defaults to this directory
```

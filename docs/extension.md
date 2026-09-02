# VS Code extension

The GUI frontend. Talks to the backend over REST at `sdlicit.serverUrl` (default `http://localhost:8000`).

## Setup

```bash
cd extension
npm install
npm run compile
```

Open the `extension/` folder in VS Code and press `F5` to launch an Extension Development Host, or package it with `vsce package` and install the resulting `.vsix`. Open a project directory containing `.sdlicit/config.yaml` as your workspace — [`examples/demo/`](https://github.com/JensKrijgsman/Sdlicit-public/tree/master/examples/demo) is a ready made one.

## Sidebar views

- **SDLC Artifacts** — tree of everything generated for the project, with accept/decline/regenerate actions and knowledge base ingestion status per artifact.
- **Dashboard** — coverage metrics (requirement to scenario, trace links), quality overview, and recent activity.
- **Knowledge Base** — the ingested corpus, with per-file ingestion status.
- **Sessions** — history of Theory of Mind sessions for the project.

Plus a **Chat** panel and a **Status & Tokens** panel in the bottom panel area.

## Core commands

Search "Sdlicit" in the Command Palette:

- **Guided Flow (Full Pipeline)** — the whole SOW → SRS → Personas → Stories → ADRs → BDD sequence.
- **New Artifact…** — create a single artifact of a given type.
- **Suggest ADR Directions** — ask the ADR agent which architectural decisions are worth writing next.
- **Review ADR (Multi-Agent)** — joint ADR + Requirement + Theory-of-Mind review.
- **Check Traceability** / **Open Trace Graph** — validate an artifact's links and browse the traceability graph.
- **Ingest Documents into KB** / **Query Knowledge Base**.
- **Open in Canvas** — the rich structured editing surface (section-by-section editing, inline Socratic elicitation, AI Assist), as an alternative to the read oriented view panels.
- **Review BDD Scenarios** — step through generated Gherkin scenarios for a requirement with accept/reject verdicts, importance, and notes.

## Configuration

| Setting | Default | Description |
|---|---|---|
| `sdlicit.serverUrl` | `http://localhost:8000` | Backend URL |
| `sdlicit.autoStartServer` | `true` | Auto-start the backend on activation if unreachable |
| `sdlicit.showCallNotifications` | — | Show a notification per backend call |
| `sdlicit.models.default` | — | Default model override shown in the UI |

## Known limitations

The Dashboard's Questions tab only reflects Socratic probes from panels currently open, not a persisted history across sessions — each panel tracks its own pending probe in memory and there is no durable cross-session log for this yet.

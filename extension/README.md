# Sdlicit — VS Code extension

The GUI frontend for the Sdlicit SDLC artifact framework: create Statements of Work, Architecture Decision Records, SRS documents, personas, user stories, and BDD/Gherkin scenarios, grounded in a project knowledge base with Socratic elicitation and traceability checks. Talks to the Sdlicit backend over REST at `sdlicit.serverUrl` (default `http://localhost:8000`).

## Prerequisites

The backend must be running. With `sdlicit.autoStartServer` enabled (the default), the extension starts it for you (`uv run uvicorn sdlicit.main:app`) the first time it can't reach the server. Otherwise, start it yourself from the repository root:

```bash
uv run sdlicit-server
```

## Setup

```bash
cd extension
npm install
npm run compile
```

Open the `extension/` folder in VS Code and press `F5` to launch an Extension Development Host, or package it with `vsce package` and install the resulting `.vsix`.

Open a project directory containing `.sdlicit/config.yaml` as your workspace (see [`examples/demo/`](../examples/demo/) for a ready made one) — the extension activates against that project.

## What's in the sidebar

- **SDLC Artifacts** — tree view of everything generated for the project (SOW, ADRs, SRS, personas, stories, BDD scenarios), with accept/decline/regenerate actions and knowledge base ingestion status per artifact.
- **Dashboard** — coverage metrics (requirement → scenario, trace links), quality overview, and recent activity.
- **Knowledge Base** — the ingested corpus (standards, briefs, prior artifacts), with per-file ingestion status.
- **Sessions** — history of Theory-of-Mind sessions for the project.

Plus a **Chat** panel and a **Status & Tokens** panel in the bottom panel area.

## Core commands

Open the Command Palette and search "Sdlicit":

- **Guided Flow (Full Pipeline)** — walks the whole SOW → SRS → Personas → Stories → ADRs → BDD sequence.
- **New Artifact…** — create a single artifact of a given type.
- **Suggest ADR Directions** — ask the ADR agent which architectural decisions are worth writing next, without proposing solutions.
- **Review ADR (Multi-Agent)** — joint ADR + Requirement + Theory-of-Mind review of an existing decision.
- **Check Traceability** / **Open Trace Graph** — validate an artifact's links and browse the traceability graph.
- **Ingest Documents into KB** / **Query Knowledge Base** — bring standards or reference material into the grounding corpus, or query it directly.
- **Open in Canvas** — the rich structured editing surface (section-by-section editing, inline Socratic elicitation, AI Assist) as an alternative to the read oriented view panels (View ADR, View SOW, etc).
- **Review BDD Scenarios** — step through generated Gherkin scenarios for a requirement with accept/reject verdicts, importance, and notes.

## Configuration

| Setting | Default | Description |
|---|---|---|
| `sdlicit.serverUrl` | `http://localhost:8000` | Backend URL. |
| `sdlicit.autoStartServer` | `true` | Auto-start the backend on activation if unreachable. |
| `sdlicit.showCallNotifications` | — | Show a notification per backend call. |
| `sdlicit.models.default` | — | Default model override shown in the UI. |

## Known limitations

There is no free-form, standing chat surface on the CLI side of this project (the CLI is a structured wizard) — the chat panel and its automatic Socratic follow-up suggestions are extension-only for now. The Dashboard's Questions tab only reflects Socratic probes from panels currently open, not a persisted history across sessions.

# Sdlicit

Sdlicit is a knowledge grounded LLM framework for structured SDLC artifact creation: Statements of Work, Architecture Decision Records, SRS documents, personas, user stories, and BDD/Gherkin scenarios. It is built around RAG grounded generation, Socratic elicitation, and end to end requirement traceability.

Three parts talk to one backend:

| Component | Path | What it is |
|---|---|---|
| **Backend** | [`src/sdlicit/`](https://github.com/JensKrijgsman/Sdlicit-public/tree/master/src/sdlicit) | FastAPI server and orchestrator: stages (intake, expansion, generation, composing), a knowledge base (LightRAG backed RAG), an MCP tool surface, and traceability analysis. |
| **CLI (TUI)** | [`cli/`](https://github.com/JensKrijgsman/Sdlicit-public/tree/master/cli) | A `rich` based terminal client. Pure presentation layer, talks to the backend exclusively over REST. |
| **VS Code extension (GUI)** | [`extension/`](https://github.com/JensKrijgsman/Sdlicit-public/tree/master/extension) | The graphical frontend: artifact panels, a trace graph, a guided flow wizard, and a knowledge base browser, all backed by the same REST API. |

Start with [Getting started](getting-started.md), or read the [Architecture](architecture.md) page for how the pieces fit together.

## Try it without setting up a project

- [`examples/demo/`](https://github.com/JensKrijgsman/Sdlicit-public/tree/master/examples/demo) — a finished example project you can point the CLI or extension at directly.
- [`examples/jabref_replay/`](https://github.com/JensKrijgsman/Sdlicit-public/tree/master/examples/jabref_replay) — a notebook that runs the real pipeline against a real client brief and shows the generated ADRs next to actual historical decisions from the [JabRef](https://github.com/JabRef/jabref) open source project.

## License

AGPL-3.0-or-later.

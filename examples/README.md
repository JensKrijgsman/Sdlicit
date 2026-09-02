# Examples

Two ways to see Sdlicit produce something real, without wiring up your own
project first.

- **[`demo/`](demo/)** — a complete example project (`.sdlicit/` workspace):
  a client brief, a generated SOW/SRS/ADRs/personas/user stories, and an
  ingested ISO-standards knowledge base. Point the CLI or extension at it
  (`demo/` as the project directory) to explore a finished run without
  generating anything yourself.

- **[`jabref_replay/`](jabref_replay/)** — a notebook that runs the pipeline
  live against a real client brief and compares the output to real,
  historical architecture decisions from the [JabRef](https://github.com/JabRef/jabref)
  open-source project. This is the one to run if you want to see the system
  actually work end to end.

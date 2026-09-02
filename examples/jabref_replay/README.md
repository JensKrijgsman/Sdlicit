# JabRef ADR replay

A small usability demo: feed a real project brief through Sdlicit's actual
pipeline (SOW → SRS → ADRs) and compare what it generates against **real,
historical decisions** made by [JabRef](https://github.com/JabRef/jabref), an
established open-source reference manager with 50+ published ADRs.

Open [`replay.ipynb`](replay.ipynb) and run it top to bottom.

## Setup

From `sdlicit-public/`:

```bash
uv sync --group examples   # installs notebook + ipykernel on top of the base package
cp .env.example .env       # then fill in OPENROUTER_API_KEY (or configure Ollama)
```

Then, from this directory:

```bash
jupyter notebook replay.ipynb
```

No knowledge-base setup is required — the demo runs with `enable_rag=False`
by default. See the notebook's last section for how to ground it against a
real KB instead.

## What's here

- `replay.ipynb` — the demo notebook.
- `replay_pipeline.py` — the pipeline runner (`run_replay`). It drives the
  same `Orchestrator` the FastAPI backend uses, sequentially: brief → SOW →
  SRS → one ADR at a time, each with every prior ADR as context. No
  dependency beyond `sdlicit` itself + PyYAML.
- `data/brief.md` — a client brief describing JabRef (problem statement,
  personas, goals, features, constraints) as if commissioning the project.
- `data/replay_dataset.jsonl` — 50+ real ADRs mined from JabRef's
  `docs/decisions/` on GitHub: id, title, context, considered options, and
  the decision actually made, with the commit author and date.

## Why JabRef

It's real, it's public, and its ADRs cover a genuinely broad set of
architectural domains (build tooling, testing, storage, i18n, parsing,
serialization, CI/CD, UI, and even a couple of recent AI/ML decisions) — a
much more honest test of "does this generalize" than a synthetic example.

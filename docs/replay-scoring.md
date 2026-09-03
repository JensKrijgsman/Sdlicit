# Replay and traceability scoring

`benchmark/` is a small standalone tool for scoring Sdlicit output against
a gold reference, or scoring a project's own traceability. It ships in the
repo but is not part of the installed `sdlicit` package — it is meant to
be run directly against your own generated artifacts.

## What it measures

Two signals, kept separate on purpose rather than blended into one score.
Blending them hides the exact failure mode that matters: a generated
decision can agree with the real one in substance while sharing no
rejected option vocabulary, and a single number would call that a miss.

**Alternatives jaccard** — a surface vocabulary indicator. Character level
similarity (Ratcliff-Obershelp ratio) between the *rejected* options on
each side, thresholded at `tau` (default 0.6). A low score here does not
mean the decision is wrong: paraphrased architecture prose rarely clears a
literal character similarity threshold, and this metric structurally
cannot see the chosen decision at all.

**Decision agreement** — an LLM judge comparing the *chosen* decision text
on each side. Reports `matches`, `different-justifiable` (a different but
still coherent choice), or `different-incoherent` (a different and
unreasonable choice), with one or two sentences of reasoning. This is the
substantive signal.

**Traceability coverage** — reuses the same `TraceService` the backend
uses for its own traceability checks (structural link validity, and
optionally semantic/full mode). No new scoring logic, just a CLI facing
wrapper around the production analyser.

## Running it

From `sdlicit-public/`:

```bash
uv sync --group dev
cp .env.example .env   # OPENROUTER_API_KEY, needed for the decision judge
```

Score a set of generated/gold decision pairs:

```bash
python benchmark/score_replay.py --pairs pairs.jsonl
```

`pairs.jsonl` is one JSON object per line:

```json
{
  "id": "ADR-0017",
  "topic": "OpenAI function calling",
  "generated_decision": "...",
  "generated_alternatives": ["...", "..."],
  "gold_decision": "...",
  "gold_alternatives": ["...", "..."]
}
```

Score a project's traceability instead (no LLM needed for structural mode):

```bash
python benchmark/score_replay.py --project examples/demo --trace-mode structural
```

Both at once, and write the full JSON report to a file:

```bash
python benchmark/score_replay.py --pairs pairs.jsonl --project examples/demo --out report.json
```

Pass `--skip-judge` to run only the alternatives jaccard (no LLM call, no
API key needed). Pass `--model` to use a different judge model than the
default `openai/gpt-5.4-nano` — the judge runs once per pair, a cheap
model is enough for this.

## Building pairs.jsonl from your own replay

[`examples/jabref_replay/replay.ipynb`](https://github.com/JensKrijgsman/Sdlicit-public/blob/main/examples/jabref_replay/replay.ipynb)
scores its own comparison this way (see its "How close is close" section)
by calling `benchmark/scoring.py` directly from the notebook rather than
shelling out to the CLI. That is the pattern to copy: pair each
`ADRReplayStep` your own run produces with the matching gold record, then
either call `alternatives_overlap`/`judge_decision_agreement` in process
or write the pairs to a JSONL file and use the CLI.

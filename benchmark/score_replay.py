#!/usr/bin/env python3
"""Score generated ADRs against a gold reference, and/or a project's traceability.

Usage:

    python benchmark/score_replay.py --pairs pairs.jsonl
    python benchmark/score_replay.py --project examples/demo --trace-mode structural
    python benchmark/score_replay.py --pairs pairs.jsonl --project examples/demo

pairs.jsonl: one JSON object per line, each with the fields
    id                     any identifier, used only for reporting
    topic                  the decision topic/title
    generated_decision     Sdlicit's chosen decision text
    generated_alternatives list of Sdlicit's rejected alternative strings
    gold_decision          the real, historical chosen decision text
    gold_alternatives      list of the real, historical rejected options

See examples/jabref_replay/replay.ipynb for a worked example that builds
this file from a real replay run.

Requires OPENROUTER_API_KEY (or OLLAMA_HOST) in the environment for the
decision agreement judge, unless --skip-judge is passed. Defaults to a
cheap model (openai/gpt-5.4-nano) since the judge runs once per pair.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from statistics import mean

from dotenv import load_dotenv
from scoring import DEFAULT_TAU, alternatives_overlap, judge_decision_agreement
from trace_score import score_traceability

load_dotenv()


def _configure_judge_lm(provider: str, model: str) -> None:
    import dspy

    if provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            print(
                "! OPENROUTER_API_KEY not set — pass --skip-judge or set it",
                file=sys.stderr,
            )
            sys.exit(1)
        dspy.configure(lm=dspy.LM(model=f"openrouter/{model}", api_key=api_key, timeout=120))
    elif provider == "ollama":
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        dspy.configure(lm=dspy.LM(model=f"ollama_chat/{model}", api_base=host, timeout=300))
    else:
        raise ValueError(f"Unknown provider: {provider}")


async def _score_pairs(pairs: list[dict], *, run_judge: bool, tau: float) -> list[dict]:
    from sdlicit.agents.llm.gateway import LLMGateway

    gateway = LLMGateway() if run_judge else None
    results = []
    for pair in pairs:
        overlap = alternatives_overlap(
            pair.get("generated_alternatives", []),
            pair.get("gold_alternatives", []),
            tau=tau,
        )
        row = {
            "id": pair.get("id", ""),
            "topic": pair.get("topic", ""),
            "alternatives_jaccard": overlap.jaccard,
            "alternatives_max_ratio": overlap.max_ratio,
        }
        if run_judge and gateway is not None:
            verdict = await judge_decision_agreement(
                gateway.predict,
                topic=pair.get("topic", ""),
                generated_decision=pair.get("generated_decision", ""),
                gold_decision=pair.get("gold_decision", ""),
            )
            row["decision_agreement"] = verdict.agreement
            row["judge_reasoning"] = verdict.reasoning
        results.append(row)
    return results


def _print_summary(rows: list[dict]) -> None:
    print(f"\n{'id':<14}{'topic':<40}{'jaccard':>8}{'max_ratio':>10}  agreement")
    print("-" * 90)
    for r in rows:
        print(
            f"{r['id']:<14}{r['topic'][:38]:<40}{r['alternatives_jaccard']:>8.3f}"
            f"{r['alternatives_max_ratio']:>10.3f}  {r.get('decision_agreement', 'n/a')}"
        )

    jaccards = [r["alternatives_jaccard"] for r in rows]
    print(f"\nMean alternatives jaccard : {mean(jaccards):.3f} (surface vocabulary only, see caveat)")
    if rows and "decision_agreement" in rows[0]:
        from collections import Counter

        counts = Counter(r["decision_agreement"] for r in rows)
        total = len(rows)
        for label in ("matches", "different-justifiable", "different-incoherent"):
            n = counts.get(label, 0)
            print(f"Decision agreement — {label:<24}: {n}/{total} ({n / total:.0%})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pairs", type=Path, help="JSONL of generated/gold decision pairs")
    parser.add_argument("--project", type=Path, help="Project workspace to score traceability for")
    parser.add_argument("--trace-mode", default="structural", choices=["structural", "semantic", "full"])
    parser.add_argument("--graph-source", default="frontmatter", choices=["frontmatter", "lightrag"])
    parser.add_argument("--provider", default="openrouter", choices=["openrouter", "ollama"])
    parser.add_argument("--model", default="openai/gpt-5.4-nano")
    parser.add_argument("--tau", type=float, default=DEFAULT_TAU)
    parser.add_argument("--skip-judge", action="store_true", help="Skip the LLM decision agreement judge")
    parser.add_argument("--out", type=Path, help="Write the full JSON report here")
    args = parser.parse_args()

    if not args.pairs and not args.project:
        parser.error("pass --pairs, --project, or both")

    report: dict = {}

    if args.pairs:
        pairs = [json.loads(line) for line in args.pairs.read_text(encoding="utf-8").splitlines() if line.strip()]
        run_judge = not args.skip_judge
        if run_judge:
            _configure_judge_lm(args.provider, args.model)
        rows = asyncio.run(_score_pairs(pairs, run_judge=run_judge, tau=args.tau))
        _print_summary(rows)
        report["decision_pairs"] = rows

    if args.project:
        trace = score_traceability(args.project, mode=args.trace_mode, graph_source=args.graph_source)
        print(f"\nTraceability ({args.trace_mode}, {args.graph_source}):")
        for key in ("structural_coverage_pct", "semantic_coverage_pct", "total_links", "broken_links_count"):
            if key in trace:
                print(f"  {key}: {trace[key]}")
        report["traceability"] = trace

    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nFull report written to {args.out}")


if __name__ == "__main__":
    main()

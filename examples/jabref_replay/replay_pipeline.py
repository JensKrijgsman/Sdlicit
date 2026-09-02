"""Sequential ADR replay pipeline — drives Sdlicit like a human would.

Feeds a real client brief through Sdlicit's Orchestrator (SOW -> SRS ->
ADRs, optionally Stories + BDD), one ADR topic at a time, accumulating each
generated ADR as context for the next — exactly like an analyst would build
up a decision log over a project.

This is the "usability showcase" version of the replay pipeline the Sdlicit
thesis notebooks use for evaluation: same core mechanics, none of the
scoring/statistics/plotting apparatus. It has no dependency beyond the
`sdlicit` package itself (+ PyYAML).

Usage::

    from replay_pipeline import run_replay
    result = await run_replay(
        client_brief=BRIEF,
        workspace=Path("./workspace"),
        adr_topics=["Use Gradle as build tool", ...],
        provider="openrouter",
    )
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from sdlicit.helpers.context_budget import assemble_context, parse_adr_records, rank_prior_adrs

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ADRReplayStep:
    """Result for a single ADR topic in the replay sequence."""

    adr_id: str
    topic: str
    # Suggestion analysis
    suggestion_hit: bool = False
    suggestion_rank: int | None = None
    all_suggestions: list[str] = field(default_factory=list)
    # Generated output
    generated_adr: str = ""
    decision: str = ""
    alternatives: list[str] = field(default_factory=list)
    # Socratic questions logged
    socratic_questions: list[str] = field(default_factory=list)
    # Metrics
    suggest_latency_s: float = 0.0
    generate_latency_s: float = 0.0
    suggest_tokens: int = 0
    generate_tokens: int = 0
    # Errors
    error: str = ""


@dataclass
class StoryResult:
    """Result from user story generation."""

    stories: list[dict] = field(default_factory=list)
    latency_s: float = 0.0
    error: str = ""


@dataclass
class BDDResult:
    """Result from BDD/Gherkin generation."""

    personas: list[dict] = field(default_factory=list)
    scenarios: list[dict] = field(default_factory=list)
    latency_s: float = 0.0
    error: str = ""


@dataclass
class ReplayResult:
    """Full replay run result."""

    workspace: Path
    provider: str = ""
    model: str = ""
    # Bootstrap artefacts
    sow: str = ""
    srs: str = ""
    requirements: list[dict] = field(default_factory=list)
    # Per-ADR results (in sequence order)
    steps: list[ADRReplayStep] = field(default_factory=list)
    # Downstream artifacts (Stories + BDD)
    story_result: StoryResult | None = None
    bdd_result: BDDResult | None = None
    # Aggregate metrics
    total_latency_s: float = 0.0
    total_tokens: int = 0
    sow_latency_s: float = 0.0
    srs_latency_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def suggestion_hit_rate(self) -> float:
        """Fraction of topics where the system suggested the target."""
        hits = sum(1 for s in self.steps if s.suggestion_hit)
        return hits / len(self.steps) if self.steps else 0.0

    @property
    def completed_count(self) -> int:
        return sum(1 for s in self.steps if s.generated_adr and not s.error)

    def to_dict(self) -> dict:
        """Serialize for JSON caching."""
        from dataclasses import asdict
        d = asdict(self)
        d["workspace"] = str(self.workspace)
        return d


# ---------------------------------------------------------------------------
# DSPy token tracking
# ---------------------------------------------------------------------------


def _count_history_len() -> int:
    try:
        import dspy
        lm = dspy.settings.lm
        if hasattr(lm, "history"):
            return len(lm.history)
    except Exception:
        pass
    return 0


def _sum_tokens_since(start_idx: int) -> int:
    try:
        import dspy
        lm = dspy.settings.lm
        if hasattr(lm, "history"):
            total = 0
            for entry in lm.history[start_idx:]:
                usage = {}
                if isinstance(entry, dict):
                    usage = entry.get("usage", {}) or {}
                    if not usage and "response" in entry:
                        resp = entry["response"]
                        if hasattr(resp, "usage") and resp.usage:
                            usage = {"total_tokens": getattr(resp.usage, "total_tokens", 0) or 0}
                total += usage.get("total_tokens", 0)
            return total
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------------------
# Suggestion matching
# ---------------------------------------------------------------------------


def _topic_matches(suggestion_title: str, target_topic: str, threshold: float = 0.5) -> bool:
    """Check if a suggested topic matches the target (fuzzy)."""
    from difflib import SequenceMatcher
    a = suggestion_title.lower().strip()
    b = target_topic.lower().strip()
    if a in b or b in a:
        return True
    words_a = set(a.split())
    words_b = set(b.split())
    if words_a and words_b:
        overlap = len(words_a & words_b) / len(words_a | words_b)
        if overlap >= 0.4:
            return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


# ---------------------------------------------------------------------------
# Main replay runner
# ---------------------------------------------------------------------------


async def run_replay(
    client_brief: str,
    workspace: Path,
    adr_topics: list[str],
    *,
    provider: str = "openrouter",
    model: str = "openai/gpt-5.4-nano",
    kb_workdir: Path | None = None,
    enable_rag: bool = True,
    enable_socratic: bool = False,
    enable_tom: bool = False,
    context_override: str = "",
    # Non-sequential support
    prior_adrs: list[str] | None = None,
    start_adr_index: int = 1,
    # Downstream generation
    generate_stories: bool = False,
    generate_bdd: bool = False,
) -> ReplayResult:
    """Run sequential ADR replay against a real Sdlicit project workspace.

    Args:
        client_brief: Raw project description (the "client brief").
        workspace: Directory for workspace artefacts.
        adr_topics: Ordered list of ADR topics to replay.
        provider: 'openrouter' or 'ollama'.
        model: Model identifier.
        kb_workdir: Pre-built LightRAG workdir to copy in (e.g. ISO standards).
        enable_rag: Enable knowledge base / RAG queries.
        enable_socratic: Enable Socratic questioning agent.
        enable_tom: Enable Theory of Mind agent.
        context_override: Static context string injected instead of KB queries.
            When set, enable_rag is forced to False (no LightRAG needed).
        prior_adrs: Pre-existing ADR content to seed as prior context.
            Use for non-sequential starts (e.g. inject real ADR-0001..N-1).
        start_adr_index: Starting ADR number (1-indexed). ADR IDs will be
            numbered from this value.
        generate_stories: After ADRs, generate User Stories.
        generate_bdd: After ADRs, generate Personas + BDD/Gherkin scenarios.

    Returns:
        ReplayResult with per-ADR steps and aggregate metrics.
    """
    import shutil

    from sdlicit.agents.llm.dspy_modules import ADRGeneration
    from sdlicit.bootstrap import create_system
    from sdlicit.helpers.artifact_naming import _slugify

    # If context_override is set, force RAG off (no LightRAG needed)
    if context_override:
        enable_rag = False

    # --- Setup workspace ---
    workspace.mkdir(parents=True, exist_ok=True)
    sdlicit_dir = workspace / ".sdlicit"
    artifacts_dir = sdlicit_dir / "artifacts"
    adr_dir = artifacts_dir / "adr"
    bdd_dir = artifacts_dir / "bdd"
    for d in [sdlicit_dir, artifacts_dir, adr_dir, bdd_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Copy KB if provided and RAG is enabled
    if enable_rag and kb_workdir and kb_workdir.exists():
        kb_dest = sdlicit_dir / "knowledge" / "lightrag_workdir"
        if not kb_dest.exists():
            kb_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(kb_workdir, kb_dest)

    # Write config
    config_data = {
        "provider": provider,
        "model": model,
        "enable_rag": enable_rag,
        "enable_socratic": enable_socratic,
        "enable_tom": enable_tom,
        "context_override": context_override,
    }
    config_file = sdlicit_dir / "config.yaml"
    # Always write fresh config (settings may have changed)
    from sdlicit.config import SdlicitConfig
    cfg = SdlicitConfig(project_dir=workspace, **config_data)
    data = cfg.model_dump(exclude={"project_dir", "api_key"})
    config_file.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")

    # Bootstrap the system
    config, orch = create_system(workspace)

    result = ReplayResult(
        workspace=workspace,
        provider=config.provider,
        model=config.model,
    )

    pipeline_start = time.time()

    # === Stage 1: SOW from brief ===
    print(f"[replay] Generating SOW from brief ({len(client_brief)} chars)...")
    t0 = time.time()
    try:
        sow_result = await orch.create_sow(client_brief)
        result.sow = sow_result.raw_text or ""
        (artifacts_dir / "sow.md").write_text(result.sow, encoding="utf-8")
        result.sow_latency_s = round(time.time() - t0, 2)
        # Ingest SOW to KB (no-op if static provider)
        with contextlib.suppress(Exception):
            await orch.ingest_artifact(result.sow, "sow", "brief")
        print(f"  SOW: {len(result.sow)} chars ({result.sow_latency_s}s)")
    except Exception as e:
        result.errors.append(f"SOW: {e}")
        print(f"  SOW ERROR: {e}")

    # === Stage 2: SRS from SOW ===
    if result.sow:
        print("[replay] Generating SRS...")
        t0 = time.time()
        try:
            srs_result = await orch.generate_srs(result.sow)
            raw = srs_result.raw_text or ""
            result.srs = raw
            try:
                parsed = json.loads(raw)
                items = parsed.get("requirements", [])
                result.requirements = [{
                    "req_id": r.get("req_id", ""),
                    "statement": r.get("statement", ""),
                    "category": r.get("category", ""),
                } for r in items]
            except (json.JSONDecodeError, AttributeError):
                pass
            # Write SRS as markdown (TraceService expects bullet format)
            srs_md = _render_srs_markdown(result.requirements)
            (artifacts_dir / "srs.md").write_text(srs_md, encoding="utf-8")
            result.srs_latency_s = round(time.time() - t0, 2)
            # Ingest SRS (no-op if static)
            with contextlib.suppress(Exception):
                await orch.ingest_artifact(raw, "srs", "srs")
            print(f"  SRS: {len(result.requirements)} requirements ({result.srs_latency_s}s)")
        except Exception as e:
            result.errors.append(f"SRS: {e}")
            print(f"  SRS ERROR: {e}")

    # === Stage 3: Sequential ADR replay ===
    # Initialize prior ADRs from injected context (non-sequential start)
    prior_adr_contents: list[str] = list(prior_adrs) if prior_adrs else []
    if prior_adrs:
        print(f"[replay] Injected {len(prior_adrs)} prior ADRs as context")

    for idx, topic in enumerate(adr_topics):
        adr_num = start_adr_index + idx
        adr_id = f"ADR-{adr_num:04d}"
        step = ADRReplayStep(adr_id=adr_id, topic=topic)
        print(f"\n[replay] [{idx+1}/{len(adr_topics)}] {topic[:60]}...")

        # --- 3a. Suggest directions ---
        t0 = time.time()
        hist_start = _count_history_len()
        try:
            sow_text = result.sow if result.sow else client_brief
            all_req_ids = set(r.get("req_id", "") for r in result.requirements)
            uncovered = sorted(all_req_ids)
            uncovered_str = ", ".join(uncovered) if uncovered else ""

            directions_result = await orch.suggest_adr_directions(
                brief=sow_text,
                prior_adrs=prior_adr_contents,
                downstream_artifacts=result.srs,
                uncovered_requirements=uncovered_str,
            )
            suggestions = [d.title for d in directions_result.adr_directions]
            step.all_suggestions = suggestions
            step.suggest_latency_s = round(time.time() - t0, 2)
            step.suggest_tokens = _sum_tokens_since(hist_start)

            for rank, s_title in enumerate(suggestions, 1):
                if _topic_matches(s_title, topic):
                    step.suggestion_hit = True
                    step.suggestion_rank = rank
                    break

            hit_str = f"HIT (rank {step.suggestion_rank})" if step.suggestion_hit else "MISS"
            print(f"  Suggestions: {len(suggestions)} | Target: {hit_str}")

        except Exception as e:
            step.suggest_latency_s = round(time.time() - t0, 2)
            result.errors.append(f"{adr_id} suggest: {e}")
            print(f"  Suggest ERROR: {e}")

        # --- 3b. Generate ADR ---
        t0 = time.time()
        hist_start = _count_history_len()
        try:
            req_json = json.dumps(result.requirements[:15])

            # Rank prior ADRs by relatedness to this topic and bound them to
            # a token budget instead of passing every prior ADR unbounded.
            ranked = rank_prior_adrs(topic, parse_adr_records(prior_adr_contents))
            token_budget = int(orch.config.model_context_window * orch.config.compact_threshold_pct)
            prior_context, dropped = assemble_context(ranked, token_budget)
            if dropped:
                print(f"  ({dropped} prior ADR(s) dropped past context budget)")

            # KB context for the ADR topic
            kb_text = ""
            if orch.kb_router is not None:
                try:
                    chunks = await orch.kb_router.query(
                        f"{topic} {client_brief[:200]}", probe_first=True
                    )
                    kb_text = "\n".join(c.text for c in chunks if c.text)
                except Exception:
                    pass
            elif orch.kb is not None:
                try:
                    chunks = await orch.kb.query(f"{topic} {client_brief[:200]}")
                    kb_text = "\n".join(c.text for c in chunks if c.text)
                except Exception:
                    pass

            prediction = await orch.llm.predict(
                ADRGeneration,
                topic=topic,
                requirements=req_json,
                prior_adrs=prior_context,
                kb_context=kb_text,
            )

            adr_output = getattr(prediction, "adr", None)
            step.generate_latency_s = round(time.time() - t0, 2)
            step.generate_tokens = _sum_tokens_since(hist_start)

            if adr_output is None:
                step.error = "No output from ADRGeneration"
                print("  Generate: NO OUTPUT")
                result.steps.append(step)
                continue

            # Extract structured fields
            title = getattr(adr_output, "title", topic)
            context = getattr(adr_output, "context", "")
            decision = getattr(adr_output, "decision", "")
            alternatives = getattr(adr_output, "alternatives", [])
            consequences = getattr(adr_output, "consequences", "")
            implements = getattr(adr_output, "implements", [])

            # Build MADR-format markdown with traceability frontmatter
            impl_str = ", ".join(implements) if implements else ""
            alts_text = "\n".join(f"- {a}" for a in alternatives) if alternatives else "- None considered"
            adr_content = (
                f"---\nid: {adr_id}\nstatus: proposed\n"
                f"implements: [{impl_str}]\n---\n\n"
                f"# {title}\n\n"
                f"## Status\nProposed\n\n"
                f"## Context\n{context}\n\n"
                f"## Decision\n{decision}\n\n"
                f"## Alternatives\n{alts_text}\n\n"
                f"## Consequences\n{consequences}\n"
            )

            slug = _slugify(topic, max_len=30)
            adr_path = adr_dir / f"{adr_id}-{slug}.md"
            adr_path.write_text(adr_content, encoding="utf-8")

            step.generated_adr = adr_content
            step.decision = decision
            step.alternatives = alternatives if isinstance(alternatives, list) else []

            print(f"  Generated: {len(adr_content)} chars, "
                  f"{len(step.alternatives)} alternatives ({step.generate_latency_s}s)")

            # --- 3c. Ingest into KB (no-op if static provider) ---
            with contextlib.suppress(Exception):
                await orch.ingest_artifact(adr_content, "adr", adr_id)

            # Update priors for next iteration
            prior_adr_contents.append(adr_content)

        except Exception as e:
            step.generate_latency_s = round(time.time() - t0, 2)
            step.error = str(e)
            result.errors.append(f"{adr_id} generate: {e}")
            print(f"  Generate ERROR: {e}")

        # --- 3d. Log Socratic questions ---
        if hasattr(orch, "socratic") and orch.socratic is not None:
            try:
                questions = getattr(orch.socratic, "pending_questions", [])
                if questions:
                    step.socratic_questions = [str(q) for q in questions]
                    print(f"  Socratic questions: {len(questions)}")
            except Exception:
                pass

        result.steps.append(step)

    # === Stage 4: User Stories (optional) ===
    if generate_stories and result.steps:
        print("\n[replay] Generating User Stories...")
        result.story_result = await _generate_stories(orch, result)

    # === Stage 5: BDD / Gherkin (optional) ===
    if generate_bdd and result.steps:
        print("\n[replay] Generating Personas + BDD scenarios...")
        result.bdd_result = await _generate_bdd(orch, result, bdd_dir)

    # --- Finalize ---
    result.total_latency_s = round(time.time() - pipeline_start, 2)
    result.total_tokens = sum(s.suggest_tokens + s.generate_tokens for s in result.steps)

    print(f"\n[replay] Done: {result.completed_count}/{len(adr_topics)} ADRs "
          f"| {result.total_latency_s}s | suggestion hit rate: {result.suggestion_hit_rate:.0%}")

    return result


# ---------------------------------------------------------------------------
# Downstream generation: Stories + BDD
# ---------------------------------------------------------------------------


async def _generate_stories(orch, result: ReplayResult) -> StoryResult:
    """Generate user stories from generated ADRs + SRS requirements."""
    from sdlicit.helpers.artifact_store import ArtifactStore, StoriesArtifact, UserStoryItem

    sr = StoryResult()
    t0 = time.time()
    try:
        # Build personas input (generate from ADRs)
        adr_contents = [s.generated_adr for s in result.steps if s.generated_adr]
        persona_result = await orch.bdd.generate_personas(
            json.dumps({"adrs": adr_contents[:5], "srs": result.srs})
        )
        personas_raw = json.loads(persona_result.raw_text or "{}").get("personas", [])

        # Generate stories
        req_json = json.dumps(result.requirements)
        personas_json = json.dumps(personas_raw)
        adrs_json = json.dumps([{
            "id": s.adr_id, "title": s.topic, "decision": s.decision
        } for s in result.steps if s.generated_adr])

        story_result = await orch.user_story.generate(
            personas=personas_json,
            requirements=req_json,
            adrs=adrs_json,
        )
        stories_raw = json.loads(story_result.raw_text or "{}").get("stories", [])
        sr.stories = stories_raw
        sr.latency_s = round(time.time() - t0, 2)

        # Save via ArtifactStore (JSON primary format for stories)
        store = ArtifactStore(result.workspace)
        stories_data = StoriesArtifact(
            stories=[UserStoryItem(**s) for s in stories_raw],
        )
        store.save_both(stories_data)
        print(f"  Stories: {len(sr.stories)} generated ({sr.latency_s}s)")

    except Exception as e:
        sr.error = str(e)
        sr.latency_s = round(time.time() - t0, 2)
        print(f"  Stories ERROR: {e}")

    return sr


async def _generate_bdd(orch, result: ReplayResult, bdd_dir: Path) -> BDDResult:
    """Generate personas + BDD/Gherkin from ADRs + requirements."""
    from sdlicit.helpers.artifact_store import (
        ArtifactStore,
        BDDArtifact,
        BDDScenario,
        PersonaItem,
        PersonasArtifact,
    )

    br = BDDResult()
    t0 = time.time()
    try:
        # Generate personas from ADRs
        adr_contents = [s.generated_adr for s in result.steps if s.generated_adr]
        persona_result = await orch.bdd.generate_personas(
            json.dumps({"adrs": adr_contents[:5], "srs": result.srs})
        )
        personas_raw = json.loads(persona_result.raw_text or "{}").get("personas", [])
        br.personas = personas_raw

        # Save personas via ArtifactStore (JSON primary)
        store = ArtifactStore(result.workspace)
        personas_data = PersonasArtifact(
            personas=[PersonaItem(**p) for p in personas_raw],
        )
        store.save_both(personas_data)

        # Generate BDD for each persona
        req_text = "\n".join(
            f"- {r['req_id']}: {r['statement']}" for r in result.requirements
        )
        for persona in personas_raw:
            try:
                gherkin_result = await orch.bdd.generate_gherkin(
                    json.dumps(persona), req_text
                )
                gherkin_text = gherkin_result.raw_text or ""
                scenario_id = persona.get("persona_id", "unknown")
                br.scenarios.append({
                    "persona_id": scenario_id,
                    "gherkin": gherkin_text,
                    "valid": not bool(gherkin_result.suggestions),
                })
                # Save BDD via ArtifactStore (JSON primary)
                bdd_data = BDDArtifact(
                    persona_slug=scenario_id,
                    scenarios=[BDDScenario(scenario_id=scenario_id, gherkin=gherkin_text)],
                    raw_gherkin=gherkin_text,
                )
                store.save(bdd_data)
            except Exception as e:
                br.scenarios.append({
                    "persona_id": persona.get("persona_id", "unknown"),
                    "error": str(e),
                })

        br.latency_s = round(time.time() - t0, 2)
        print(f"  BDD: {len(br.personas)} personas, "
              f"{len(br.scenarios)} scenarios ({br.latency_s}s)")

    except Exception as e:
        br.error = str(e)
        br.latency_s = round(time.time() - t0, 2)
        print(f"  BDD ERROR: {e}")

    return br


# ---------------------------------------------------------------------------
# Trace analysis
# ---------------------------------------------------------------------------


async def analyse_traces(workspace: Path, mode: str = "semantic") -> dict:
    """Run trace analysis on a completed replay workspace.

    Returns a dict with coverage metrics comparing generated artifacts.
    """
    from sdlicit.config import SdlicitConfig
    from sdlicit.expansion_stage.traceability import TraceService

    config = SdlicitConfig.from_project(workspace)
    config.trace_check_mode = mode
    config.traceability_graph_source = "frontmatter"  # No KB needed

    trace_svc = TraceService.from_config(config, llm=None, kb=None)
    coverage = trace_svc.analyse(workspace)

    return {
        "mode": coverage.mode,
        "total_links": coverage.total_links,
        "valid_links": coverage.valid_links,
        "broken_links": coverage.broken_links_count,
        "structural_coverage_pct": coverage.structural_coverage_pct,
        "semantic_coverage_pct": coverage.semantic_coverage_pct,
        "mean_tfidf": coverage.mean_tfidf,
        "mean_jaccard": coverage.mean_jaccard,
        "mean_combined": coverage.mean_combined,
        "per_requirement_scores": coverage.per_requirement_scores,
        "artifact_counts": coverage.artifact_counts,
        "has_conflicts": coverage.has_conflicts,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_srs_markdown(requirements: list[dict]) -> str:
    """Convert requirements list to markdown with bullet format TraceService expects.

    Produces format like:
        - [REQ-AUTH-01] The system shall support user authentication
    """
    lines = ["# Software Requirements Specification\n"]
    # Group by category
    functional = [r for r in requirements if r.get("category") == "functional"]
    non_functional = [r for r in requirements if r.get("category") != "functional"]

    if functional:
        lines.append("\n## Functional Requirements\n")
        for r in functional:
            lines.append(f"- [{r['req_id']}] {r['statement']}")

    if non_functional:
        lines.append("\n## Non-Functional Requirements\n")
        for r in non_functional:
            lines.append(f"- [{r['req_id']}] {r['statement']}")

    # If no categorization worked, just list all
    if not functional and not non_functional:
        lines.append("\n## Requirements\n")
        for r in requirements:
            lines.append(f"- [{r['req_id']}] {r['statement']}")

    return "\n".join(lines) + "\n"

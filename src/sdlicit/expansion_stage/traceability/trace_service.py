"""Unified trace service — structural, semantic, and graph-based trace analysis.

Provides three levels of trace analysis (controlled via ``trace_check_mode`` config):

The service is configured via ``trace_check_mode`` in the project config:
  - ``structural`` — ID cross-reference validation only (fast, deterministic)
  - ``semantic`` — structural + content similarity analysis (requires scikit-learn)
  - ``full`` — structural + semantic + graph-based conflict detection (may use LLM)

Usage::

    service = TraceService.from_config(config)
    result = service.analyse(workspace)
    result = service.analyse(workspace, mode="full")  # override config
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Sequence

from sdlicit.logging import get_logger

if TYPE_CHECKING:
    from sdlicit.agents.llm.gateway import LLMGateway
    from sdlicit.config import SdlicitConfig
    from sdlicit.expansion_stage.tools.knowledge_base import KnowledgeBase

_log = get_logger("trace_service")

TraceCheckMode = Literal["structural", "semantic", "full"]

# ── ID patterns ──────────────────────────────────────────────────────────────

RE_REQ = re.compile(r"REQ-[A-Z]+-\d+")
RE_ADR = re.compile(r"ADR-\d+")
RE_PERSONA = re.compile(r"PERSONA-\d+")
RE_STORY = re.compile(r"STORY-\d+")
RE_BDD = re.compile(r"BDD-\d+")
RE_KB = re.compile(r"KB:[^\s,\]]+")

# ── Stopwords for semantic analysis ──────────────────────────────────────────

_STOPWORDS = frozenset(
    "a an the is are was were be been being have has had do does did will would "
    "shall should may might can could of in to for on with at by from as into "
    "through during before after above below between out off over under again "
    "further then once here there when where why how all each every both few "
    "more most other some such no nor not only own same so than too very s t "
    "just don should've that that'll these those this that am it its and but if "
    "or because until while about".split()
)

_TOKEN_RE = re.compile(r"[a-z][a-z0-9_-]{2,}")


def _tokenize(text: str) -> list[str]:
    """Lowercase tokenize, keep tokens >= 3 chars, remove stopwords."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class TraceLink:
    """A single cross-reference between artifacts."""

    source_id: str
    target_id: str
    link_type: str
    valid: bool = True
    # Semantic score (populated when mode includes semantic)
    semantic_score: float | None = None


@dataclass
class ArtifactCoverage:
    """Trace coverage for one artifact."""

    artifact_id: str
    artifact_type: str
    # Structural
    outgoing_links: int = 0
    valid_links: int = 0
    broken_links: int = 0
    # Semantic (best-match score against upstream requirements)
    semantic_score: float | None = None
    covered_by: list[str] = field(default_factory=list)


@dataclass
class TraceCoverage:
    """Complete trace analysis result for a workspace or project."""

    mode: TraceCheckMode = "structural"
    # Structural metrics
    total_links: int = 0
    valid_links: int = 0
    broken_links_count: int = 0
    structural_coverage_pct: float = 0.0
    broken_link_details: list[TraceLink] = field(default_factory=list)
    # Semantic metrics (populated when mode is semantic or full)
    semantic_coverage_pct: float | None = None
    mean_tfidf: float | None = None
    mean_jaccard: float | None = None
    mean_topic: float | None = None
    mean_combined: float | None = None
    per_requirement_scores: dict[str, float] = field(default_factory=dict)
    # LLM-judge semantic scores (populated when llm_judge=True)
    llm_judge_coverage_pct: float | None = None
    llm_judge_scores: dict[str, float] = field(default_factory=dict)
    # Per-artifact coverage (for UI display)
    artifacts: list[ArtifactCoverage] = field(default_factory=list)
    # Graph-based issues (populated when mode is full)
    graph_issues: list[dict] = field(default_factory=list)
    has_conflicts: bool = False
    conflict_assessment: str = ""
    # Artifact counts
    artifact_counts: dict[str, int] = field(default_factory=dict)
    # Known IDs in workspace
    known_ids: set[str] = field(default_factory=set)
    # Gherkin-specific semantic alignment (populated when mode is semantic or full)
    gherkin_alignment_pct: float | None = None
    per_scenario_alignment: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize for API responses."""
        return {
            "mode": self.mode,
            "total_links": self.total_links,
            "valid_links": self.valid_links,
            "broken_links_count": self.broken_links_count,
            "structural_coverage_pct": round(self.structural_coverage_pct, 2),
            "semantic_coverage_pct": (
                round(self.semantic_coverage_pct, 2)
                if self.semantic_coverage_pct is not None
                else None
            ),
            "mean_tfidf": round(self.mean_tfidf, 4) if self.mean_tfidf is not None else None,
            "mean_jaccard": round(self.mean_jaccard, 4) if self.mean_jaccard is not None else None,
            "mean_topic": round(self.mean_topic, 4) if self.mean_topic is not None else None,
            "mean_combined": round(self.mean_combined, 4) if self.mean_combined is not None else None,
            "per_requirement_scores": {
                k: round(v, 4) for k, v in self.per_requirement_scores.items()
            },
            "llm_judge_coverage_pct": (
                round(self.llm_judge_coverage_pct, 2)
                if self.llm_judge_coverage_pct is not None
                else None
            ),
            "llm_judge_scores": {
                k: round(v, 4) for k, v in self.llm_judge_scores.items()
            },
            "artifacts": [
                {
                    "artifact_id": a.artifact_id,
                    "artifact_type": a.artifact_type,
                    "outgoing_links": a.outgoing_links,
                    "valid_links": a.valid_links,
                    "broken_links": a.broken_links,
                    "semantic_score": (
                        round(a.semantic_score, 4)
                        if a.semantic_score is not None
                        else None
                    ),
                    "covered_by": a.covered_by,
                }
                for a in self.artifacts
            ],
            "graph_issues": self.graph_issues,
            "has_conflicts": self.has_conflicts,
            "conflict_assessment": self.conflict_assessment,
            "artifact_counts": self.artifact_counts,
            "gherkin_alignment_pct": (
                round(self.gherkin_alignment_pct, 2)
                if self.gherkin_alignment_pct is not None
                else None
            ),
            "per_scenario_alignment": {
                k: round(v, 4) for k, v in self.per_scenario_alignment.items()
            },
        }


# ── Internal document type for semantic analysis ─────────────────────────────


@dataclass
class _ArtefactDoc:
    """A single artifact's text with ID and type, used for semantic analysis."""

    artefact_id: str
    artefact_type: str
    text: str
    tokens: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = _tokenize(self.text)


# ── TraceService ─────────────────────────────────────────────────────────────


class TraceService:
    """Unified trace analysis: structural, semantic, and graph-based.

    Configured via ``trace_check_mode`` in the config. Can be overridden
    per-call with the ``mode`` parameter.
    """

    def __init__(
        self,
        default_mode: TraceCheckMode = "structural",
        llm: "LLMGateway | None" = None,
        kb: "KnowledgeBase | None" = None,
        coverage_threshold: float = 0.3,
        n_topics: int = 8,
        llm_judge: bool = False,
    ) -> None:
        self._default_mode = default_mode
        self._llm = llm
        self._kb = kb
        self._coverage_threshold = coverage_threshold
        self._n_topics = n_topics
        self._llm_judge = llm_judge

    @classmethod
    def from_config(
        cls,
        config: "SdlicitConfig",
        llm: "LLMGateway | None" = None,
        kb: "KnowledgeBase | None" = None,
    ) -> "TraceService":
        """Create a TraceService from project configuration."""
        return cls(
            default_mode=config.trace_check_mode,
            llm=llm,
            kb=kb,
        )

    # ── Public API ───────────────────────────────────────────────────────────

    def analyse(
        self,
        workspace: Path,
        *,
        mode: TraceCheckMode | None = None,
        llm_judge: bool | None = None,
    ) -> TraceCoverage:
        """Run trace analysis on a workspace directory.

        Args:
            workspace: Path to workspace (contains srs/, adrs/, stories/, bdd/).
            mode: Override the configured mode. If None, uses default.
            llm_judge: Override the LLM-judge flag. If None, uses default.

        Returns:
            TraceCoverage with metrics appropriate for the requested mode.
        """
        effective_mode = mode or self._default_mode
        use_judge = llm_judge if llm_judge is not None else self._llm_judge
        result = TraceCoverage(mode=effective_mode)

        # Always run structural
        self._structural_analysis(workspace, result)

        # Semantic if mode >= semantic
        if effective_mode in ("semantic", "full"):
            self._semantic_analysis(workspace, result)
            self._gherkin_alignment_analysis(workspace, result)

        # LLM-judge semantic scoring (optional, uses cheap model)
        if use_judge and effective_mode in ("semantic", "full"):
            self._llm_judge_analysis(workspace, result)

        # Graph-based conflict detection if full
        if effective_mode == "full":
            self._graph_analysis(workspace, result)

        return result

    async def analyse_async(
        self,
        workspace: Path,
        *,
        mode: TraceCheckMode | None = None,
        llm_judge: bool | None = None,
    ) -> TraceCoverage:
        """Async version — needed for LLM-based conflict detection in 'full' mode."""
        effective_mode = mode or self._default_mode
        use_judge = llm_judge if llm_judge is not None else self._llm_judge
        result = TraceCoverage(mode=effective_mode)

        # Structural (sync)
        self._structural_analysis(workspace, result)

        # Semantic (sync)
        if effective_mode in ("semantic", "full"):
            self._semantic_analysis(workspace, result)
            self._gherkin_alignment_analysis(workspace, result)

        # LLM-judge semantic scoring (optional)
        if use_judge and effective_mode in ("semantic", "full"):
            self._llm_judge_analysis(workspace, result)

        # Graph + LLM conflict detection (async)
        if effective_mode == "full":
            await self._graph_analysis_async(workspace, result)

        return result

    def analyse_artifact(
        self,
        artifact_id: str,
        workspace: Path,
        *,
        mode: TraceCheckMode | None = None,
    ) -> ArtifactCoverage:
        """Analyse trace coverage for a single artifact.

        Returns per-artifact coverage data suitable for inline UI display.
        """
        full_result = self.analyse(workspace, mode=mode)
        for art in full_result.artifacts:
            if art.artifact_id == artifact_id:
                return art
        # Not found — return empty
        return ArtifactCoverage(artifact_id=artifact_id, artifact_type="unknown")

    def detect_implements(
        self,
        artifact_text: str,
        workspace: Path,
        *,
        threshold: float = 0.25,
    ) -> list[str]:
        """Detect which requirements an artifact semantically addresses.

        Compares the artifact text against all requirements in the SRS
        using TF-IDF + Jaccard similarity. Returns requirement IDs that
        score above the threshold. Falls back to Jaccard-only when
        scikit-learn is unavailable.

        Used to auto-populate ``implements:`` frontmatter when an ADR is saved.
        """
        reqs = self._extract_docs_requirements(workspace)
        if not reqs:
            return []

        # Build a pseudo-document for the artifact
        artifact_doc = _ArtefactDoc(artefact_id="_new", artefact_type="adr", text=artifact_text)

        # Phase 1: Explicit REQ-ID mentions in the text (always detected)
        import re as _re
        req_ids_in_text = set(_re.findall(r"REQ-(?:FUNC|NONFUNC)-\d+", artifact_text))
        known_req_ids = {r.artefact_id for r in reqs}
        explicit_matches = req_ids_in_text & known_req_ids

        # Phase 2: Similarity-based detection
        use_tfidf = True
        try:
            import sklearn  # noqa: F401
        except (ImportError, Exception):
            use_tfidf = False
            _log.info("scikit-learn unavailable — using Jaccard-only fallback for detect_implements")

        matched: list[tuple[str, float]] = []
        if use_tfidf:
            all_docs = reqs + [artifact_doc]
            tfidf_matrix, _ = self._compute_tfidf_matrix(all_docs)
            artifact_idx = len(all_docs) - 1
            for i, req in enumerate(reqs):
                tfidf_cos = self._cosine_similarity_sparse(tfidf_matrix, artifact_idx, i)
                jaccard = self._keyword_jaccard(artifact_doc, req)
                combined = 0.6 * tfidf_cos + 0.4 * jaccard
                if combined >= threshold:
                    matched.append((req.artefact_id, combined))
        else:
            # Jaccard-only fallback (no external deps needed)
            # Use a lower threshold since Jaccard alone is less discriminative
            jaccard_threshold = threshold * 0.7
            for req in reqs:
                jaccard = self._keyword_jaccard(artifact_doc, req)
                if jaccard >= jaccard_threshold:
                    matched.append((req.artefact_id, jaccard))

        # Merge explicit mentions with similarity matches
        matched.sort(key=lambda x: x[1], reverse=True)
        result_ids = list(explicit_matches)
        for req_id, _ in matched:
            if req_id not in result_ids:
                result_ids.append(req_id)

        return result_ids

    # ── Structural analysis ──────────────────────────────────────────────────

    def _structural_analysis(self, workspace: Path, result: TraceCoverage) -> None:
        """Regex-based ID cross-reference validation."""
        req_ids = set(self._parse_requirements(workspace))
        adrs = self._parse_adrs(workspace)
        persona_ids = set(self._parse_personas(workspace))
        stories = self._parse_stories(workspace)
        gherkin = self._parse_gherkin(workspace)

        known_ids = req_ids | set(adrs) | persona_ids | set(stories) | set(gherkin)
        result.known_ids = known_ids
        result.artifact_counts = {
            "requirements": len(req_ids),
            "adrs": len(adrs),
            "personas": len(persona_ids),
            "stories": len(stories),
            "gherkin": len(gherkin),
        }

        # Check ADR references
        for adr_id, refs in adrs.items():
            art_cov = ArtifactCoverage(artifact_id=adr_id, artifact_type="adr")
            for r in refs["req_refs"]:
                valid = r in req_ids
                self._record_link(result, art_cov, adr_id, r, "cites_req", valid)
            for a in refs["adr_refs"]:
                valid = a in adrs
                self._record_link(result, art_cov, adr_id, a, "cites_adr", valid)
            for k in refs["kb_refs"]:
                self._record_link(result, art_cov, adr_id, k, "cites_kb", True)
            result.artifacts.append(art_cov)

        # Check story references
        for story_id, refs in stories.items():
            art_cov = ArtifactCoverage(artifact_id=story_id, artifact_type="story")
            for p in refs["persona_refs"]:
                valid = p in persona_ids
                self._record_link(result, art_cov, story_id, p, "cites_persona", valid)
            for r in refs["req_refs"]:
                valid = r in req_ids
                self._record_link(result, art_cov, story_id, r, "cites_req", valid)
            result.artifacts.append(art_cov)

        # Check BDD references
        for bdd_id, refs in gherkin.items():
            art_cov = ArtifactCoverage(artifact_id=bdd_id, artifact_type="bdd")
            if refs["story_ref"]:
                valid = refs["story_ref"] in stories
                self._record_link(result, art_cov, bdd_id, refs["story_ref"], "cites_story", valid)
            if refs["adr_ref"]:
                valid = refs["adr_ref"] in adrs
                self._record_link(result, art_cov, bdd_id, refs["adr_ref"], "cites_adr", valid)
            result.artifacts.append(art_cov)

        # Compute structural coverage
        if result.total_links > 0:
            result.structural_coverage_pct = (result.valid_links / result.total_links) * 100.0

    def _record_link(
        self,
        result: TraceCoverage,
        art_cov: ArtifactCoverage,
        source: str,
        target: str,
        link_type: str,
        valid: bool,
    ) -> None:
        """Record a trace link in both the overall result and per-artifact coverage."""
        if not target:
            return
        result.total_links += 1
        art_cov.outgoing_links += 1
        if valid:
            result.valid_links += 1
            art_cov.valid_links += 1
        else:
            result.broken_links_count += 1
            art_cov.broken_links += 1
            result.broken_link_details.append(
                TraceLink(source_id=source, target_id=target, link_type=link_type, valid=False)
            )

    # ── Semantic analysis ────────────────────────────────────────────────────

    def _semantic_analysis(self, workspace: Path, result: TraceCoverage) -> None:
        """Content-based similarity: TF-IDF + Jaccard + NMF topics."""
        try:
            import sklearn  # noqa: F401
        except ImportError:
            _log.warning("scikit-learn not installed — skipping semantic analysis")
            return

        reqs = self._extract_docs_requirements(workspace)
        adrs = self._extract_docs_adrs(workspace)
        stories = self._extract_docs_stories(workspace)
        bdds = self._extract_docs_bdd(workspace)

        targets = adrs + stories + bdds
        if not reqs or not targets:
            result.semantic_coverage_pct = 0.0
            return

        all_docs = reqs + targets
        doc_index = {id(d): i for i, d in enumerate(all_docs)}

        # TF-IDF matrix
        tfidf_matrix, _ = self._compute_tfidf_matrix(all_docs)

        # NMF topic model
        W, actual_topics = self._compute_topic_vectors(all_docs)

        # Pairwise scores: each REQ vs each downstream target
        req_best: dict[str, float] = {}
        all_tfidf: list[float] = []
        all_jaccard: list[float] = []
        all_topic: list[float] = []

        for req in reqs:
            req_idx = doc_index[id(req)]
            best_combined = 0.0
            best_target = ""

            for target_doc in targets:
                tgt_idx = doc_index[id(target_doc)]
                tfidf_cos = self._cosine_similarity_sparse(tfidf_matrix, req_idx, tgt_idx)
                jaccard = self._keyword_jaccard(req, target_doc)
                topic_sim = self._topic_similarity(W, req_idx, tgt_idx) if W is not None else 0.0

                combined = 0.5 * tfidf_cos + 0.3 * jaccard + 0.2 * topic_sim
                all_tfidf.append(tfidf_cos)
                all_jaccard.append(jaccard)
                all_topic.append(topic_sim)

                if combined > best_combined:
                    best_combined = combined
                    best_target = target_doc.artefact_id

            req_best[req.artefact_id] = best_combined

            # Update artifact coverage with semantic score
            for art in result.artifacts:
                if art.artifact_id == req.artefact_id:
                    art.semantic_score = best_combined
                    if best_target:
                        art.covered_by.append(best_target)
                    break

        # Aggregates
        n_pairs = len(all_tfidf)
        if n_pairs > 0:
            result.mean_tfidf = sum(all_tfidf) / n_pairs
            result.mean_jaccard = sum(all_jaccard) / n_pairs
            result.mean_topic = sum(all_topic) / n_pairs
            result.mean_combined = (
                0.5 * result.mean_tfidf + 0.3 * result.mean_jaccard + 0.2 * result.mean_topic
            )

        result.per_requirement_scores = req_best
        covered = sum(1 for v in req_best.values() if v >= self._coverage_threshold)
        result.semantic_coverage_pct = (covered / len(req_best) * 100.0) if req_best else 0.0

    # ── LLM-judge semantic analysis ──────────────────────────────────────────

    def _llm_judge_analysis(self, workspace: Path, result: TraceCoverage) -> None:
        """LLM-as-judge semantic trace coverage.

        Uses a cheap model (e.g. GPT-4.1 nano) to judge whether each requirement
        is meaningfully addressed by at least one downstream artifact. This
        grounds the semantic coverage metric: TF-IDF/Jaccard can miss semantic
        equivalence (paraphrasing), while the LLM can recognize that an ADR about
        "session management" addresses REQ-AUTH-02 "user authentication state".

        Role: provides a more accurate semantic coverage score than lexical overlap
        alone, at modest cost (~0.01 USD per workspace evaluation).
        """
        try:
            import dspy
        except ImportError:
            _log.warning("dspy not installed — skipping LLM-judge analysis")
            return

        reqs = self._extract_docs_requirements(workspace)
        targets = (
            self._extract_docs_adrs(workspace)
            + self._extract_docs_stories(workspace)
            + self._extract_docs_bdd(workspace)
        )

        if not reqs or not targets:
            result.llm_judge_coverage_pct = 0.0
            return

        # Build downstream summary (truncated to fit context)
        downstream_texts = []
        for t in targets:
            snippet = t.text[:600]
            downstream_texts.append(f"[{t.artefact_type.upper()}] {t.artefact_id}: {snippet}")
        downstream_summary = "\n---\n".join(downstream_texts)
        # Cap total context to avoid excessive token usage
        if len(downstream_summary) > 12000:
            downstream_summary = downstream_summary[:12000] + "\n... (truncated)"

        # DSPy signature for the judge
        class _TraceJudge(dspy.Signature):
            """Judge whether a software requirement is meaningfully addressed
            by downstream architecture artifacts (ADRs, user stories, BDD scenarios).
            A requirement is 'covered' if at least one downstream artifact
            demonstrates awareness of and response to the requirement's intent,
            even if using different terminology."""

            requirement: str = dspy.InputField(
                desc="The requirement text (ID + description)"
            )
            downstream_artifacts: str = dspy.InputField(
                desc="Summaries of downstream artifacts (ADRs, stories, BDD)"
            )
            is_covered: bool = dspy.OutputField(
                desc="True if at least one downstream artifact addresses this requirement"
            )
            confidence: float = dspy.OutputField(
                desc="Confidence 0.0-1.0 in the coverage judgment"
            )

        judge = dspy.Predict(_TraceJudge)
        scores: dict[str, float] = {}

        for req in reqs:
            try:
                pred = judge(
                    requirement=f"{req.artefact_id}: {req.text[:500]}",
                    downstream_artifacts=downstream_summary,
                )
                score = float(pred.confidence) if pred.is_covered else 0.0
                scores[req.artefact_id] = score
            except Exception as exc:
                _log.debug("LLM judge failed for %s: %s", req.artefact_id, exc)
                scores[req.artefact_id] = 0.0

        result.llm_judge_scores = scores
        covered = sum(1 for v in scores.values() if v >= 0.5)
        result.llm_judge_coverage_pct = (covered / len(scores) * 100.0) if scores else 0.0

    # ── Gherkin-specific semantic alignment (LLM-as-judge) ─────────────────

    def _gherkin_alignment_analysis(self, workspace: Path, result: TraceCoverage) -> None:
        """LLM-as-judge: score each BDD scenario against its traced requirement.

        For each ``.feature`` file, resolves the traced requirement(s) via
        ``# req:`` header or ``# story:`` → two-hop, then asks a DSPy judge to
        score alignment on four dimensions:

        - concept_coverage: same domain concepts (entities, actions, states)?
        - behavioral_fidelity: steps correctly operationalise the requirement?
        - trace_correctness: trace links valid and coherent?
        - completeness: covers the full acceptance criteria?

        Final score = mean of the four sub-scores.  When no LLM is available,
        falls back to a simple structural heuristic.

        Populates:
          - ``result.per_scenario_alignment``: ``{BDD-NNN: score}``
          - ``result.gherkin_alignment_pct``: % of features ≥ threshold (0.5)
        """
        bdd_dir = self._resolve_bdd_dir(workspace)
        if bdd_dir is None:
            return

        req_docs = self._extract_docs_requirements(workspace)
        if not req_docs:
            return
        req_lookup: dict[str, _ArtefactDoc] = {d.artefact_id: d for d in req_docs}

        # Story lookup for two-hop resolution
        stories = self._parse_stories(workspace)

        # Build LLM judge if dspy available and LM configured
        judge = None
        try:
            import dspy

            if dspy.settings.lm is None:
                _log.debug("No DSPy LM configured — using structural fallback for gherkin alignment")
            else:
                class _GherkinJudge(dspy.Signature):
                    """Judge whether a BDD Gherkin scenario faithfully encodes the
                    behaviour of the requirement(s) it traces to.
                    Score each dimension 0.0–1.0."""

                    scenario: str = dspy.InputField(
                        desc="Gherkin feature text (Feature + Scenario + Given/When/Then)"
                    )
                    requirements: str = dspy.InputField(
                        desc="Traced SRS requirement(s) (ID + full text)"
                    )
                    concept_coverage: float = dspy.OutputField(
                        desc="0.0–1.0: steps address the same domain concepts"
                    )
                    behavioral_fidelity: float = dspy.OutputField(
                        desc="0.0–1.0: steps correctly operationalise required behaviour"
                    )
                    trace_correctness: float = dspy.OutputField(
                        desc="0.0–1.0: trace links are valid and coherent"
                    )
                    completeness: float = dspy.OutputField(
                        desc="0.0–1.0: scenario covers full acceptance criteria"
                    )
                    reasoning: str = dspy.OutputField(
                        desc="Brief explanation (2-3 sentences)"
                    )

                judge = dspy.Predict(_GherkinJudge)
        except (ImportError, AttributeError):
            pass

        alignment_threshold = 0.5
        scores: dict[str, float] = {}

        for feat_file in sorted(bdd_dir.glob("*.feature")):
            text = feat_file.read_text(encoding="utf-8")
            bdd_id = ""
            req_ids: list[str] = []
            story_ref = ""
            for line in text.splitlines()[:12]:
                line_s = line.strip()
                if line_s.startswith("# id:"):
                    m = RE_BDD.findall(line_s)
                    bdd_id = m[0] if m else ""
                elif line_s.startswith("# req:"):
                    req_ids = RE_REQ.findall(line_s)
                elif line_s.startswith("# story:"):
                    m = RE_STORY.findall(line_s)
                    story_ref = m[0] if m else ""

            bdd_id = bdd_id or f"BDD-{feat_file.stem}"

            # Two-hop fallback: BDD → story → req
            if not req_ids and story_ref and story_ref in stories:
                req_ids = stories[story_ref].get("req_refs", [])

            if not req_ids:
                continue

            # Collect requirement context
            req_context = ""
            for req_id in req_ids:
                req_doc = req_lookup.get(req_id)
                if req_doc:
                    req_context += f"\n--- {req_id} ---\n{req_doc.text[:800]}\n"

            if not req_context:
                continue

            # Score via LLM judge or structural fallback
            if judge is not None:
                try:
                    pred = judge(scenario=text[:2000], requirements=req_context)
                    sub = [
                        max(0.0, min(1.0, float(pred.concept_coverage))),
                        max(0.0, min(1.0, float(pred.behavioral_fidelity))),
                        max(0.0, min(1.0, float(pred.trace_correctness))),
                        max(0.0, min(1.0, float(pred.completeness))),
                    ]
                    scores[bdd_id] = sum(sub) / 4.0
                except Exception as exc:
                    _log.debug("Gherkin judge failed for %s: %s — using structural fallback", bdd_id, exc)
                    # Fall through to structural fallback
                    text_lower = text.lower()
                    s = 0.0
                    if any(r in text for r in req_ids):
                        s += 0.3
                    if all(kw in text_lower for kw in ("given", "when", "then")):
                        s += 0.4
                    if story_ref and story_ref in stories:
                        s += 0.3
                    scores[bdd_id] = min(s, 1.0)
            else:
                # Structural fallback: check for req refs + G/W/T presence
                text_lower = text.lower()
                s = 0.0
                if any(r in text for r in req_ids):
                    s += 0.3
                if all(kw in text_lower for kw in ("given", "when", "then")):
                    s += 0.4
                if story_ref and story_ref in stories:
                    s += 0.3
                scores[bdd_id] = min(s, 1.0)

        result.per_scenario_alignment = scores
        if scores:
            covered = sum(1 for v in scores.values() if v >= alignment_threshold)
            result.gherkin_alignment_pct = covered / len(scores) * 100.0
        else:
            result.gherkin_alignment_pct = None

    # ── Graph-based analysis (sync — deterministic only) ─────────────────────

    def _graph_analysis(self, workspace: Path, result: TraceCoverage) -> None:
        """Deterministic graph checks: broken links, stale refs, orphans."""
        from sdlicit.expansion_stage.traceability.trace_dag import TraceGraph

        project_dir = workspace
        if self._kb is not None:
            graph = TraceGraph.from_project_and_kb(project_dir, kb=self._kb)
        else:
            graph = TraceGraph.from_project(project_dir)

        # Check for broken edges in the graph
        for edge in graph.edges:
            if edge.target not in graph.nodes:
                result.graph_issues.append({
                    "severity": "error",
                    "message": f"Broken graph link: {edge.source} → {edge.target} ({edge.edge_type})",
                    "source_id": edge.source,
                    "target_id": edge.target,
                })

        # Check for references to superseded artifacts
        for edge in graph.edges:
            target_node = graph.nodes.get(edge.target)
            if target_node and target_node.status == "superseded":
                result.graph_issues.append({
                    "severity": "warning",
                    "message": f"{edge.source} references superseded artifact {edge.target}",
                    "source_id": edge.source,
                    "target_id": edge.target,
                })

        result.has_conflicts = any(
            i["severity"] == "error" for i in result.graph_issues
        )

    async def _graph_analysis_async(self, workspace: Path, result: TraceCoverage) -> None:
        """Graph analysis with optional LLM-based conflict detection."""
        # Run deterministic checks first
        self._graph_analysis(workspace, result)

        # If LLM is available, run semantic conflict detection
        if self._llm is not None:
            await self._llm_conflict_check(workspace, result)

    async def _llm_conflict_check(self, workspace: Path, result: TraceCoverage) -> None:
        """LLM-based semantic conflict detection between linked artifacts."""
        from sdlicit.agents.llm.dspy_modules import ConflictDetection
        from sdlicit.expansion_stage.traceability.trace_dag import TraceGraph

        project_dir = workspace
        if self._kb is not None:
            graph = TraceGraph.from_project_and_kb(project_dir, kb=self._kb)
        else:
            graph = TraceGraph.from_project(project_dir)

        if not graph.nodes:
            return

        # For each node with edges, gather related content
        artifacts_dir = project_dir / ".sdlicit" / "artifacts"
        if not artifacts_dir.exists():
            return

        # Check a sample of connected pairs for conflicts
        checked = set()
        for edge in graph.edges[:20]:  # Limit to avoid excessive LLM calls
            pair = (edge.source, edge.target)
            if pair in checked:
                continue
            checked.add(pair)

            source_node = graph.nodes.get(edge.source)
            target_node = graph.nodes.get(edge.target)
            if not source_node or not target_node:
                continue

            try:
                source_text = Path(source_node.file_path).read_text(encoding="utf-8")[:2000]
                target_text = Path(target_node.file_path).read_text(encoding="utf-8")[:2000]
            except (OSError, FileNotFoundError):
                continue

            try:
                prediction = self._llm.call(
                    ConflictDetection,
                    new_artifact=source_text,
                    new_artifact_id=edge.source,
                    related_artifacts=f"--- [{target_node.artifact_type}] {edge.target} ---\n{target_text}",
                    graph_context=f"{edge.source} -[{edge.edge_type}]-> {edge.target}",
                )
                output = prediction.result
                if output.has_conflicts:
                    for issue in output.issues:
                        result.graph_issues.append({
                            "severity": issue.severity,
                            "message": issue.message,
                            "source_id": issue.source_id,
                            "target_id": issue.target_id,
                        })
                    result.has_conflicts = True
                if output.coverage_assessment:
                    result.conflict_assessment = output.coverage_assessment
            except Exception as exc:
                _log.debug("LLM conflict check failed for %s→%s: %s", edge.source, edge.target, exc)

    # ── Workspace parsers (structural) ───────────────────────────────────────

    @staticmethod
    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

    @staticmethod
    def _resolve_srs(workspace: Path) -> Path:
        """Canonical: .sdlicit/artifacts/srs.md, fallback: srs/srs.md."""
        canonical = workspace / ".sdlicit" / "artifacts" / "srs.md"
        if canonical.exists():
            return canonical
        return workspace / "srs" / "srs.md"

    @staticmethod
    def _resolve_adr_dir(workspace: Path) -> Path | None:
        """Canonical: .sdlicit/artifacts/adr/, fallback: adrs/, .sdlc/adr/."""
        for candidate in [
            workspace / ".sdlicit" / "artifacts" / "adr",
            workspace / "adrs",
            workspace / ".sdlc" / "adr",
        ]:
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _resolve_stories(workspace: Path) -> tuple[Path | None, Path | None]:
        """Return (md_path, json_path) — JSON preferred for structural parsing."""
        candidates_md = [
            workspace / ".sdlicit" / "artifacts" / "stories.md",
            workspace / "stories" / "stories.md",
        ]
        candidates_json = [
            workspace / ".sdlicit" / "artifacts" / "stories.json",
            workspace / "stories" / "stories.json",
        ]
        md = next((p for p in candidates_md if p.exists()), None)
        js = next((p for p in candidates_json if p.exists()), None)
        return md, js

    @staticmethod
    def _resolve_personas(workspace: Path) -> Path | None:
        """Canonical: .sdlicit/artifacts/personas.json, fallback: personas/personas.json."""
        for candidate in [
            workspace / ".sdlicit" / "artifacts" / "personas.json",
            workspace / "personas" / "personas.json",
        ]:
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _resolve_bdd_dir(workspace: Path) -> Path | None:
        """Canonical: .sdlicit/artifacts/bdd/, fallback: bdd/."""
        for candidate in [
            workspace / ".sdlicit" / "artifacts" / "bdd",
            workspace / "bdd",
        ]:
            if candidate.exists():
                return candidate
        return None

    def _parse_requirements(self, workspace: Path) -> list[str]:
        return RE_REQ.findall(self._read(self._resolve_srs(workspace)))

    def _parse_adrs(self, workspace: Path) -> dict[str, dict]:
        adr_dir = self._resolve_adr_dir(workspace)
        if adr_dir is None:
            return {}
        adrs: dict[str, dict] = {}
        for f in sorted(adr_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            ids = RE_ADR.findall(text)
            adr_id = ids[0] if ids else f"ADR-{f.stem}"
            adrs[adr_id] = {
                "req_refs": list(set(RE_REQ.findall(text))),
                "adr_refs": list(set(RE_ADR.findall(text)) - {adr_id}),
                "kb_refs": list(set(RE_KB.findall(text))),
            }
        return adrs

    def _parse_personas(self, workspace: Path) -> list[str]:
        f = self._resolve_personas(workspace)
        if f is None:
            return []
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, TypeError):
            return []
        items = data if isinstance(data, list) else data.get("personas", [])
        return [p.get("persona_id", "") for p in items if p.get("persona_id")]

    def _parse_stories(self, workspace: Path) -> dict[str, dict]:
        md, js = self._resolve_stories(workspace)
        # Prefer JSON (structured, no parsing ambiguity)
        if js is not None:
            try:
                data = json.loads(js.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, TypeError):
                pass
            else:
                items = data if isinstance(data, list) else data.get("stories", [])
                return {
                    s["story_id"]: {
                        "persona_refs": [s.get("persona_id", "")] if s.get("persona_id") else [],
                        "req_refs": s.get("requirement_ids", []),
                    }
                    for s in items
                    if s.get("story_id")
                }
        # Fallback to markdown (block-based parsing)
        if md is not None:
            out: dict[str, dict] = {}
            text = md.read_text(encoding="utf-8")
            lines = text.splitlines()
            current_id: str | None = None
            current_persona_refs: list[str] = []
            current_req_refs: list[str] = []

            for line in lines:
                ids = RE_STORY.findall(line)
                if ids:
                    # Flush previous story block
                    if current_id:
                        out[current_id] = {
                            "persona_refs": current_persona_refs,
                            "req_refs": current_req_refs,
                        }
                    current_id = ids[0]
                    # Also capture any refs on the same header line
                    current_persona_refs = RE_PERSONA.findall(line)
                    current_req_refs = RE_REQ.findall(line)
                elif current_id:
                    # Accumulate refs from continuation lines
                    current_persona_refs.extend(RE_PERSONA.findall(line))
                    current_req_refs.extend(RE_REQ.findall(line))

            # Flush last story block
            if current_id:
                out[current_id] = {
                    "persona_refs": current_persona_refs,
                    "req_refs": current_req_refs,
                }
            return out
        return {}

    def _parse_gherkin(self, workspace: Path) -> dict[str, dict]:
        bdd_dir = self._resolve_bdd_dir(workspace)
        if bdd_dir is None:
            return {}
        out: dict[str, dict] = {}
        for f in sorted(bdd_dir.glob("*.feature")):
            text = f.read_text(encoding="utf-8")
            bdd_id = story_ref = adr_ref = ""
            for line in text.splitlines()[:10]:
                line_s = line.strip()
                if line_s.startswith("# id:"):
                    m = RE_BDD.findall(line_s)
                    bdd_id = m[0] if m else ""
                elif line_s.startswith("# story:"):
                    m = RE_STORY.findall(line_s)
                    story_ref = m[0] if m else ""
                elif line_s.startswith("# adr:"):
                    m = RE_ADR.findall(line_s)
                    adr_ref = m[0] if m else ""
            out[bdd_id or f"BDD-{f.stem}"] = {"story_ref": story_ref, "adr_ref": adr_ref}
        return out

    # ── Document extractors (semantic) ───────────────────────────────────────

    def _extract_docs_requirements(self, workspace: Path) -> list[_ArtefactDoc]:
        """Extract individual requirements as documents for semantic analysis."""
        srs = self._resolve_srs(workspace)
        if not srs.exists():
            return []
        text = srs.read_text(encoding="utf-8")
        docs: list[_ArtefactDoc] = []

        # Legacy header format: ## REQ-AUTH-01 ...
        re_header_block = re.compile(
            r"^#{1,3}\s*REQ-[A-Z]+-\d+.*?(?=^#{1,3}\s|\Z)", re.MULTILINE | re.DOTALL
        )
        re_header_id = re.compile(r"^#{1,3}\s*(REQ-[A-Z]+-\d+)", re.MULTILINE)

        for m in re_header_block.finditer(text):
            block = m.group(0)
            header_m = re_header_id.match(block)
            if header_m:
                docs.append(_ArtefactDoc(
                    artefact_id=header_m.group(1),
                    artefact_type="requirement",
                    text=block,
                ))

        # Bullet format fallback: - [REQ-SYS-10] description
        if not docs:
            re_bullet = re.compile(r"^\s*[-*]\s*\[?(REQ-[A-Z]+-\d+)\]?\s*(.*)", re.MULTILINE)
            for m in re_bullet.finditer(text):
                req_id = m.group(1)
                req_text = m.group(2).strip()
                if req_text:
                    docs.append(_ArtefactDoc(
                        artefact_id=req_id,
                        artefact_type="requirement",
                        text=req_text,
                    ))

        return docs

    def _extract_docs_adrs(self, workspace: Path) -> list[_ArtefactDoc]:
        """Extract ADR documents for semantic analysis."""
        adr_dir = self._resolve_adr_dir(workspace)
        if adr_dir is None:
            return []
        docs: list[_ArtefactDoc] = []
        for f in sorted(adr_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            ids = RE_ADR.findall(text)
            adr_id = ids[0] if ids else f"ADR-{f.stem}"
            docs.append(_ArtefactDoc(artefact_id=adr_id, artefact_type="adr", text=text))
        return docs

    def _extract_docs_stories(self, workspace: Path) -> list[_ArtefactDoc]:
        """Extract user stories as documents for semantic analysis."""
        md, _ = self._resolve_stories(workspace)
        if md is None:
            return []
        text = md.read_text(encoding="utf-8")
        docs: list[_ArtefactDoc] = []
        current_id = ""
        current_lines: list[str] = []
        for line in text.splitlines():
            ids = RE_STORY.findall(line)
            if ids:
                if current_id and current_lines:
                    docs.append(_ArtefactDoc(
                        artefact_id=current_id,
                        artefact_type="story",
                        text="\n".join(current_lines),
                    ))
                current_id = ids[0]
                current_lines = [line]
            elif current_id:
                current_lines.append(line)
        if current_id and current_lines:
            docs.append(_ArtefactDoc(
                artefact_id=current_id,
                artefact_type="story",
                text="\n".join(current_lines),
            ))
        return docs

    def _extract_docs_bdd(self, workspace: Path) -> list[_ArtefactDoc]:
        """Extract BDD feature files as documents for semantic analysis."""
        bdd_dir = self._resolve_bdd_dir(workspace)
        if bdd_dir is None:
            return []
        docs: list[_ArtefactDoc] = []
        for f in sorted(bdd_dir.glob("*.feature")):
            text = f.read_text(encoding="utf-8")
            ids = RE_BDD.findall(text)
            bdd_id = ids[0] if ids else f"BDD-{f.stem}"
            docs.append(_ArtefactDoc(artefact_id=bdd_id, artefact_type="bdd", text=text))
        return docs

    # ── Similarity computations ──────────────────────────────────────────────

    @staticmethod
    def _keyword_jaccard(a: _ArtefactDoc, b: _ArtefactDoc) -> float:
        """Stopword-filtered Jaccard similarity."""
        set_a = set(a.tokens)
        set_b = set(b.tokens)
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)

    @staticmethod
    def _compute_tfidf_matrix(docs: Sequence[_ArtefactDoc]) -> tuple[Any, Any]:
        """Build TF-IDF matrix over all artifact texts."""
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectorizer = TfidfVectorizer(
            tokenizer=_tokenize,
            token_pattern=None,
            max_features=5000,
            sublinear_tf=True,
        )
        texts = [d.text for d in docs]
        matrix = vectorizer.fit_transform(texts)
        return matrix, vectorizer

    @staticmethod
    def _compute_topic_vectors(docs: Sequence[_ArtefactDoc], n_topics: int = 8) -> tuple[Any, int]:
        """Fit NMF topic model, return topic-distribution matrix."""
        from sklearn.decomposition import NMF
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectorizer = TfidfVectorizer(
            tokenizer=_tokenize,
            token_pattern=None,
            max_features=3000,
            sublinear_tf=True,
        )
        tfidf = vectorizer.fit_transform([d.text for d in docs])
        actual_topics = min(n_topics, tfidf.shape[0], tfidf.shape[1])
        if actual_topics < 2:
            return None, 0
        nmf = NMF(n_components=actual_topics, random_state=42, max_iter=300)
        W = nmf.fit_transform(tfidf)
        return W, actual_topics

    @staticmethod
    def _cosine_similarity_sparse(matrix: Any, i: int, j: int) -> float:
        """Cosine similarity between rows i and j of a sparse matrix."""
        from sklearn.metrics.pairwise import cosine_similarity
        return float(cosine_similarity(matrix[i], matrix[j])[0, 0])

    @staticmethod
    def _topic_similarity(W: Any, i: int, j: int) -> float:
        """Cosine similarity between topic distributions."""
        import numpy as np
        a = W[i]
        b = W[j]
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom == 0:
            return 0.0
        return float(np.dot(a, b) / denom)

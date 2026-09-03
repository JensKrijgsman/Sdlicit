"""Tests for benchmark/scoring.py — the decision level replay scoring tool.

benchmark/ is not part of the installed sdlicit package, so these tests
add it to sys.path directly rather than importing it as a package.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmark"))

from scoring import alternatives_overlap  # noqa: E402


def test_identical_lists_score_perfect():
    o = alternatives_overlap(["use pgvector"], ["use pgvector"])
    assert o.jaccard == 1.0
    assert o.max_ratio == 1.0
    assert o.matched_pairs == 1


def test_containment_counts_as_a_match():
    o = alternatives_overlap(["use pgvector database directly"], ["use pgvector database"])
    assert o.jaccard == 1.0
    assert o.matched_pairs == 1


def test_empty_both_sides_scores_perfect():
    o = alternatives_overlap([], [])
    assert o.jaccard == 1.0
    assert o.max_ratio == 1.0


def test_empty_one_side_scores_zero():
    o = alternatives_overlap(["something"], [])
    assert o.jaccard == 0.0
    assert o.generated_count == 1
    assert o.gold_count == 0


def test_ratio_is_order_symmetric():
    """SequenceMatcher.ratio() is not order symmetric on real prose — the
    scoring module must not leak that asymmetry into its own score."""
    a = "enable the dynamic creation of payload and namespacing based on configuration"
    b = (
        "global flat parameter namespace require all clients to manually prefix "
        "all keys and rely on string key conventions rather than endpoint mapping"
    )
    forward = alternatives_overlap([a], [b])
    backward = alternatives_overlap([b], [a])
    assert forward.max_ratio == backward.max_ratio


def test_paraphrased_prose_stays_below_threshold_like_the_paper():
    """Reproduces the thesis's ADR-0006 worked example (Appendix F): the
    two ground truth options and three generated alternatives describe
    the same tradeoff in different words, the closest pair is well below
    tau=0.6, and the thresholded jaccard collapses to zero even though
    the replay itself is a good one. This is the metric's documented
    blind spot, not a generator failure — the test pins the behavior so
    a future change to the similarity function does not silently start
    reporting nonzero overlap here."""
    gold = [
        "Enable the dynamic creation of payload and/or namespacing by default.",
        "Enable the dynamic creation of payload and/or namespacing based on configuration.",
    ]
    generated = [
        "Free-form payload assembly: directly merge arbitrary JSON objects from "
        "workflow/model/tool outputs into a final payload without schema-driven "
        "field mapping or namespacing, collisions resolved by last-write-wins.",
        "Global flat parameter namespace: require all clients to manually prefix "
        "all keys (e.g. tool, workflow) and rely on string-key conventions rather "
        "than endpoint-contract-driven mapping.",
        "No deterministic mapping layer: pass raw model/tool arguments to REST "
        "calls and only validate server responses, rely on server-side validation "
        "and errors for correction.",
    ]
    o = alternatives_overlap(generated, gold)
    assert o.jaccard == 0.0
    assert 0.2 < o.max_ratio < 0.35


def test_tau_is_configurable():
    a = ["roughly the same idea worded differently here"]
    b = ["roughly the same idea, worded a little differently"]
    strict = alternatives_overlap(a, b, tau=0.95)
    loose = alternatives_overlap(a, b, tau=0.5)
    assert strict.jaccard <= loose.jaccard

"""Decision level scoring for comparing a generated ADR against a gold ADR.

Two signals, deliberately kept separate rather than folded into one score:

alternatives_overlap
    A surface vocabulary indicator only. Character level similarity
    (Ratcliff-Obershelp, difflib.SequenceMatcher.ratio) between the
    REJECTED options on each side, thresholded at tau. Two option lists
    can describe the same tradeoff in different words and score zero
    here, and the metric structurally cannot see the chosen decision at
    all if a gold source omits the chosen option from its own list of
    considered alternatives. Report it, do not treat it as the headline
    score.

judge_decision_agreement
    An LLM judge comparing the CHOSEN decision text on each side, not the
    rejected options. This is the substantive signal: does the generated
    decision reach the same architectural conclusion as the real one, and
    if not, is the divergence still technically coherent.

This split follows the same methodology the thesis validation notebooks
use (NB07/NB08), because a single blended score hides exactly the failure
mode that matters: two decisions can agree in substance while sharing no
rejected option vocabulary, and a single number would call that a miss.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

import dspy
from pydantic import BaseModel, Field

DEFAULT_TAU = 0.6


@dataclass(frozen=True)
class AlternativesOverlap:
    """Pairwise character similarity between two sets of option strings."""

    jaccard: float
    max_ratio: float
    matched_pairs: int
    generated_count: int
    gold_count: int
    tau: float

    @property
    def caveat(self) -> str:
        return (
            "Surface vocabulary indicator only. A low score does not mean "
            "the decision itself is wrong: it compares rejected option "
            "phrasing, not the chosen decision, and paraphrased architecture "
            "prose rarely clears a literal character similarity threshold."
        )


def _ratio(a: str, b: str) -> float:
    """Symmetric Ratcliff-Obershelp ratio.

    SequenceMatcher.ratio() is not order symmetric — its matching-block
    search is a greedy heuristic over one string against the other, and
    swapping which string is "a" can shift the ratio by several hundredths
    on real prose (verified empirically on the thesis's own ADR-0006
    worked example). Take the max of both orders so the score reflects
    the best alignment found rather than an arbitrary argument order.
    """
    al, bl = a.strip().lower(), b.strip().lower()
    return max(
        SequenceMatcher(None, al, bl).ratio(),
        SequenceMatcher(None, bl, al).ratio(),
    )


def alternatives_overlap(
    generated: list[str],
    gold: list[str],
    tau: float = DEFAULT_TAU,
) -> AlternativesOverlap:
    """Thresholded fuzzy Jaccard over two option lists, plus the raw max ratio.

    A pair counts as a match when one string contains the other, or their
    Ratcliff-Obershelp ratio reaches tau. jaccard is matched pairs over the
    union size. max_ratio is the single highest pairwise ratio found,
    reported uncapped by the threshold so a near miss is visible even when
    it does not clear tau.
    """
    if not generated and not gold:
        return AlternativesOverlap(1.0, 1.0, 0, 0, 0, tau)
    if not generated or not gold:
        return AlternativesOverlap(0.0, 0.0, 0, len(generated), len(gold), tau)

    best_ratio = 0.0
    matched_gold: set[int] = set()
    matched_gen: set[int] = set()
    for gi, g in enumerate(generated):
        for ki, k in enumerate(gold):
            gl, kl = g.strip().lower(), k.strip().lower()
            ratio = 1.0 if (gl in kl or kl in gl) and gl and kl else _ratio(g, k)
            best_ratio = max(best_ratio, ratio)
            if ratio >= tau:
                matched_gen.add(gi)
                matched_gold.add(ki)

    matched_pairs = min(len(matched_gen), len(matched_gold))
    union = len(generated) + len(gold) - matched_pairs
    jaccard = matched_pairs / union if union else 0.0
    return AlternativesOverlap(
        jaccard=round(jaccard, 3),
        max_ratio=round(best_ratio, 3),
        matched_pairs=matched_pairs,
        generated_count=len(generated),
        gold_count=len(gold),
        tau=tau,
    )


DecisionAgreement = Literal["matches", "different-justifiable", "different-incoherent"]


class DecisionAgreementOutput(BaseModel):
    agreement: DecisionAgreement = Field(
        description=(
            "'matches' if the generated decision reaches the same architectural "
            "conclusion as the gold decision, even in different words. "
            "'different-justifiable' if it reaches a different conclusion but "
            "the choice is still internally coherent and defensible given the "
            "topic. 'different-incoherent' if it is a different conclusion and "
            "not a reasonable choice (contradicts the stated context, ignores "
            "an obvious constraint, or is simply confused)."
        )
    )
    reasoning: str = Field(description="One or two sentences justifying the verdict")


class DecisionAgreementJudge(dspy.Signature):
    """Judge whether a generated architectural decision matches a real one.

    Compare the CHOSEN decisions only, not the rejected alternatives — two
    decisions can be phrased completely differently and still be the same
    architectural move (e.g. both isolate provider specific logic behind an
    adapter), and two decisions can share vocabulary while disagreeing on
    substance. Read for the actual technical conclusion.
    """

    topic: str = dspy.InputField(desc="The architectural decision topic/title")
    generated_decision: str = dspy.InputField(desc="Sdlicit's generated decision text")
    gold_decision: str = dspy.InputField(desc="The real, historical decision text")
    verdict: DecisionAgreementOutput = dspy.OutputField()


async def judge_decision_agreement(
    lm_predict,
    *,
    topic: str,
    generated_decision: str,
    gold_decision: str,
) -> DecisionAgreementOutput:
    """Run the decision level LLM judge.

    lm_predict is any callable with the signature of LLMGateway.predict —
    pass orch.llm.predict from a live Orchestrator, or wire your own DSPy
    predictor with the same call shape.
    """
    prediction = await lm_predict(
        DecisionAgreementJudge,
        topic=topic,
        generated_decision=generated_decision or "(no decision generated)",
        gold_decision=gold_decision or "(no gold decision recorded)",
    )
    return prediction.verdict

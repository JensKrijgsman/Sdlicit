---
id: ADR-0003
status: Proposed
implements: [REQ-TIME-04, REQ-SEC-02, REQ-SEC-03, REQ-CTRL-01, REQ-CTRL-02]
tested_by: []
---

# ADR-0003: Use GPT-4o-mini LLM classifier for project attribution of ingested activities

## Context
The system ingests Outlook events and GitHub activity items for a selected date range and configured funder reporting periods, then computes suggested billable hours per project/sub-case. Manual mapping of each item is time-consuming; however, the solution must:
- allow configuration-driven mapping using project context keywords/sub-case names and GitHub repo linkage rules,
- provide reviewer-visible mapping confidence/rationale and low-confidence flags,
- represent gaps/unmapped candidates,
- enforce researcher-only visibility and an explicit ready-for-export gate,
- support RBAC, minimum-data processing/storage, retention limits, and summary attribution traceability,
- generate non-technical explanations, and
- meet pilot performance and accuracy targets under sparse/inconsistent GitHub signals.
Prior ADRs: ADR-0001 and ADR-0002 describe foundational system concerns; this ADR supersedes none explicitly.

## Decision
Adopt an LLM-based classifier using GPT-4o-mini as a mapping engine to attribute each candidate Outlook/GitHub item to a configured project/sub-case. The classifier will operate within the existing rules/heuristics framework:
1) Candidate preparation: For each ingested item, build a compact, non-sensitive context payload (e.g., Outlook subject + organizer/attendee role hints + time; GitHub item type + repo name + title/PR summary + author + timestamps) and include the configured project/sub-case keywords/repo linkage rules.
2) Classification output: The classifier returns (a) predicted project/sub-case (or “unmapped”), (b) confidence score, (c) brief, human-readable rationale suitable for researcher review, and (d) optional supporting features (e.g., matched keyword/repo/topic) used to justify the suggestion.
3) Coverage and low-confidence handling: Compute an explicit coverage metric (fraction of candidates assigned vs. total). Mark mappings with confidence below a configured threshold as “low-confidence” and require researcher review (still producing an explicit rationale).
4) Gating and edit workflow: The draft generation uses system suggestions from the classifier; researchers can edit hours, add notes/deviations, and then mark the draft ready for export. Export generation occurs only after explicit readiness.
5) Explainability constraints: Explanations must be non-technical and must avoid surveillance/productivity/scoring language; language focuses on “why this item seems relevant” rather than employee monitoring.
6) Data minimization and audit: Store only summary attribution trace information needed for admin review and user-visible mapping rationale; do not store raw source content beyond what is necessary for transparency and auditing. Retain ingested and derived attribution data according to configurable retention bounds.
7) Reliability/consistency: Use deterministic post-processing rules to reconcile classifier output with configured repo-keyword linkage rules and researcher overrides; for sparse/inconsistent GitHub signals, the system may increase the unmapped rate rather than forcing risky attribution, but must still provide coverage and low-confidence flags.
8) Performance controls: Apply batching/caching and strict input truncation to ensure draft generation completes within the pilot time budget; optionally timebox LLM calls and fall back to non-LLM heuristics for late/failed classifications while preserving confidence/coverage reporting.

## Alternatives Considered
- Use only deterministic keyword/repo heuristics (no LLM).
- Use a fine-tuned model trained on historical mappings instead of GPT-4o-mini.
- Use GPT-4o-mini only as a secondary re-ranker after heuristic pre-filtering (LLM never sees items that do not match obvious keyword/repo rules).
- Use a rules-plus-LLM approach where the LLM produces only an explanation and a separate classifier/score module does the attribution decision.
- Vendor-specific embeddings similarity search (vector DB) instead of an LLM classifier.

## Consequences
Positive:
- Improved mapping quality for ambiguous or variably worded Outlook/GitHub signals versus pure heuristics.
- Built-in confidence/rationale enables researcher review, low-confidence marking, and transparency.
- Supports fuzzy/disambiguation heuristics required to reach accuracy targets.
- Facilitates human-readable, non-technical explanations.
- Explicit coverage metric and “unmapped” representation improve administrative oversight.

Risks/Tradeoffs:
- LLM outputs can vary; requires thresholding, post-processing, and careful prompt/output validation.
- Performance/cost: LLM calls for thousands of GitHub items must be batched/timeboxed/cached to meet the 60-minute draft-generation window.
- Data privacy: prompts and stored rationales must follow the minimum-data/storage requirements; avoid storing raw content unnecessarily.
- Potential over-attribution: must rely on confidence thresholds and allow unmapped/gaps to preserve accuracy.

Mitigations:
- Constrain model input to minimal attributes required for attribution.
- Validate structured outputs (JSON schema) and fallback to unmapped/heuristics on errors.
- Track coverage and low-confidence metrics; tune thresholds against the test dataset with ground truth to satisfy accuracy requirements.

"""Expansion stage — multi-agent review and enhancement.

After the ADR is fully composed, the expansion stage orchestrates:
  - ADR_Agent   → full-sweep coherence / completeness review
  - Requirement_Agent → RAG-backed check against ISO standards, patterns, literature
  - ToM_Agent   → theory-of-mind assessment of user intent and satisfaction

The ToM agent gets the final say — it synthesises all agent outputs
and the user's interaction history to recommend accept / revise / reject.

Future: SRS and SOW generation also live here (TBD — separate modules).
"""

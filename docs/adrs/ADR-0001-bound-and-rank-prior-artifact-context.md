---
id: ADR-0001
status: accepted
date: 2026-09-02
implements: []
tested_by: []
---

# ADR-0001: Bound and rank prior ADR context instead of unbounded oldest first concatenation

## Context and Problem Statement
`ADRAgent.suggest_directions` and `ADRAgent.full_review` need prior ADRs as context so new suggestions and reviews are coherent with decisions already made. The existing implementation read every ADR file on disk on every call and passed a fixed slice (the first five or first ten, sorted by filename) as raw markdown into the DSPy signature. Filenames sort oldest first, so once a project grew past the slice size, the newest and most relevant ADRs were the ones silently dropped, while the oldest ones were kept regardless of relevance. `ADRAgent.review_step` additionally accepted a `prior_adrs` parameter it never read, so the disk read happened on every single field review for no benefit at all.

Separately, every generated ADR is already chunked and stored in the LightRAG knowledge base under the `artifacts/` namespace, and `ADRAgent` already queries that store for grounding. The same content was therefore present twice: once through bounded, probe gated retrieval, once through unbounded raw concatenation, with no relationship between the two paths.

Two pieces already existed that a proper fix could build on: `summarise_adr()`, written specifically to compact an ADR for context window use but never called from the generation pipeline, and the traceability layer's `implements` frontmatter plus similarity scoring, which already computes which artifacts relate to which requirements.

Separately, `ToMAgent.compact_session()` summarises the Theory of Mind interaction log once it crosses `model_context_window * compact_threshold_pct`, but that budget concept had no connection to artifact context at all.

## Considered Options
* Add a single ranking and token budgeting module shared by every call site that needs prior ADR context
* Leave the fixed slice in place and simply raise the count
* Always query the knowledge base for prior context instead of passing any ADRs directly
* Let an LLM call choose which prior ADRs to include, per generation

## Decision Outcome
Chosen option: "Add a single ranking and token budgeting module shared by every call site that needs prior ADR context", because it reuses infrastructure that already existed (`summarise_adr`, the requirement `implements` links) rather than adding a new mechanism, and it keeps the fix in one place instead of three separate call sites.

`helpers/context_budget.py` ranks candidates by requirement overlap with the topic first, word overlap second, and recency (the numeric id, a reliable creation order signal for both on disk and in memory ADRs) as the final tiebreaker. Full text is kept for as many top ranked candidates as fit a token budget; the rest are summarised with `summarise_adr()` while summaries still fit; anything past the budget is dropped, and the caller is told how many were dropped rather than truncating silently.

The token budget reuses `config.model_context_window` and `config.compact_threshold_pct`, the same two fields ToM compaction already reads, so both subsystems answer to one shared, user configurable notion of how much context is too much, instead of two unrelated ones. The VS Code extension's session manager previously used its own hardcoded 20000 token constant for the equivalent warning; it now reads the same two config fields the CLI already did.

`ADRAgent.review_step` had its unused `prior_adrs` parameter removed end to end, since it was never part of the client facing request contract, only an internal, wasted disk read.

### Consequences
* Good, because context now grows with relevance to the topic at hand instead of growing unboundedly with project size, and the newest decisions are no longer the ones silently excluded.
* Good, because ranking and budgeting logic lives in one place and is reused by the agent methods, the MCP tool path, and the example replay pipeline, instead of three separate ad hoc slices.
* Good, because a dropped count is always available, so a caller can tell when context was left out instead of assuming everything relevant was included.
* Good, because the ToM and artifact context budgets are now the same two configuration fields, adjustable in one place.
* Bad, because ranking still relies on word overlap and explicit requirement ids rather than full semantic search; a topic phrased very differently from a related ADR's own wording can still be ranked lower than it should be.
* Bad, because raw concatenation and knowledge base retrieval remain two separate mechanisms; this decision bounds and ranks the first one, it does not merge the two.

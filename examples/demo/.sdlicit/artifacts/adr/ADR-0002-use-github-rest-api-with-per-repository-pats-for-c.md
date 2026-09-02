---
id: ADR-0002
status: Proposed
implements: [REQ-SEC-02, REQ-SEC-03, REQ-LG-01, REQ-ROB-01, REQ-MAINT-01]
tested_by: []
---

# ADR-0002: Use GitHub REST API with per-repository PATs for commit/push activity ingestion

## Context
The system must ingest GitHub activity for configured repositories, timebox activity to a selected date range, and compute suggested billable hours per project/sub-case based on reconciled GitHub events and Outlook events. It must also enforce role-based access control, minimize stored GitHub fields, support operational monitoring and error visibility, and provide configurable data retention.

GitHub ingestion is specifically required to include commit/push activity time signals when available, and to process sparse/inconsistent GitHub signals with explicit coverage metrics and low-confidence markings.

## Decision
Use GitHub REST API ingestion with a scoped, per-repository Personal Access Token (PAT) for retrieving commit and push-related activity for only the repositories configured in the pilot. Each configured repository will be associated with its own PAT, stored encrypted and associated to the owning pilot/integration configuration. Ingestion will query GitHub REST endpoints to collect timeboxed activity items (e.g., commits, compare/branch or push-derived activity where supported) and map those artifacts to the selected date range using available timestamps (commit timestamps, PR/issue timestamps, and push timestamps when returned by the chosen REST calls).

Implementation details (high level):
- Token scope: generate PATs with the minimum scopes required for public/private repository access (e.g., read-only capabilities as applicable) and restrict each PAT to a single target repository (where possible via GitHub token/resource scoping).
- Repository routing: ingestion jobs use the PAT mapped to the target repository; no global token is used for all repositories.
- REST client: implement a GitHub REST client module that handles pagination, rate limiting, and retries with backoff.
- Data minimization: persist only fields needed for attribution (timestamps, identifiers, actor linkage required for mapping rationale, and summary attribution trace), avoiding raw payload storage.
- Observability: record ingestion status per user/source and surface REST errors/actionable messages.
- Retention: apply configurable retention windows for ingested artifacts and derived attribution data.

This approach is selected to tightly scope access per repository while leveraging broadly supported REST endpoints and minimizing integration complexity for the pilot timeframe.

## Alternatives Considered
- Use a single GitHub PAT for all repositories (simpler credentials, but less isolation and higher blast radius).
- Use GitHub Apps with fine-grained permissions and installation per repository (more complex setup; may be heavier for pilot).
- Use GraphQL API for ingestion (potentially fewer round-trips but higher complexity and less straightforward mapping to specific push/commit timelines).
- Ingest only pull request and issue timestamps (easier but may under-capture push/commit activity and reduce attribution coverage).

## Consequences
Positive outcomes:
- Better security isolation: per-repo PATs reduce blast radius compared to a shared credential.
- Aligns with minimum-field processing and audit needs by enabling structured ingestion and selective persistence.
- Facilitates operational tooling: ingestion can be tracked per repository/token and per user/source.
- Improves compliance posture for RBAC and data retention by keeping integration access explicit and scoped.

Trade-offs / risks:
- Managing many per-repo PATs adds operational overhead (token lifecycle, rotation, and configuration).
- REST APIs may not expose push timestamps consistently across all repository visibility/configurations; ingestion must fall back to commit timestamps when push times are unavailable, and must explicitly report coverage.
- REST rate limits may require careful pagination, backoff, and batching to meet performance targets.

Mitigations:
- Build token rotation tooling and validation for per-repo tokens.
- Implement fallback ordering for timestamps and produce coverage metrics and low-confidence markers when push signals are missing.

---
id: ADR-0004
status: Proposed
implements: [REQ-CTRL-01, REQ-CTRL-02, REQ-SEC-01, REQ-SEC-02, REQ-SEC-03]
tested_by: []
---

# ADR-0004: Store time allocation drafts in PostgreSQL with per-user Row-Level Security

## Context
The pilot requires researchers to create and manage billable-hours drafts for selected date ranges and configured funder reporting periods, with system-suggested hours computed from reconciled Outlook and GitHub signals. Researchers must be able to review and edit mapped hours, visualize gaps/unmapped candidates, and then explicitly mark drafts as ready for export. The system must also enforce that draft attribution data is visible only to the owning researcher by default, while supporting RBAC at minimum roles (researcher, maintainer, coordinator). Additionally, data minimization and retention controls are required: store only necessary data for audit and user-visible mapping rationale, and provide configurable retention with an upper bound duration. Performance constraints include generating drafts for up to 200 Outlook events and up to 5,000 GitHub activity items within 60 minutes on pilot hardware.

## Decision
Implement persistence of time allocation draft data in PostgreSQL using row-level security (RLS) policies to ensure per-user isolation by default. Draft tables will store ownership metadata (owning_researcher_user_id) and export readiness state (export_ready_at / status). RBAC will be implemented in the application and enforced in SQL via RLS policy predicates that allow: (a) researchers to read/write only their own draft rows, (b) maintainers/coordinators to access only as permitted by configured pilot policy (e.g., read-only oversight or limited administrative actions). Export generation and “use for reporting” actions will be implemented as database/API operations that query only drafts marked ready for export, ensuring explicit researcher confirmation is required.

## Alternatives Considered
- Store drafts in a document store (e.g., DynamoDB) with partition keys per user and application-enforced filtering for access control
- Store drafts in PostgreSQL without RLS and rely solely on application-layer authorization checks
- Use PostgreSQL views with security-barrier rules instead of RLS policies
- Store drafts in an encrypted application-managed format and decrypt only for authorized users (authorization still enforced by app)

## Consequences
Positive consequences:
- Stronger default confidentiality: RLS prevents accidental cross-user exposure even if application queries are imperfect.
- Clean separation of researcher vs. administrative access aligned with REQ-CTRL-01 and REQ-SEC-01.
- Draft lifecycle safety: export queries/actions can be constrained to export-ready drafts (REQ-CTRL-02).
- Data minimization can be achieved via schema design (storing summary attribution and mapping rationale instead of raw source content) (REQ-SEC-02, REQ-LG-01).
- Retention can be enforced via scheduled cleanup based on configured retention bounds (REQ-SEC-03).

Trade-offs / risks:
- RLS introduces complexity in schema design, policy management, and testing; requires careful handling of the database session user context.
- Some administrative oversight use cases may require additional RLS policies or carefully designed roles/claims.
- Export pipelines must be designed to ensure “use for reporting” never reads non-ready drafts.

Operational impact:
- Need automated tests for RLS behavior and for export gating.
- Need monitoring for RLS-related query failures and policy regressions.
- Need scheduled retention cleanup jobs and migration/versioning for policy changes.

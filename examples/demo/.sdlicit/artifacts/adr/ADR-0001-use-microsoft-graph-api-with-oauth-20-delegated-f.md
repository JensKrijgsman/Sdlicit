---
id: ADR-0001
status: Proposed
implements: [REQ-ING-01, REQ-ING-02, REQ-CTRL-01, REQ-SEC-01, REQ-SEC-02]
tested_by: []
---

# ADR-0001: Use Microsoft Graph API with OAuth 2.0 delegated flow for Outlook calendar event ingestion

## Context
The system must ingest Outlook calendar meeting/events and extract attributes needed for attribution mapping (subject/title, organizer/attendees, start/end time) and then compute suggested billable hours per project/sub-case for selected date ranges. Pilot use requires that drafts and related ingestion traceability are visible only to the owning researcher by default, and that data processing uses the minimum set of fields required. The ingestion mechanism must support operational monitoring and error visibility for pilot setup.

## Decision
Adopt Microsoft Graph API for Outlook calendar event ingestion using an OAuth 2.0 delegated authorization flow (authorization code flow with PKCE). The system will request least-privilege delegated scopes to read the signed-in researcher’s calendar events within the user-selected date range, extract only attribution-relevant fields, and persist only derived attribution summaries and mapping rationale needed for transparency/audit. Draft generation and export readiness are controlled by the researcher, and access to drafts is enforced per researcher (RBAC/pilot policy).

## Alternatives Considered
- Use Microsoft Graph application permissions with client credentials (daemon/background service) to read all researchers’ calendars
- Use Exchange Web Services (EWS) or raw IMAP/Exchange connectivity to ingest calendar events
- Use manual calendar export ingestion (ICS files) uploaded by researchers
- Use Microsoft Power Automate connectors as an integration layer instead of direct Graph API ingestion

## Consequences
Positive:
- Enables per-researcher ingestion via delegated auth, aligning with default draft visibility for the owning researcher.
- Uses a modern, supported Microsoft integration surface (Graph) with consistent permissioning and auditing.
- Can request least-privilege delegated scopes and limit stored data to minimum fields and derived attribution summaries.
- Improves reliability and reduces integration complexity versus legacy protocols.

Trade-offs / considerations:
- Delegated flow requires interactive sign-in/consent per researcher (pilot onboarding overhead).
- Token lifecycle management (refresh, revocation, and handling consent changes) is required.
- Operational monitoring must surface Graph-specific errors (consent required, throttling, permission failures) in actionable form.

Implementation notes:
- Use Microsoft Graph endpoints for calendar events: /me/events (or calendarView with start/end) filtered to the selected date range.
- Use incremental fetch/caching strategies and retry with backoff to handle throttling.
- Map Graph event fields to attribution inputs (subject, start/end, organizer, attendees as needed).
- Store derived mapping summaries and user-visible rationale; do not store raw event content beyond what is required.
- Enforce retention limits via a configurable TTL for ingested artifacts and derived attribution data.

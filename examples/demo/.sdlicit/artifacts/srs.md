# Software Requirements Specification

This set of requirements specifies the software behavior for a pilot time-tracking assistant that drafts funder-compliant billable hour attributions per project/sub-case using researchers’ activity artifacts (Outlook/GitHub) and user-provided project context, with exports in funder-specific formats while keeping attribution output as a researcher-reviewed draft.

## Scope
The system shall be deployed for a small initial pilot group for a few months. It shall not provide always-on or multi-tenant capabilities, and it shall not automatically submit timesheets on behalf of researchers. It shall support ingestion of Outlook calendar/meeting data and GitHub activity relevant to work attribution, provide assistive attribution with transparency, allow researcher review and edits before marking/using final outputs, and produce funder-specific export files suitable for the reporting workflow.

## Functional Requirements

### REQ-TIME-01
**Statement:** The system shall allow a researcher to create and manage a billable-hours draft for a selected date range and for configured funder reporting periods.
**Rationale:** Researchers need an actionable draft aligned to reporting periods and controllable by them.
**Acceptance Criteria:** Given a configured funder and date range, when a researcher selects the date range, the system generates a draft allocation structure covering all configured projects/sub-cases for that range; the draft can be saved and re-opened with consistent values after refresh.

### REQ-TIME-02
**Statement:** For each configured project and sub-case, the system shall compute suggested billable hours based on reconciled Outlook events and GitHub activity within the selected date range.
**Rationale:** Core pilot value is reconciling actual activities into suggested funder-allocated time attribution.
**Acceptance Criteria:** For a sample dataset (at least 20 Outlook events and 50 GitHub activity items), the system produces suggested hours per project/sub-case; suggested totals per project/sub-case and their underlying contributing items are displayed and match the system’s attribution calculation output to within 1 minute granularity.

### REQ-TIME-03
**Statement:** The system shall support user input of project context keywords/sub-case names and use this configuration to map Outlook events and GitHub artifacts to projects/sub-cases.
**Rationale:** Project-to-activity disambiguation requires explicit context input.
**Acceptance Criteria:** When a researcher adds keywords for Project A/Sub-case 1 and saves, events labeled with those keywords (or matching linkage rules) are mapped to Project A/Sub-case 1 in the next draft generation; mappings can be reviewed and changed via the UI.

### REQ-TIME-04
**Statement:** The system shall allow a researcher to review the mapping of each contributing Outlook/GitHub item to a project/sub-case and show the confidence or rationale used for the mapping.
**Rationale:** Transparency reduces perceived surveillance and supports trust in attribution.
**Acceptance Criteria:** For at least 95% of mapped items in a test dataset, the UI displays (a) target project/sub-case, (b) a mapping explanation (e.g., keyword match or other rule outcome), and (c) a confidence indicator; unmapped items are explicitly indicated as unmapped.

### REQ-TIME-05
**Statement:** The system shall allow researchers to edit suggested hours per project/sub-case in the draft, including setting hours to a specific value and adding notes for deviations.
**Rationale:** Researchers must remain in control and correct attribution before any submission/export usage.
**Acceptance Criteria:** After draft generation, a researcher can change hours for at least 10 projects/sub-cases and persist changes; exported values reflect edits; notes are stored and associated with the modified project/sub-case.

### REQ-TIME-06
**Statement:** The system shall provide an explicit representation of gaps or unmapped time candidates (e.g., events or work items that could not be attributed to any project/sub-case).
**Rationale:** Gap visibility reduces back-and-forth and helps researchers address missing allocations early.
**Acceptance Criteria:** When test data includes items with no keyword/repo mapping, the draft shows a “unmapped/needs review” section listing those items and the system does not silently discard them.

### REQ-TIME-07
**Statement:** The system shall export the researcher-reviewed draft into funder-specific output formats configured for the pilot (including Excel template/structure).
**Rationale:** Funder reporting workflow requires compatible export structures.
**Acceptance Criteria:** For each of the configured pilot funders (minimum 2 in test), exporting produces a file that conforms to that funder’s required columns/row structure and totals; a validation script checks column presence and that project/sub-case hour totals match the draft within 1 minute.

### REQ-TIME-08
**Statement:** The system shall provide a configuration mechanism to define export mappings from internal project/sub-case identifiers to funder-specific row/field names.
**Rationale:** Export requirements vary by funder and must be adaptable without hard-coding.
**Acceptance Criteria:** After an administrator or maintainer updates the export mapping configuration for a funder, generating an export for the same draft produces changed field population according to the new mapping without code changes.

### REQ-ING-01
**Statement:** The system shall ingest Outlook calendar meeting/events and extract attributes needed for attribution mapping (e.g., meeting title/subject, organizer/attendees, start/end time).
**Rationale:** Outlook events are a primary activity source for time attribution.
**Acceptance Criteria:** For a controlled Outlook calendar import, the system creates attribution candidates with start/end times; meeting candidates appear in the draft mapping list and are correctly time-bounded within the selected date range.

### REQ-ING-02
**Statement:** The system shall ingest GitHub activity from the configured repositories and timebox activity items to the selected date range using available timestamps (e.g., commit dates, PR/issue timestamps, or push times when available).
**Rationale:** GitHub-based attribution must be time-aligned to reporting periods.
**Acceptance Criteria:** Given repositories with commits and PRs across multiple days, the system assigns those items to the correct date range buckets; at least 95% of items are assigned using the expected timestamp field per activity type.

### REQ-GH-01
**Statement:** The system shall support linking projects/sub-cases to GitHub repositories either by manual selection or by configured repository-keyword heuristics, with an override mechanism per researcher.
**Rationale:** Project-to-repo linking is an open question and must be flexible.
**Acceptance Criteria:** When manual linking is configured, only selected repos contribute to that project/sub-case; when keyword heuristics are enabled, repos matching keywords are proposed; researchers can override the resulting mapping and regenerate drafts.

### REQ-ALLOC-01
**Statement:** When system-suggested total hours for a project/sub-case are below the funder allocated target for the reporting period, the system shall represent the shortfall and allow the researcher to either (a) adjust hours manually or (b) select additional candidate items for attribution if available.
**Rationale:** Allocation gap handling must reduce estimation effort while maintaining user control.
**Acceptance Criteria:** Given a funder allocation target and test dataset that produces a shortfall, the UI shows the shortfall amount; the researcher can adjust hours to match the desired value and/or select additional candidates (from unmapped/low-confidence pools) and see the updated total.

## Non-Functional Requirements

### REQ-CTRL-01
**Statement:** The system shall enforce that attribution drafts are visible only to the owning researcher by default during the pilot.
**Rationale:** Non-surveillance intent and draft-only visibility are key pilot policy requirements.
**Acceptance Criteria:** In pilot test accounts, a researcher cannot access another researcher’s draft or underlying attribution mappings; attempts return an access-denied response without exposing sensitive content.

### REQ-CTRL-02
**Statement:** The system shall ensure that export generation and any “use for reporting” action occur only after the researcher has explicitly reviewed and marked the draft as ready for export.
**Rationale:** Automated submission is out of scope and researchers must approve content.
**Acceptance Criteria:** If a researcher attempts to export without marking “ready,” the system blocks export and displays an explicit message; after marking ready, export is allowed and includes the latest edits.

### REQ-SEC-01
**Statement:** The system shall implement role-based access control with at minimum roles for researcher, maintainer, and coordinator, and shall restrict access according to configured pilot policy.
**Rationale:** Access control decisions are a security/privacy requirement.
**Acceptance Criteria:** For each role, automated authorization tests verify permissions for view/edit/import/export and draft visibility; unauthorized actions fail with appropriate error codes and do not alter data.

### REQ-SEC-02
**Statement:** The system shall process Outlook and GitHub data using the minimum set of fields required for attribution, and shall store only data necessary for the chosen attribution transparency and audit (at least summary attribution and user-visible mapping rationale).
**Rationale:** Supports privacy assurance and minimizes stored personal/work data.
**Acceptance Criteria:** A data inventory report lists stored fields vs. received fields; in a configured test, stored record schema excludes at least one non-essential field from Outlook/GitHub payloads while still enabling display of attribution summary/rationale.

### REQ-SEC-03
**Statement:** The system shall provide configurable data retention settings for ingested artifacts and derived attribution data, including an upper bound duration.
**Rationale:** Pilot privacy reviewers require clarity on what is stored and for how long.
**Acceptance Criteria:** With retention set to X days in configuration, ingested artifacts older than X days are removed or anonymized per policy; a retention job/verification test confirms no records remain after X+1 days in a test environment.

### REQ-LG-01
**Statement:** The system shall maintain an attribution trace summary that links each attributed hour to contributing Outlook/GitHub items at a summary level sufficient for administrative review without exposing raw source content beyond what is necessary.
**Rationale:** Stakeholders require traceability while limiting surveillance risk.
**Acceptance Criteria:** For a given project/sub-case and date range, the system can produce a trace summary listing contributing item counts and identifiers (e.g., event title hash or item ID) and the mapping explanation used; raw payload bodies are not included in the trace summary view.

### REQ-ROB-01
**Statement:** The system shall handle sparse or inconsistent GitHub signals by producing attribution suggestions with an explicit coverage metric and by marking low-confidence attributions for user review.
**Rationale:** Open questions note uncertainty in GitHub signals; the solution must remain robust and transparent.
**Acceptance Criteria:** Given a dataset with low activity (e.g., fewer than 5 commits in the period), the system assigns low/uncertain confidence to suggestions below a defined threshold and flags them in the UI; at least 90% of suggestions in the low-activity dataset are flagged appropriately.

### REQ-UST-01
**Statement:** The researcher user interface shall enable a complete draft workflow (generate draft, review mappings, edit hours, and mark ready for export) in no more than 15 minutes for a typical pilot dataset of up to 10 projects/sub-cases.
**Rationale:** Reduces researcher time spent and directly supports success criteria.
**Acceptance Criteria:** Usability test with at least 5 pilot users on a standard dataset shows median completion time ≤15 minutes; no critical UI blockers occur.

### REQ-UST-02
**Statement:** The system shall clearly show, for each project/sub-case in the draft, the difference between system-suggested hours and researcher-edited hours.
**Rationale:** Makes gaps and adjustments explicit to reduce confusion and admin follow-up.
**Acceptance Criteria:** In a test where a researcher edits at least 5 project/sub-cases, the UI displays deltas (suggested vs edited) for all edited entries; exports reflect edited values and include researcher notes where provided.

### REQ-QUAL-01
**Statement:** For a test dataset with known ground-truth mapping, the system shall achieve attribution mapping accuracy of at least 85% for items that match defined keyword/repo linkage rules and at least 70% for items requiring fuzzy/disambiguation heuristics.
**Rationale:** Ensures suggested drafts are sufficiently reliable for pilot adoption.
**Acceptance Criteria:** Run attribution evaluation over at least 100 Outlook/GitHub items; compute mapping accuracy by item-to-project/sub-case assignment; report both rule-based and heuristic-based subsets meeting thresholds.

### REQ-PERF-01
**Statement:** Draft generation for a date range containing up to 200 Outlook events and up to 5,000 GitHub activity items shall complete within 60 minutes on the pilot hardware configuration.
**Rationale:** Pilot feasibility requires acceptable performance without 24/7 high-availability.
**Acceptance Criteria:** In load/performance tests on the specified pilot environment, 95th percentile draft generation duration is ≤60 minutes for the specified item counts.

### REQ-MAINT-01
**Statement:** The system shall provide operational tooling for pilot setup and ingestion monitoring, including the ability to view ingestion status per user and per source (Outlook/GitHub) and to see errors with actionable messages.
**Rationale:** Maintainers need manageable operations during a pilot period.
**Acceptance Criteria:** When an ingestion job fails (e.g., invalid credentials or API limit), the UI/logs show a clear error category and next step; maintainers can retry ingestion for the affected source without deleting user configurations.

### REQ-EXPL-01
**Statement:** The system shall provide an understandable, non-technical explanation for each attribution suggestion (at least for mappings shown as suggested/low-confidence), avoiding language that implies surveillance or productivity scoring.
**Rationale:** Non-surveillance intent and researcher satisfaction require appropriate UX wording.
**Acceptance Criteria:** In user testing with at least 3 researchers, participants rate explanations as clear (e.g., ≥4/5) and report that explanations do not imply monitoring beyond time attribution drafts.

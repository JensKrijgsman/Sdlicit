# User Stories

## STORY-P01-001
**Persona:** PERSONA-01
**Requirements:** REQ-TIME-01
**Statement:** As a Dr. Amina Patel, I want to create and manage a billable-hours draft for a selected date range aligned to configured funder reporting periods, so that my work is organized exactly for the period I’m reporting.

## STORY-P01-002
**Persona:** PERSONA-01
**Requirements:** REQ-TIME-02, REQ-TIME-03, REQ-ROB-01
**Statement:** As a Dr. Amina Patel, I want the system to compute suggested billable hours per configured project and sub-case from reconciled Outlook events and GitHub activity within my selected date range, including a coverage metric and low-confidence markings, so that I can trust what’s being suggested and quickly spot what needs review.

## STORY-P01-003
**Persona:** PERSONA-01
**Requirements:** REQ-TIME-04
**Statement:** As a Dr. Amina Patel, I want to review how each Outlook/GitHub item is mapped to a project/sub-case with clear confidence and rationale, so that I understand why time is attributed the way it is without technical jargon.

## STORY-P01-004
**Persona:** PERSONA-01
**Requirements:** REQ-TIME-05
**Statement:** As a Dr. Amina Patel, I want to edit the suggested hours per project/sub-case in my draft (set exact values and add deviation notes), so that the final numbers reflect my actual billable time.

## STORY-P01-005
**Persona:** PERSONA-01
**Requirements:** REQ-TIME-06, REQ-ROB-01
**Statement:** As a Dr. Amina Patel, I want the draft to explicitly show gaps or unmapped time candidates and highlight low-confidence attributions, so that I can resolve missing items instead of discovering them later.

## STORY-P01-006
**Persona:** PERSONA-01
**Requirements:** REQ-TIME-07, REQ-TIME-08, REQ-TIME-02, REQ-CTRL-02
**Statement:** As a Dr. Amina Patel, I want the system to export only a researcher-reviewed draft into the configured funder-specific Excel output format after I mark the draft as ready, so that the export matches my edits and is validated before I use it for reporting.

## STORY-P02-001
**Persona:** PERSONA-02
**Requirements:** REQ-TIME-01, REQ-SEC-01
**Statement:** As a Marco Rossi, I want to monitor ingestion status for each user/source and verify configuration impacts across Outlook and GitHub processing, so that pilot failures can be diagnosed without disrupting user-specific configurations.

## STORY-P02-002
**Persona:** PERSONA-02
**Requirements:** REQ-ROB-01, REQ-TIME-04, REQ-TIME-06
**Statement:** As a Marco Rossi, I want to troubleshoot and reason about sparse or inconsistent GitHub signals by using coverage and low-confidence/low-mapping indicators, so that I can improve mapping heuristics and guide researchers to the right review actions.

## STORY-P02-003
**Persona:** PERSONA-02
**Requirements:** REQ-TIME-08
**Statement:** As a Marco Rossi, I want a configuration mechanism to map internal project/sub-case identifiers to funder-specific row/field names, so that export formatting changes can be made via configuration rather than code changes.

## STORY-P02-004
**Persona:** PERSONA-02
**Requirements:** REQ-TIME-07, REQ-TIME-04, REQ-LG-01
**Statement:** As a Marco Rossi, I want the system’s trace summary to support pilot evaluation by linking attributed hours to contributing Outlook/GitHub items at a summary level, so that I can verify what was derived without relying on raw source content.

## STORY-P03-001
**Persona:** PERSONA-03
**Requirements:** REQ-CTRL-01, REQ-SEC-01
**Statement:** As a Sofia Nguyen, I want the system to enforce RBAC so researchers can only access their own attribution drafts and mappings by default, so that pilot privacy expectations are not violated.

## STORY-P03-002
**Persona:** PERSONA-03
**Requirements:** REQ-SEC-02, REQ-SEC-03, REQ-LG-01
**Statement:** As a Sofia Nguyen, I want the system to store only the minimum necessary fields with configurable retention (including an upper bound) and provide an attribution trace summary link, so that privacy and auditability are satisfied without over-collecting data.

## STORY-P03-003
**Persona:** PERSONA-03
**Requirements:** REQ-CTRL-02, REQ-TIME-07
**Statement:** As a Sofia Nguyen, I want export generation and any “use for reporting” action to be blocked until the researcher explicitly reviews and marks the draft as ready, so that exports cannot be produced prematurely.

## STORY-P03-004
**Persona:** PERSONA-03
**Requirements:** REQ-TIME-07
**Statement:** As a Sofia Nguyen, I want funder-specific exports to conform to the configured Excel template structure and required columns/totals validation, so that researchers can trust the output for reporting.

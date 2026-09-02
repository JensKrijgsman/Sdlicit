# Pilot time-tracking assistant for funded research projects (Outlook/GitHub-assisted time attribution)

## Problem Statement

Researchers on subsidized/funded projects must report billable hours per project/sub-case according to a fixed allocation from funders. Existing timesheets require researchers to manually enter hours to match the allocated target even when their actual work time differs, causing a “double tax” (researchers spend extra effort to estimate/fill gaps, and admin follows up for missing hours after submission). The current process does not reflect reality and results in dissatisfaction, back-and-forth, and additional administrative burden. The lab needs a pilot system that helps individuals produce a sensible draft of billable hours by reconciling their actual activities (e.g., Outlook meetings, GitHub activity, and other work artifacts) with required funder allocations, while keeping the output as a draft for the individual to review. The system must also support funder-specific export formats. This is not a production, always-on, or multi-tenant system; it should be runnable by a small initial group for a few months to generate data and inform later rollout.

## Stakeholders

### Researchers (pilot group individuals)
- A way to log and review billable hours by project/sub-case that aligns with their actual activities
- A draft/assistive attribution that reduces manual estimation and avoids being perceived as surveillance
- Ability to review and edit proposed time attributions before submission
- Clarity on how their Outlook and GitHub activity is mapped to project/sub-case time
- Exportable outputs compatible with their required reporting workflow

### Research group lead / research coordinator (pilot decision-makers)
- (Preferred/conditional) visibility into individual submissions only as drafts, not raw data, based on pilot policy
- Ability to ensure the system supports accurate project-level hour accounting for funder reporting
- Minimal added overhead for coordination and follow-up with researchers
- Ability to monitor pilot adoption and qualitative results to support possible wider rollout

### Administrative/reporting staff (indirect stakeholders)
- Reduced back-and-forth with researchers after timesheet submission
- Exports that meet funder requirements (including Excel formatting/structure)
- Reliable project/sub-case hour reporting that matches the funder’s reporting model
- Traceability sufficient to understand how time was attributed (at least at a summary level)

### Lab/Company management (future rollout stakeholders)
- Evidence from a successful pilot to justify company-wide rollout
- A system that is feasible to operate and maintain during a pilot period
- Costs and operational implications that fit a non-profit, lab-keeping context

### Project owners/funders (external reporting acquirers)
- Accurate reporting of billable hours per allocated project/sub-case
- Compliance with funder-specific export and reporting format expectations
- Consistency with funder rules around allocated hours and reporting granularity

### System maintainers (developers/admin operating the pilot system)
- A maintainable pilot implementation that can run without high-availability or 24/7 requirements
- Integration mechanisms for Outlook and GitHub ingestion
- Configuration support for funder-specific exports and project keyword/context mapping
- Operational tooling for pilot setup, onboarding, and data ingestion monitoring

### Security/privacy reviewers (internal policy stakeholders)
- Assurance that the pilot is not intended for surveillance and that only relevant data is processed for attribution
- Support for access-control decisions (e.g., individual-only draft visibility)
- A clear explanation of data sources and what is stored/retained during the pilot

## Constraints

- Pilot scope: small initial group for a few months; no high availability, no multi-tenancy, no 24/7 uptime requirements
- Non-surveillance intent: outputs are primarily for the individual’s review; access controls must align with policy
- Researchers must be able to review and edit proposed time attribution before submission
- Export requirements vary by funder and must be adaptable (e.g., Excel formats specific to a funder)
- Project context required for disambiguation: need a mechanism to input project context keywords/sub-case names and link projects to Outlook events and repos
- Uncertain/incomplete definition of GitHub signals: solution must be robust to varying contribution patterns (e.g., infrequent pushes)
- Hosting is undecided; solution should accommodate likely options and deployment constraints
- Admin back-and-forth should be reduced by improving draft quality and making gaps explicit earlier in the workflow

## Out of Scope

- Company-wide rollout design and deployment beyond pilot group
- 24/7 operational readiness, enterprise-grade high availability, and disaster recovery guarantees
- Full production-grade multi-tenant architecture
- Monitoring/surveillance features intended to track individuals against productivity norms
- Automated submission without user review
- Deep HR/compliance systems unrelated to time reporting (e.g., personnel management)

## Open Questions

- Hosting/deployment: Where will the pilot run (local server, cloud, managed service), and what constraints apply (network access, data residency, identity providers)?
- Access control policy: Should only individuals see their own draft initially (as currently leaning), and who (if anyone) can view summaries? What visibility rules are acceptable during the pilot?
- Data retention and privacy: What data from Outlook/GitHub is stored vs. processed on the fly, and for how long? Is any data retention policy required?
- Attribution methodology: What exact rules should govern mapping Outlook events and GitHub activity to projects/sub-cases (keyword matching vs LLM classification vs hybrid)?
- Project-to-repo linking: How should projects be linked to GitHub repositories (manual selection, keyword-based mapping, heuristics)? Who maintains the mapping?
- GitHub signal selection: Which signals are in scope for attribution (commits, diffs, push timing, repo sweeps, PRs/issues), and what is the acceptable attribution confidence/coverage when signals are sparse?
- Allocation gap handling: When actual logged time is below the allocated target, should the system suggest additional attribution candidates, prompt for justification/notes, or allow user-driven adjustment without additional context?
- Funder export requirements: How many export templates must be supported in the pilot, and can export mapping be configured rather than hard-coded?
- User experience: What is the minimum UI needed for researchers (project selection, draft review, edit flows, conflict/gap explanation)?
- Integration boundaries: Which Outlook sources are used (calendar events, meeting titles, attendees), and which GitHub scopes (org/repo list) are accessible?
- LLM constraints: Are there requirements for running the LLM locally vs using a hosted API? What data can be sent externally (if any), and are there approval constraints?
- Success criteria: What metrics define pilot success (reduced admin back-and-forth count, reduced researcher time spent filling timesheets, accuracy of funder exports, user satisfaction)?
- Onboarding/setup: What inputs must each researcher provide at setup (project lists, sub-case definitions, repo ownership/linking, keywords), and who will own that process?

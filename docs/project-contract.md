# PlanBridge v1.0 — Project and Data Contract

## 1. Purpose

PlanBridge captures progress statements from project reports, identifies
the planned activities they may describe, explains its matching results,
and routes uncertain matches to human review.

This document defines the initial v1.0 contract. Implementation details
may be refined when necessary, but the frozen architecture, MVP scope,
and mandatory safeguards must remain intact.

## 2. Core workflow

1. Import planned activities from a CSV or XLSX file.
2. Import a progress report while preserving its original text and source.
3. Extract one or more structured progress events from the report.
4. Generate and rank candidate planned activities.
5. Record matching scores and human-readable explanations.
6. Require human review when a match is ambiguous or below the acceptance
   criteria.
7. Apply approved progress updates in a database transaction.
8. Record the decision and resulting changes in an audit trail.
9. Display project progress summaries.

An imported statement is not an approved schedule update. A high matching
score alone never authorizes a database mutation.

## 3. Core entities

### 3.1 Project

Represents a project whose planned activities and reported progress
are being tracked.

Initial fields:
- id: internal UUID primary key
- name: required project name
- description: optional description
- created_at: creation timestamp

### 3.2 PlannedActivity

Represents an activity imported from the project schedule.

Initial fields:
- id: internal UUID primary key
- project_id: owning project
- external_id: identifier from the source schedule
- name: required activity name
- description: optional activity description
- planned_start: optional planned start date
- planned_finish: optional planned finish date
- planned_progress: optional planned progress percentage, if supplied
- current_progress: current approved progress percentage, if available
- status: normalized activity status
- source_metadata: import provenance where needed

The source external_id identifies an activity within its project.
Duplicate external IDs within the same project must be detected during
import rather than silently creating conflicting activities.

Planned dates and planned progress are not overwritten by reported progress.
Only explicitly approved updates may change permitted current-progress
fields. Schedule dates, durations, dependencies, and baselines are not
automatically rescheduled.

### 3.3 ProgressReport

Represents an imported report and its provenance.

Initial fields:
- id: internal UUID primary key
- project_id: owning project
- original_text: original report text, preserved without replacement
- source_filename: original uploaded filename
- source_format: CSV or XLSX
- source_row: source row or record reference when applicable
- imported_at: import timestamp

The original text is the evidence used to derive progress events.
Normalization and extraction must not destroy or replace that evidence.

### 3.4 ProgressEvent

Represents a structured statement extracted from a progress report.

Initial fields:
- id: internal UUID primary key
- report_id: originating report
- event_text: relevant statement retained from the source
- normalized_text: optional normalized text used for matching
- reported_activity_id: optional activity identifier explicitly present
  in the report
- reported_status: optional extracted status
- reported_progress: optional reported progress percentage
- reported_date: optional date associated with the statement
- extraction_metadata: optional extraction details

Missing values remain unknown. The system must not invent dates,
percentages, completion claims, or activity identifiers.

Reported values are observations, not approved schedule changes.

### 3.5 MatchResult

Represents the explainable ranking of a candidate activity for an event.

Initial fields:
- id: internal UUID primary key
- event_id: evaluated progress event
- activity_id: candidate planned activity
- fuzzy_score: normalized RapidFuzz similarity score, from 0 to 1
- tfidf_score: normalized TF-IDF cosine similarity score, from 0 to 1
- combined_score: configured combined ranking score, from 0 to 1
- explanation: human-readable evidence for the ranking
- rank: candidate's position for the event
- matcher_version: identifier for the matching configuration

Scores are ranking evidence, not calibrated probabilities of correctness.
The initial combination weights and review thresholds must be configurable
and evaluated on a held-out synthetic test set before being finalized.

Candidate results must preserve the distinction between a strong unique
match and multiple competing plausible matches.

### 3.6 ReviewDecision

Represents a human decision about a progress event and its proposed match.

Initial fields:
- id: internal UUID primary key
- event_id: reviewed progress event
- selected_activity_id: activity selected by the reviewer, if any
- decision: approved, rejected, or needs_clarification
- reviewer: reviewer identifier
- comment: optional explanation
- decided_at: decision timestamp

A decision must refer to a valid event and, where applicable, an activity
belonging to the same project. Rejected or unresolved events must not
mutate approved schedule data.

### 3.7 AuditRecord

Records review decisions and changes to approved project data.

Initial fields:
- id: internal UUID primary key
- project_id: affected project
- event_id: related progress event, when applicable
- decision_id: related review decision, when applicable
- action: action performed
- entity_type: affected entity type
- entity_id: affected entity identifier
- before_state: previous values for changed fields, when applicable
- after_state: resulting values for changed fields, when applicable
- actor: responsible reviewer or system operation
- created_at: audit timestamp

The audit record for an approved update and the corresponding database
mutation must commit in the same transaction. If either fails, both must
roll back. Audit history must not be silently rewritten by ordinary
application operations.

## 4. Initial import contract

### Planned activity CSV/XLSX

Required columns:
- external_id
- name

Optional columns:
- description
- planned_start
- planned_finish
- planned_progress

Rules:
- Validate required columns, row values, date formats, and percentages.
- Progress percentages must be between 0 and 100, inclusive.
- Detect duplicate external IDs within a project.
- Report invalid rows clearly; do not silently discard them.
- Preserve source filename and row references for traceability.

- Define explicit import/update behavior before implementing imports that
  modify existing activities.

### Planned activity import and update policy

- **No implicit overwrites:** Importing a planned activity must not silently overwrite an existing activity in the same project.
- **Existing activity conflict:** If an uploaded row's `external_id` already exists in the target project, report the row as a conflict and leave the existing database record unchanged.
- **Duplicate IDs within the upload:** Flag all rows involved in a duplicate `external_id` conflict. Do not silently select one row.
- **Valid new activities:** Only validated rows whose `external_id` does not already exist in the target project are eligible for insertion.
- **Invalid or conflicting rows:** Report the source row and a clear reason. Do not silently discard rows or partially modify existing activities.
- **Import summary:** Report inserted, invalid, conflicting, and skipped-empty-row counts.
- **Future updates:** Any update to existing activities requires a separately defined, explicit update workflow. It is not part of the initial import behavior.


### Progress report CSV/XLSX

The importer must support a documented text-column mapping rather than
assuming every report uses the same column names.

Required logical input:
- original report text

Optional columns:
- activity identifier
- reported status
- reported progress
- reported date

Rules:
- Preserve the original report text.
- Validate supplied identifiers, dates, statuses, and percentages.
- Missing optional fields are unknown, not zero or false.
- Retain source filename and row references where applicable.
- Reject unsupported formats and malformed inputs with clear errors.

CSV encoding, XLSX sheet selection, empty rows, duplicate records, and
import error reporting must be handled explicitly during implementation.

## 5. Matching and review contract

1. Candidate activities must belong to the same project as the event.
2. RapidFuzz and TF-IDF provide complementary similarity evidence.
3. Candidate rankings must be reproducible for the same inputs and
   matching configuration.
4. The top candidates, scores, and explanations must be available for
   review.
5. Low-confidence or ambiguous matches must be flagged for human review.
6. A missing or invalid candidate must never be treated as a match.
7. Thresholds and score weights must be evaluated using a held-out
   synthetic test set that is not used to tune those settings.
8. Evaluation must report matching quality and relevant error cases,
   including incorrect high-ranked matches and ambiguous cases.

## 6. Approval and transaction contract

- Importing a report does not approve its extracted events.
- Matching does not modify planned activities.
- Only an authorized approval workflow can apply a permitted progress
  update.
- Validate the selected event, activity, project association, and reported
  values before applying an update.
- Record the review decision, before/after values, and audit record.
- Apply the permitted update and audit record in one database transaction.
- Roll back the complete operation if a required write fails.
- Preserve planned schedule dates, durations, dependencies, and baselines.
- Duplicate approval requests must not apply the same event twice.
- Rejected or unresolved events must leave approved schedule data unchanged.

## 7. Initial status and progress rules

The initial status vocabulary is:
- not_started
- in_progress
- completed
- blocked
- on_hold
- unknown

Input aliases may be mapped to this vocabulary through explicit validation
and normalization rules.

Progress is a percentage from 0 to 100. An absent progress value is
unknown; it must not be interpreted as zero. A reported status and reported
percentage may conflict, so conflicting evidence must be flagged rather
than silently reconciled.

The exact permitted update fields and conflict-resolution behavior must
be defined before implementing the approval service.

## 8. API and data-handling principles

- Use JSON for application API request and response bodies.
- Use consistent identifiers and ISO 8601 date/time representations.
- Validate requests at the API boundary and enforce data integrity in
  the database where appropriate.
- Return actionable validation errors without exposing secrets or
  internal stack traces.
- Keep original report evidence separate from normalized matching text.
- Do not introduce autonomous schedule changes.
- Keep the initial implementation within the frozen monolithic
  React/Vite and FastAPI architecture.

## 9. MVP acceptance criteria

The MVP is acceptable when:

- Planned activities and progress reports can be imported and validated.
- Original report text and source provenance are preserved.
- Structured events can be matched to ranked candidate activities.
- Matching results include scores, explanations, and ambiguity handling.
- Human review is required for uncertain cases.
- Unapproved, rejected, and unresolved events cannot mutate schedule data.
- Approved changes and audit history are committed atomically.
- Project summaries reflect approved progress data.
- Unit and API tests cover validation, matching, review, transactions,
  and audit behavior.
- Matching quality is evaluated on a held-out synthetic test set.

## 10. Concurrency and idempotency

- Approval must revalidate the event, selected activity, project association,
  and permitted update fields inside the database transaction.
- The approval workflow must prevent concurrent requests from applying the
  same progress event more than once.
- Use PostgreSQL transaction and row-locking capabilities where appropriate
  to serialize competing approvals.
- If the event has already been approved, a repeated approval must not
  apply another mutation. Return a clear result indicating that it was
  already processed.
- If the activity changes between review and approval, revalidate the
  proposed update against the current stored state before applying it.
- A failed validation or database write must not leave a partial approval
  or an audit record claiming that an unsuccessful update was committed.
- Tests must cover duplicate approvals, conflicting approvals, transaction
  rollback, and preservation of unchanged schedule fields.

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

Stored candidate ordering:

- Candidates are ordered by `combined_score` descending, then `tfidf_score`
  descending, then `fuzzy_score` descending, then `external_id` ascending,
  then `id` ascending. That order is total, so ranking is deterministic.
- The stored list is an order-preserving subsequence of that fully ordered
  scored set. It is not a contiguous prefix of it: candidates may be absent
  because of the stored-candidate limit or candidate-floor pruning.
- `rank` is the position within the stored list, not the position among all
  scored candidates. Ranks are contiguous from 1. Each explanation states
  the candidate's position among all scored candidates, so a rank cannot be
  mistaken for an overall standing.
- Rank 1 is the highest-scoring eligible candidate whenever any exists. The
  single exception is an insufficient-text event, where no text ranking is
  computed at all.
- Candidate-floor pruning applies to ranks 2 and below only.
- The top score and the margin to the next candidate are computed over the
  full scored set, never over the stored list, so pruning can neither
  conceal nor manufacture ambiguity.
- Where exactly one eligible candidate is scored there is no next candidate,
  so the margin equals that candidate's combined score, and the absence of a
  competitor is stated in its explanation. The margin is not unavailable in
  this case: a sole candidate is unambiguous by definition, rather than
  ambiguous for want of a comparison. A sole candidate therefore cannot be
  reported as ambiguous while the accept floor is at or above the configured
  margin, which holds for every configuration in the development search
  grid.

Identifier evidence:

- An activity whose `external_id` equals the event's reported activity
  identifier is stored when it is not already present. It is stored for
  transparency only: it never reorders candidates and never affects top-1
  selection, the top score, or the margin.
- A stored score of zero in an insufficient-text row means that no
  similarity was computed. It does not mean that similarity was computed and
  found to be zero. In that case the top score and the margin are reported
  as unavailable, never as zero, and routing must not treat those zeros as
  measured similarity. This distinction lives in the row's explanation,
  which a consumer of the stored scores must therefore read.

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

### Progress report column mapping and import policy

- **Caller-supplied mapping:** The importer is given a column mapping naming
  the source column for each logical field. It does not infer meaning from
  header names.
- **Required text column:** The mapping must name the report text column and
  that column must exist in the file. A missing one is a read error that
  names the available columns.
- **Optional field states:** Each optional field is in one of three states.
  Left at its default, the documented default column name is used if the file
  contains it, and the field is otherwise unmapped. Set to nothing, the
  caller declares that the file does not supply it and the field is unmapped.
  Named explicitly, the column must exist: a name the file does not contain
  is a read error naming the available columns. An explicitly mapped field is
  therefore never silently imported as missing values. The documented default
  column names are `text`, `activity_id`, `status`, `progress`, `date`.
- **Unmapped source columns:** Columns the mapping does not name are ignored
  rather than rejected, because reports carry fields the system does not use.
- **Unmapped fields:** An unmapped optional field is unknown for every row
  and is reported in the import summary. It is never defaulted.
- **Original text preserved:** The report text is stored exactly as read,
  without trimming or normalization. Duplicate detection compares a
  normalized copy only, and that normalization is applied identically to
  uploaded rows and to stored reports.
- **Blank rows:** Only a wholly blank source row is skipped. A row carrying
  content elsewhere but no report text is an invalid row, reported with its
  source row number.
- **Reported status:** Input statuses are mapped to the contract vocabulary
  through the alias table documented in section 7. An unrecognized status is
  reported rather than silently recorded as unknown.
- **Scope:** Importing a report records its text and provenance only. It does
  not extract progress events, match activities, or modify planned
  activities. Validated optional observations are returned to the caller for
  the extraction step rather than persisted by the importer.

### Progress report duplicate detection

A report may legitimately restate a statement, so repeated text is never
treated as proof of a redundant observation and is never silently discarded.
Duplicates are diagnostics, reported alongside the import rather than as
blocking validation errors.

- **Same upload:** Rows whose normalized report text matches another row in
  the same upload are all flagged, including the first. Each flag names a
  deterministic reference occurrence, the lowest source row number in the
  group, so the same upload always produces the same diagnostics.
- **Rows with other errors:** Duplicate detection considers every row with
  nonblank report text, including rows that failed validation for another
  reason. A row with blank text has no text to compare and is not grouped.
- **Existing reports:** Normalized text is also compared against reports
  already stored in the same project. A match is reported as a potential
  duplicate, not as an identical observation, and names the existing record's
  source filename and row where available.
- **Project isolation:** Comparison against stored reports is scoped to the
  target project. Identical text in another project is never flagged.
- **No uniqueness constraint:** Report text carries no database uniqueness
  constraint. Detection is advisory and never prevents an import.
- **Import outcome stated:** Every duplicate flag records whether the row it
  refers to was imported, so a reviewer can tell a flagged-and-stored row
  from a flagged row rejected for an unrelated validation error.

### Progress report import summary

The summary separates blocking outcomes from diagnostics.

- `skipped_empty_count`: wholly blank source rows.
- `invalid_count`: distinct rows not imported because of a blocking
  validation error.
- `inserted_count`: rows persisted.
- These three partition the file: `inserted_count + invalid_count +
  skipped_empty_count` equals the number of source rows read.
- `duplicate_count`: distinct rows carrying a duplicate flag. This count is a
  diagnostic and is independent of the partition above. A flagged row may
  have been imported or rejected, so it may also be counted in
  `inserted_count` or in `invalid_count`.
- Unmapped optional fields are reported alongside the counts.

### Progress event extraction policy

Extraction turns a stored report's preserved text into one or more
structured events. It derives nothing that the source does not state.

- **Scope:** Extraction reads a stored report and writes progress events. It
  does not match activities, rank candidates, approve anything, or modify
  planned activities. The report's `original_text` is never rewritten.
- **Segmentation:** The report text is split on line boundaries, and a
  leading list marker on a line is treated as formatting rather than
  evidence. Segmentation is deterministic: the same text always yields the
  same events in the same order. A single-line report yields exactly one
  event.
- **Recognized list markers:** Only these are removed, and only at the start
  of a line, followed by whitespace or the end of the line:
  - bullet markers `-`, `*` and `•`
  - numeric list markers in the forms `1.`, `2)` and `(3)`

  A marker is recognized only in those exact forms, so a statement opening
  with a negative number, a decimal or a year keeps its text intact.
- **Lettered markers are not stripped:** Forms such as `A)`, `a.` and `I.`
  are left in place in v1.0. They are indistinguishable from legitimate
  source text such as an initial or a reference, and removing them could
  destroy evidence. Recognizing them requires a separately defined rule.
- **Blank segments:** A line with no content after its list marker and
  surrounding whitespace is removed and counted, not stored as an event.
- **Evidence retained:** Each event stores the statement as it appears in the
  source, and its extraction metadata records the segment index and the
  character range it came from, so the exact original text is always
  recoverable from the report.
- **Normalized text:** Normalized matching text is stored separately from the
  retained statement and never replaces it.
- **No derived values in v1.0:** Reported activity identifiers, statuses,
  progress percentages and dates come only from the explicit columns
  validated at import. Extraction does not parse them out of prose. A field
  with no explicit value remains unknown.
- **Unambiguous attachment only:** Explicit column observations describe the
  report, not a particular statement within it. They are attached to the
  event only when the report yields exactly one event. When a report yields
  several events, attaching them to any one of them would assert something
  the source does not say, so every event's reported fields stay unknown.
  Report-level values are never copied onto several events.
- **Observations recorded either way:** The observations a report supplied
  are recorded in extraction metadata under one key, in the same shape,
  whether or not they were applied. Alongside them, metadata states
  explicitly whether they were applied, withheld or never supplied, and why
  anything was withheld. An event-level value that is unknown because the
  report said nothing is therefore distinguishable from one left unknown
  because a known report-level value could not be assigned safely. Review
  tooling consumes that distinction; defining it here does not imply any
  particular interface.
- **Re-extraction:** A report that already has events is reported as a
  conflict. Its existing events are left unchanged and nothing is inserted,
  because match results and review decisions may already refer to them. Any
  deliberate re-extraction requires a separately defined workflow.
- **Concurrent extraction:** The existing-event check must hold against
  competing extractions of the same report, not only against already
  committed state. Extraction therefore takes a row lock on the report
  before checking, so two concurrent attempts cannot both find no events and
  both insert. The second attempt waits, then observes the committed events
  and reports the ordinary conflict, so the refusal policy is the same
  whether the attempts are sequential or concurrent. Locking is per report
  and must not serialize extraction of other reports.
- **Missing report:** Extracting an unknown report is an error, not an empty
  result.
- **Extraction summary:** Report created-event, skipped-blank-segment and
  conflict counts, whether report-level observations were withheld, and the
  reason for anything not extracted.
- **Observations remain observations:** An extracted event is not an approved
  schedule update, however explicit its reported values.

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
9. A reported activity identifier is evidence, not an instruction. Identifier
    agreement with the leading candidate is recorded and never promotes a
    candidate or relaxes a threshold. An identifier naming an eligible
    activity that is not the leading candidate, and an identifier naming no
    eligible activity in the project, each route the event to human review.
10. Routing yields one primary outcome, chosen by a fixed precedence, and a
    list of every applicable reason. An identifier conflict or a not-found
    identifier remains present in that list even when another outcome takes
    precedence, so identifier evidence can never silently resolve or mask
    conflicting text evidence.
11. Escalation to review is distinct from the requirement for human
    approval. Every applied schedule update requires human approval
    regardless of score, so escalation describes only those events for which
    the system cannot offer a single strong candidate.
12. Observations withheld during extraction are never read when matching.
    Where they were withheld, the event simply supplies no identifier.
13. The exact configuration behind every stored `matcher_version` must
    remain available, so that a stored ranking stays interpretable after the
    defaults change. Configurations are append-only: changing a weight or a
    threshold requires a new version, and routing for a stored result is
    recomputed from the configuration that result names, never from the
    current default.
14. A held-out evaluation that fails its pre-registered criteria is reported
    as a failure with its error cases. It must not be used to adjust weights
    or thresholds, and a repeat run against the same held-out data is not a
    held-out result.

### Routing outcomes and reasons

Every matched event receives exactly one primary outcome and a list of every
applicable reason. The primary outcome is the first applicable outcome in
this precedence:

`no_candidates` > `insufficient_text` > `identifier_conflict` >
`identifier_not_found` > `low_confidence` > `ambiguous` >
`single_strong_candidate`

Applicability conditions:

- **`no_candidates`:** the primary outcome whenever the project has no
  eligible planned activity. No candidate rows are stored, and neither the
  top score nor the margin is available. It takes precedence over every
  other outcome, including a reported identifier, because there is nothing
  to name. Identifier reasons are still recorded: where an identifier was
  supplied, no eligible activity can match it, so `identifier_not_found` is
  retained as a reason; where none was supplied, `identifier_absent` is
  recorded. `identifier_conflict` is never recorded here, because the named
  activity cannot exist among eligible activities.
- **`insufficient_text`:** the event's normalized matching text contains no
  usable token. No text similarity is computed, so neither the top score nor
  the margin is available; both are unavailable rather than zero. Where the
  reported identifier names an eligible activity, exactly one
  identifier-only candidate row is stored at rank 1. Where the identifier is
  absent or names no eligible activity, no candidate rows are stored and
  neither the top score nor the margin is available, so there is no zero
  score for a consumer to misread as a failed similarity calculation or as a
  strong-match decision.
- **`identifier_conflict`:** the reported identifier names an activity that
  exists among the project's eligible activities, and that activity differs
  from the text-ranked leader. It is therefore recorded only where a
  text-ranked leader exists, which requires both usable matching text and at
  least one eligible activity. The named activity and the leading candidate
  are both annotated.
- **`identifier_not_found`:** the event supplied an identifier and no
  eligible activity in the project matches it. The reason is recorded
  whenever that holds, including where `no_candidates` or
  `insufficient_text` takes precedence as the primary outcome.
- **`low_confidence`:** the leading candidate's combined score is below the
  accept floor. This outranks ambiguity, because a leading candidate below
  the accept floor makes the margin moot.
- **`ambiguous`:** the leading candidate's combined score is at or above the
  accept floor and the margin to the next candidate is below the configured
  margin.
- **`single_strong_candidate`:** the leading candidate's combined score is
  at or above the accept floor, the margin is at or above the configured
  margin, and no identifier conflict or not-found reason applies.

Reasons recorded alongside the primary outcome:

- `identifier_absent`: the event reports no identifier. Informational; it
  never changes the outcome. An event whose report-level observations were
  withheld during extraction supplies no identifier and is recorded this
  way.
- `identifier_agrees`: the reported identifier names the leading candidate.
  Informational only: it never promotes a candidate, relaxes a threshold or
  changes the primary outcome.
- `identifier_matched_without_text`: the reported identifier names an
  eligible activity, but the event has no usable text, so there is no
  ranking for the identifier to agree or conflict with. It is recorded
  alongside the `insufficient_text` outcome and the identifier-only
  candidate row, so that this case stays distinguishable from a textless
  event that named nothing. Informational only, like the two reasons above.
- Every applicable condition above is recorded as a reason, including one
  whose outcome was outranked. An `identifier_conflict` or
  `identifier_not_found` reason therefore remains visible when
  `no_candidates` or `insufficient_text` takes precedence, so identifier
  evidence can never silently resolve or mask conflicting text evidence.

An identifier-only candidate, stored solely because the report names it and
carrying scores of zero because no similarity was computed, is never the
leading candidate for routing and can never produce
`single_strong_candidate`. Every outcome above requires human approval
before any schedule update.

### Matching configuration and evaluation policy

Weights and thresholds are configuration, selected on development data and
frozen before the held-out evaluation.

- **Configurable parameters:** the component weights, which sum to 1; the
  candidate floor, below which a candidate is not stored except at rank 1 or
  as identifier evidence; the accept floor, below which the leading
  candidate is low confidence; the margin, below which the leading two
  candidates are ambiguous; and the number of candidates stored.
- **Provisional initial values:** equal component weights, candidate floor
  0.15, accept floor 0.55, margin 0.10, five stored candidates. These are
  provisional until the development selection below has run.
- **Development search grid:** the configurations searched are exactly the
  product of
  - fuzzy weight 0.30, 0.40, 0.50, 0.60, 0.70, with the TF-IDF weight
    always 1 minus the fuzzy weight;
  - accept floor 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70;
  - margin 0.05, 0.10, 0.15, 0.20;
  - candidate floor 0.10, 0.15, 0.20.
- **Selection constraints:** a configuration qualifies only if it satisfies
  all of a false-strong-match rate at or below 0.05, at least 100 strong
  match claims, stored-candidate recall at or above 0.90, and an escalation
  rate over positives at or below 0.40.
- **Selection rule:** among qualifying configurations, maximize top-1
  accuracy; break ties by lower escalation rate, then lower fuzzy weight,
  then lower accept floor, then lower margin, then lower candidate floor.
  The rule is fixed before the search runs.
- **No qualifying configuration:** report that none exists, with the search
  grid and the binding constraint for each configuration. No target is
  relaxed and no configuration is selected. The held-out set is not
  generated or run.
- **Freeze:** the selected configuration is recorded under a new matcher
  version, and both are committed, before the held-out evaluation runs.

Held-out protocol:

- The configuration and its matcher version are committed first. The
  held-out evaluation runs once, against that committed configuration.
- Held-out results must not be used to select a configuration, to tune
  weights or thresholds, or to revise acceptance criteria.
- A held-out run that fails its pre-registered criteria is reported as a
  failure, with its error cases. The permitted responses are to accept and
  document the limitation, or to change the design and then evaluate
  against a new held-out split generated from a new seed, with criteria
  pre-registered before that run.
- A repeated run against the same held-out data is not a held-out result
  and is not reported as one.
- If no development configuration qualifies, the held-out set is neither
  generated nor run.

Evaluation metrics and their denominators:

- Positives are events derived from a known activity; negatives are events
  with no corresponding activity. Retrieval recall is undefined for
  negatives, which are therefore excluded from recall denominators.
- **Top-1 accuracy:** events whose leading candidate is the true activity,
  over all positives.
- **Stored-candidate recall:** positives whose true activity appears among
  the candidates actually retained for review, including any identifier
  row, over all positives. This is the operational acceptance criterion,
  because it measures what a reviewer can see.
- **Scored recall at 5 and at 3:** positives whose true activity appears in
  the leading five or three scored candidates before storage pruning, over
  all positives. Diagnostic only.
- **Pruning loss:** positives whose true activity was within the leading
  five scored candidates but is not retained for review, over positives
  whose true activity was within the leading five.
- **False-strong-match rate:** events presented as a single strong candidate
  whose leading candidate is not the true activity, over all events
  presented as a single strong candidate. A negative presented as a strong
  candidate counts as a false strong match. The denominator is the set of
  strong-match claims, not all events.
- **Claim volume guard:** the false-strong-match rate is assessable only
  with at least 100 strong-match claims, which is a hard minimum; 300 is
  the target. Below the minimum the criterion is reported as insufficient
  and fails; an insufficient denominator is never reported as a pass.
- **Interval reporting:** the false-strong-match rate is reported as the
  point estimate, a 95% Wilson score interval for that estimate, and the
  claim denominator, so a borderline result reads as borderline rather than
  as a clean pass.
- **Escalation rate:** positives not presented as a single strong candidate,
  over all positives. Escalation over all events is reported as a
  diagnostic, since a negative that escalates is behaving correctly.
- **Negative strong-claim rate:** negatives presented as a single strong
  candidate, over all negatives. Diagnostic.
- An event with no computable leading candidate still counts in the positive
  denominators as a retrieval failure. It is never excluded.

Dataset adequacy and leakage:

- Development and held-out sets are generated deterministically from
  recorded seeds: `20261101` for development and `20261102` for held-out.
- The two sets are split by project, so no project's activities appear in
  both, and no normalized activity name may occur in both sets. Splitting by
  event rather than by project would leak phrasing between the sets.
- Generated datasets are not committed. They are reproducible from the
  generator and its recorded seeds, and committing them would invite
  inspection of held-out rows.
- Each set requires at least 20 projects, 400 activities, 400 positives, 200
  negatives, and 600 events. Minimum sizes are fixed before any run.
- Both runs report event, positive, negative and strong-claim counts, every
  metric denominator, and whether each criterion was assessable.
- Insufficient held-out evidence is reported as insufficient and failing. It
  is never reported as a pass, and the held-out set is not regenerated and
  rerun to obtain a better number.

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

Input aliases are mapped to this vocabulary through explicit validation
and normalization rules.

The accepted input aliases are listed below. Matching is case-insensitive;
underscores, hyphens and slashes are treated as spaces and repeated
whitespace is collapsed before lookup. An input that matches no alias is
reported as an error; it is never silently recorded as `unknown`.
Aliases are listed in normalized form, so `n a` matches the input `N/A`.

| Canonical status | Accepted aliases |
| --- | --- |
| `not_started` | `not started`, `notstarted`, `pending`, `to do`, `todo`, `yet to start` |
| `in_progress` | `in progress`, `inprogress`, `ongoing`, `started`, `under way`, `wip`, `work in progress` |
| `completed` | `closed`, `complete`, `completed`, `done`, `finished` |
| `blocked` | `blocked`, `blocker`, `impediment`, `stuck` |
| `on_hold` | `deferred`, `on hold`, `onhold`, `paused`, `suspended` |
| `unknown` | `n a`, `na`, `tbd`, `unknown` |

A status reported for an activity remains an observation. Normalizing it
does not approve a schedule update.

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
- Evaluation reports stored-candidate recall, the false-strong-match rate
  and the escalation rate, each with its denominator stated, together with
  the event, positive, negative and strong-claim counts for the development
  and held-out runs.

### Test execution prerequisites

- Unit tests must not require a database connection.
- Database integration tests run against a dedicated test database. They must
  never fall back to the development database: a configured database other
  than the dedicated test database is an unconditional failure, in every
  mode.
- When integration tests are requested explicitly, an unavailable database or
  a test database behind the latest migration must fail clearly. A run that
  silently skips its integration tests and reports success is not acceptable.
- Test setup never creates, drops, truncates or migrates a database.

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
- The same rule applies wherever a check decides whether to write: a read
  followed by a conditional insert must be serialized by the database, not
  by assumption. Under the default read-committed isolation an uncommitted
  insert is invisible to a competing transaction, so an unlocked
  check-then-insert admits duplicates. Tests covering such a check must
  exercise it from more than one connection, since a single session cannot
  reproduce the interleaving.
- Matching is such a check. It locks the progress event before testing
  whether results already exist for the matching configuration, so two
  concurrent attempts cannot both find none and both insert. The lock is per
  event, and planned activities are not locked, because matching only reads
  them and must not block imports.
- A test for a lock of this kind must fail when the lock is removed. A
  holder that takes a conflicting lock on a parent row can block the child
  insert through the foreign key check alone, which would make such a test
  pass whether or not the code locks anything.

# PlanBridge — current progress and handoff

Verified against the repository at commit `b80ae64` on branch `main`.
Everything below was confirmed by inspecting files, running tests and
querying the databases. Test numbers are actual run output, not estimates.

`docs/project-contract.md` remains the authoritative specification. This
document describes state; it never overrides the contract.

---

## 1. Project overview

**PlanBridge v1.0** (inspired by SIH26122) captures progress statements from
project reports, identifies the planned activities they may describe,
explains its matching results, and routes uncertain matches to human review.
It is an eight-week solo MVP.

The central safety rule, from contract section 2: *an imported statement is
not an approved schedule update, and a high matching score alone never
authorizes a database mutation.*

### MVP scope

Import planned activities and progress reports from CSV/XLSX, preserve
original report text, extract structured progress events, rank candidate
activities with explainable scores, require human review for uncertain
cases, apply approved changes transactionally with an audit trail, and show
project progress summaries. Matching quality is evaluated on a held-out
synthetic test set.

Version 1.0 excludes OCR, speech recognition, live enterprise integrations,
autonomous schedule changes, complex predictive ML, multi-agent AI and
microservices.

### Technology stack (frozen)

| Layer | Choice |
|---|---|
Backend | Python 3.12.10, FastAPI 0.162.0, Uvicorn 0.54.0 |
Database | PostgreSQL via SQLAlchemy 2.1.6, Alembic 1.20.1, psycopg 3.3.6 |
Ingestion | pandas 3.1.0, openpyxl 3.1.5 |
Matching | RapidFuzz 3.14.6, scikit-learn 1.9.1, scipy 1.18.1, numpy 2.5.4 |
Testing | pytest 9.1.1, HTTPX 0.28.1 |
Frontend | React + Vite + TypeScript — **not started** |

### Architecture

A monolithic backend. Layers are deliberately separated so each is testable
alone:

```
readers        parse CSV/XLSX into rows keyed by logical field
validation     validate values, no database access
import         persist, with transaction and conflict handling
extraction     turn report text into progress events
matching       pure scoring: no database, no routing
routing        outcome and reason classification from a scoring result
explanations   human-readable evidence, no decisions
evaluation     synthetic datasets and cached evidence (not application code)
```

---

## 2. Completed implementation

Contract workflow steps 1, 2 and 3 are complete and database-tested. Step 4
(matching) has its pure layers complete; its evaluation is in progress.

### 2.1 Planned-activity import — complete

| File | Responsibility |
|---|---|
`backend/app/planned_activity_reader.py` | read CSV/XLSX, validate headers, preserve source row numbers |
`backend/app/import_validation.py` | required values, dates, progress range, duplicate external IDs |
`backend/app/planned_activity_import.py` | insert valid rows, report conflicts, never overwrite |

Behaviour: existing activities are never silently overwritten; every row in
an uploaded duplicate-ID conflict is flagged; `source_metadata` records
filename, format and source row.

### 2.2 Progress-report ingestion — complete

| File | Responsibility |
|---|---|
`backend/app/progress_report_reader.py` | caller-supplied `ColumnMapping`, three-state optional fields (`AUTO` / `None` / explicit) |
`backend/app/progress_report_validation.py` | required text, status aliases, progress, dates, duplicate detection |
`backend/app/progress_report_import.py` | persist text and provenance, flag duplicates within the upload and against stored reports |

`original_text` is stored byte-for-byte. Duplicates are advisory diagnostics
that never block an import. Cross-upload duplicate detection is scoped to
the project and compares the validator's own normalization.

### 2.3 Progress-event extraction — complete

| File | Responsibility |
|---|---|
`backend/app/progress_report_segmentation.py` | deterministic split on lines, bullet and numeric markers only |
`backend/app/progress_event_extraction.py` | create events, attach or withhold report observations, refuse re-extraction |

Key rule: report-level observations are attached only when the report yields
exactly one event. For a multi-statement report every event's reported
fields stay unknown and the values are recorded under one metadata key with
state `applied`, `withheld` or `not_supplied`. Re-extraction of a report
that already has events is refused. The report row is locked
(`with_for_update=True`) before the existing-event check, so two concurrent
extractions cannot both insert.

### 2.4 Matching — pure layers complete

| File | Responsibility |
|---|---|
`backend/app/matching_config.py` | frozen `MatcherConfig`, append-only version registry, `config_fingerprint` |
`backend/app/matching.py` | normalization, RapidFuzz and TF-IDF components, `assemble_result` |
`backend/app/matching_routing.py` | primary-outcome precedence and reasons |
`backend/app/matching_explanations.py` | evidence prose, no probability language |

- `score_candidates` computes component scores and delegates to
  `assemble_result`, which owns ranking, rounding, tie-breaks, margin,
  pruning and identifier retention. Evaluation reuses `assemble_result`, so
  there is one assembly path.
- `resolve_named_activity` is shared by scoring and evaluation.
- Current matcher version `planbridge-matcher-1`, **provisional**: weights
  0.50/0.50, candidate floor 0.15, accept floor 0.55, margin 0.10, five
  stored candidates. Fingerprint pinned at
  `7870b9c57574056f04af267ddc2f3df82084eac37cbb6e53765bafe373b75760`.

### 2.5 Evaluation framework — partially complete

| File | Status |
|---|---|
`backend/evaluation/synthetic_dataset.py` | **complete** — deterministic generator and validation |
`backend/evaluation/evidence.py` | **complete** — cached component scores |
`backend/evaluation/metrics.py` | **not written** |
`backend/evaluation/development_search.py` | **not written** |
`backend/evaluation/benchmark.py` | **not written** |

The development split generates 20 projects, 400 activities, 600 events,
400 positives, 200 negatives and 4 textless negatives. Two separate
processes produce an identical dataset. Evidence precomputation takes
0.571 s for 11,920 pairs with a 2,363 KiB cache, against a measured naive
grid cost of 366.5 s.

### 2.6 Database tables, models and migrations

All seven tables exist in both databases and match `backend/app/models.py`:
`projects`, `planned_activities`, `progress_reports`, `progress_events`,
`match_results`, `review_decisions`, `audit_records`, plus
`alembic_version`.

Two migrations, both applied; head is `0e8b1dbb8b0e`:

```
<base> -> 47f8534a1543  create initial database schema
47f8534a1543 -> 0e8b1dbb8b0e  align activity statuses with project contract
```

**No migration has been needed since.** `match_results` already stores both
component scores, the combined score, explanation, rank and matcher version,
and routing is recomputed rather than persisted.

### 2.7 API endpoints

**One endpoint exists.** `GET /health` in `backend/app/main.py` returns
`{"status": "ok"}`. No import, extraction, matching, review or summary
endpoint has been written. `get_session()` exists in
`backend/app/database.py` ready to serve as a FastAPI dependency, and is not
yet used by any route.

### 2.8 Frontend

**Not started.** `frontend/` is an empty directory with no tracked files, no
`package.json`, no Vite configuration.

---

## 3. Environment and configuration

### Setup

```bash
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

The virtual environment lives at `backend/.venv` (not `backend/venv`).
`requirements.txt` is a complete freeze: 82 exact pins, and every installed
package is declared.

### Database configuration

Two local PostgreSQL databases, both at migration head:

| Database | Purpose | Config file |
|---|---|---|
`planbridge` | development | `backend/.env` |
`planbridge_test` | integration tests | `backend/.env.test` |

Both files define exactly one variable, `DATABASE_URL`. Both are untracked
and ignored by `.gitignore` (`.env`, `.env.*`); neither has ever been
committed. **Never print their values.**

`app/config.py` resolves `.env` from the package location, not the working
directory, so commands work from either `.` or `backend/`. Settings and the
engine are created lazily, so importing the application requires no
configured environment.

### Commands

```bash
# default test suite (excludes the slow grid matrix)
backend/.venv/Scripts/python.exe -m pytest -q -m "not slow"

# the slow 420-configuration equivalence matrix
backend/.venv/Scripts/python.exe -m pytest -q -m slow

# PostgreSQL integration tests; --require-db turns a skip into a failure
backend/.venv/Scripts/python.exe -m pytest --require-db -m integration -q

# everything
backend/.venv/Scripts/python.exe -m pytest -q

# migration state (run from backend/)
cd backend && .venv/Scripts/python.exe -m alembic -c ../alembic.ini current

# API server
cd backend && .venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Frontend commands do not exist yet.

To point Alembic at the test database, set `DATABASE_URL` in the
environment; it takes precedence over the `.env` file. Never migrate,
truncate or reset the development database as part of a test.

---

## 4. Testing

347 tests across 18 files.

| File | Tests |
|---|---|
`test_matching.py` | 48 |
`test_synthetic_dataset.py` | 40 |
`test_matching_routing.py` | 35 |
`test_evidence_cache.py` | 30 |
`test_matching_explanations.py` | 25 |
`test_db_prerequisites.py` | 23 |
`test_progress_event_extraction.py` | 21 |
`test_progress_report_validation.py` | 19 |
`test_progress_report_reader.py` | 19 |
`test_progress_report_import.py` | 18 |
`test_progress_report_segmentation.py` | 17 |
`test_matching_config.py` | 16 |
`test_import_validation.py` | 11 |
`test_planned_activity_reader.py` | 10 |
`test_planned_activity_import.py` | 7 |
`test_planned_activity_import_integration.py` | 4 |
`test_configuration.py` | 3 |
`test_health.py` | 1 |

### Actual results, run at commit `b80ae64`

```
pytest -q -m "not slow"                      345 passed, 2 deselected in 19.33s
pytest -q -m slow                            2 passed, 345 deselected in 20.29s
pytest --require-db -m integration -q        39 passed, 308 deselected in 5.09s
pytest -q                                    347 passed in 38.15s
```

Zero failures, zero skips, zero errors. The default suite passes from both
the repository root and `backend/`.

### Test design worth preserving

- **Integration fixtures build their own engine** from `.env.test` and
  refuse any database other than `planbridge_test`, unconditionally. They
  never use `app.database.get_engine()`.
- **Nothing is committed by most integration tests.** Each runs in a
  transaction that is rolled back, with the session joined via
  `join_transaction_mode="create_savepoint"` so the code under test can
  really commit and roll back.
- The one exception is `concurrent_extraction`, which must commit so a
  second connection can see the report. It deletes exactly the rows it
  created.
- **`--require-db`** turns a missing or outdated test database into a
  failure rather than a silent skip. Without it, unit tests still run with
  no PostgreSQL.
- Test setup never creates, drops, truncates or migrates a database.
- Both databases were empty before and after the runs above.

### Known issues and limitations

1. **`identifier_matched_without_text` has no evaluation evidence.**
   Negatives receive only non-existent identifiers and every textless event
   is a negative, so no generated event is both textless and names an
   eligible activity. Unit tests cover the routing condition; evaluation
   does not measure it.
2. **The slow matrix samples 20 of 600 events** against all 420
   configurations, chosen deterministically (`events[::30][:20]`). All 600
   would cost roughly 366 s.
3. **The provisional candidate floor of 0.15 sits inside the scoring noise.**
   Measured: unrelated text scores fuzzy ~0.30–0.36, so combined ~0.15–0.18
   at equal weights. The floor will prune little at that value, and the
   grid's maximum of 0.20 is only just clear of it. Recorded in a test, not
   acted on; development selection must measure it.
4. **`import_progress_reports` has an unlocked read-then-write** in its
   cross-upload duplicate query. Benign by design — duplicate flags never
   gate an insert, so the worst case is a lost diagnostic, not corrupt data.
   Not hardened; worth a deliberate decision.
5. **`--require-db` is opt-in** and nothing in the repository enforces it.
   There is no CI configuration at all.
6. **The prerequisite skip/fail policy is tested at the function boundary**,
   not by a subprocess run against a genuinely broken database.

---

## 5. Git status

- **Branch:** `main`. **Latest commit:** `b80ae64`.
- **Working tree:** clean, including untracked files.
- **`origin/main` is at `29723ea`; `main` is 10 commits ahead and
  unpushed.** The remote is `https://github.com/Dheeresh47/PlanBridge.git`.

Unpushed commits, newest first:

```
b80ae64 feat: add cached matching evidence for evaluation
eab03ab feat: add deterministic synthetic evaluation dataset
f18ab70 refactor: extract reusable matching result assembly
887defd docs: define synthetic evaluation parameters
aa9b92b feat: add activity match routing and explanations
8d8c92b feat: implement pure activity matching and scoring
41d01a3 docs: define the margin when only one candidate is scored
b7eb4c4 build: add the matching dependencies and transitive pins
c76ebfc fix: declare the undeclared transitive dependencies
8ca666d docs: define the activity matching and ranking policy
```

Milestone commits before that: `29723ea` (extraction concurrency lock),
`6e1665c` (event extraction), `6371233` (progress-report import),
`02812ea` (first PostgreSQL integration tests), `c92a354` (planned-activity
import), `179c67b` (initial schema), `b999ee4` (the project contract).

---

## 6. Remaining implementation

### Not yet implemented

| Component | Status |
|---|---|
`backend/evaluation/metrics.py` | not written |
`backend/evaluation/development_search.py` | not written |
`backend/evaluation/benchmark.py` | not written |
Held-out evaluation | **must not run** until a configuration qualifies and is frozen |
Human review workflow | not started |
Approved updates and audit history | not started |
Progress summaries | not started |
FastAPI endpoints beyond `/health` | not started |
React + Vite + TypeScript frontend | not started |
End-to-end testing | not started |

### The exact next step

**Step 4 of the evaluation framework: write `backend/evaluation/metrics.py`.**

The six-step plan, approved earlier, with steps 1–3 done:

1. ~~Extract `assemble_result()`~~ — done, `f18ab70`
2. ~~`synthetic_dataset.py`~~ — done, `eab03ab`
3. ~~`evidence.py`~~ — done, `b80ae64`
4. **`metrics.py`** — denominators and statistical calculations ← next
5. `development_search.py` — all 420 configurations and the selection rule
6. `benchmark.py` — runtime and memory

`metrics.py` must implement, exactly as the contract defines them:

- **Top-1 accuracy** — positives whose leading candidate is the true
  activity, over all positives.
- **Stored-candidate recall** — positives whose true activity appears among
  the candidates retained for review, including any identifier row, over
  all positives. This is the operational acceptance criterion.
- **Scored recall at 5 and at 3** — diagnostic only.
- **Pruning loss** — positives whose truth was in the leading five but is
  not retained, over positives whose truth was in the leading five.
- **False-strong-match rate** — events presented as a single strong
  candidate whose leader is not the true activity, over **all events
  presented as a single strong candidate**. A negative presented as a strong
  candidate counts as a false strong match.
- **Claim volume guard** — at least 100 strong-match claims is a hard
  minimum, 300 the target. Below the minimum the criterion is reported
  insufficient and **fails**; it is never a pass.
- **Interval reporting** — the false-strong-match rate is reported as the
  point estimate, a **95% Wilson score interval**, and the claim
  denominator.
- **Escalation rate** — positives not presented as a single strong
  candidate, over all positives. Escalation over all events is a diagnostic,
  because a negative that escalates is behaving correctly.
- **Negative strong-claim rate** — negatives presented as a single strong
  candidate, over all negatives. Diagnostic.
- An event with no computable leading candidate still counts in the positive
  denominators as a retrieval failure; it is never excluded.
- Textless stress cases count toward the negative denominator and overall
  escalation, and are reported as a separate diagnostic subgroup.

Then step 5 applies the selection constraints — false-strong-match rate
≤ 0.05, at least 100 claims, stored-candidate recall ≥ 0.90, escalation over
positives ≤ 0.40 — and the tie-break: maximize top-1 accuracy, then lower
escalation, lower fuzzy weight, lower accept floor, lower margin, lower
candidate floor. If no configuration qualifies, report that and **do not
relax any target, and do not generate the held-out set.**

---

## 7. Important decisions

### Architectural

- **One assembly path.** `assemble_result` owns ranking, rounding,
  tie-breaking, margin, pruning and identifier retention. Evaluation caches
  only the configuration-independent component scores and reuses that
  function. A second ranking implementation would invalidate the evaluation.
- **Pure layers take values, not ORM objects.** `matching.py` consumes
  `CandidateActivity`, so scoring is testable without a database and
  database behaviour cannot reach matching decisions.
- **Lazy settings and engine.** `get_settings()` and `get_engine()` are
  cached accessors, so importing the package never reads an environment or
  opens a connection.
- **Routing is computed, not stored.** Outcome and reasons are derived from
  stored scores plus the configuration named in the row's `matcher_version`,
  which is why no migration was needed.
- **Append-only matcher registry.** Changing any weight or threshold
  requires a new version; a fingerprint test fails if a historical entry is
  edited.
- **Unavailable is not zero.** A missing top score or margin stays `None`
  and fails every threshold rather than comparing as a measured zero. A
  stored score of zero on an identifier-only row means no similarity was
  computed.

### Rejected approaches, and why

- **A unique constraint on report text** — rejected; a report may
  legitimately restate a statement, and segmentation gives identical lines
  identical text.
- **SQL-side normalization for cross-upload duplicate detection** —
  rejected; Postgres `lower()` is locale-dependent and `\s` is ASCII-only,
  so it would disagree with the Python normalizer and make the two detection
  scopes inconsistent.
- **Fitting TF-IDF once per project in evaluation** — rejected for now.
  Verified numerically identical on all 11,920 pairs, but it saves only
  0.245 s and requires reimplementing the clamp, rounding and matrix
  product. Reusing `_tfidf_scores` unchanged removes that divergence risk.
- **Attaching report observations to every event of a multi-statement
  report** — rejected; it would assert something the report does not say.
- **Stripping lettered list markers (`A)`, `a.`, `I.`)** — rejected; they
  are indistinguishable from an initial or a reference, and removing them
  destroys evidence.
- **An `evidence` JSONB column on `match_results`** — deferred; nothing
  consumes it and the contract lists no such field.

### Pitfalls

- **A test that passes for the wrong reason.** A contention test whose
  holder takes `FOR UPDATE` on a parent row blocks the child insert through
  the foreign key's own `FOR KEY SHARE` check, so it passes whether or not
  the code locks anything. Use `FOR NO KEY UPDATE`, and **always verify a
  lock test fails when the lock is removed.**
- **`candidates or DEFAULT`** silently swallows an empty list. Use
  `is None`.
- **`round()` is banker's rounding.** `round(0.5)` is `0`. The textless
  share uses `floor(0.02 * n + 0.5)` through one shared helper.
- **`tracemalloc` inflates timings** — it made evidence precomputation read
  4.606 s instead of 0.571 s. Time first, measure memory in a second pass.
- **Relative paths depend on the working directory.** Resolve from
  `__file__`, not from `.`; this already bit `config.py` and a test helper.
- **An exact match makes weighting irrelevant** — fuzzy and TF-IDF are both
  1.0, so every weighting agrees. Test weight sensitivity on a candidate
  whose components differ.
- **Private helpers are imported by evaluation** (`_fuzzy_score`,
  `_tfidf_scores`, `_input_order_key`). Renaming them breaks
  `evaluation/evidence.py`.

### Contract rules that must be preserved

`docs/project-contract.md` is authoritative and must be read in full. Its
load-bearing rules, in brief:

- An imported statement is not an approved update; no score authorizes a
  mutation; only an authorized approval workflow may apply one.
- Candidates must belong to the same project as the event.
- Rankings must be reproducible for the same inputs and configuration.
- Low-confidence or ambiguous matches must be flagged for human review, and
  every outcome still requires human approval.
- A missing or invalid candidate is never a match.
- Scores are ranking evidence, **not calibrated probabilities**.
- Thresholds and weights must be evaluated on a held-out synthetic set that
  is **not** used to tune them. Held-out results must never select a
  configuration or revise criteria, and a failing held-out run is reported
  as a failure.
- Original report text is preserved; missing values stay unknown; the system
  must not invent dates, percentages, completion claims or identifiers.
- Approved updates and their audit record commit in one transaction, or roll
  back together.
- A read followed by a conditional insert must be serialized by the
  database, and a test for such a lock must fail when the lock is removed.
- Policy is documented in the contract **before** it is implemented.

---

## 8. Instructions for the next Claude Code session

1. **Read `docs/project-contract.md` first, in full.** It is the
   authoritative specification and it is long. Do not skim it.
2. **Read this file next** for state.
3. **Inspect the actual repository before writing code.** Check file
   contents, run the tests, query migration state. Do not trust a summary,
   including this one, over the code.
4. **Never recreate completed features.** Steps 1–3 of the contract
   workflow and the pure matching layers are done and tested. Extend them;
   do not rewrite them.
5. **Never change the frozen stack or the project scope without asking.**
   No new dependency, no database change, no architectural change without
   explicit approval. `requirements.txt` is a complete freeze.
6. **Continue from the exact unfinished step:**
   `backend/evaluation/metrics.py`, as specified in section 6.
7. **Working practices this project expects:**
   - Update the contract **before** implementing a changed policy, in its
     own commit.
   - Propose a design and wait for approval before coding a milestone.
   - Report actual test output; never claim a result you did not run.
   - Distinguish verified facts from assumptions, and say plainly when
     something is unverified.
   - Do not commit without explicit approval. Do not push without it either
     — ten commits are currently unpushed by choice.
   - Never print the contents of `.env` or `.env.test`.
   - Never migrate, truncate, reset or write to the development database in
     a test.
   - Do not generate or run the held-out evaluation until a development
     configuration qualifies and is frozen under a new matcher version.

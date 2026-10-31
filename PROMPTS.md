# Prompt History & Review Log

## 2026-10-30: Stage A Fixes, Stage B1 Implementation, and Stage B1 Fixes

### Stage A Fixes
- **Prompts**:
  1. Export Stage A tables to CSV, profile rows, qualified non-manager holders, weekly supply in whole shifts (`floor(contract_hours / 7.5)`), and separate `min_headcount` vs `target_headcount` demand.
  2. Implement department-to-skills alignment, secondary-only `BILINGUAL_ES`, `SKL-ONCOLOGY` weekday coverage slot on `TEAM-002`, boundary hire/termination dates, and determinism tests.
- **Reviewed & Changed**:
  - Found the demand figure that didn't reconcile: explained how the historical nominal "50" shifts differed from granular per-skill slot sums ($30.0$ min, $60.0$ target) and corrected supply accounting to paid shifts.
  - Corrected over-common skill distribution: made `BILINGUAL_ES` strictly secondary (20–25% ratio) and established `SKL-ONCOLOGY` requirement on `TEAM-002` with 2 holders.

### Stage B1 Implementation (Leave Requests & Lifecycle Events)
- **Prompts**:
  1. Generate configured leave requests (5,000 full scale, ~100 starter) with submission-order coverage gating, `leave_coverage_buffer`, and audit event logs (`LeaveRequestEvent`).
  2. Implement lifecycle transitions, manager vs SYSTEM actor rules, statutory holiday exemption from annual leave hours, and baseline profile reporting.
- **Reviewed & Changed**:
  - Validated counterfactual coverage rejections and ensured sick leaves are exempt from coverage constraints.
  - Enforced actor governance prohibiting self-approval (`actor_id != requester`) and routing manager requests to SYSTEM or peer team managers.

### Stage B1 Fixes & Verification
- **Prompts**:
  1. Ensure consistent naive UTC datetimes with trailing `'Z'`; add static check against disallowed functions and verify identical output across `TZ="IST-5:30"` and `TZ="UTC0"`.
  2. Optimize coverage checks using precomputed `(team_id, date) -> {skill_id: min_headcount}` dictionary lookup (reducing full-scale runtime to 0.38s).
  3. Calibrate `leave_coverage_buffer` (1.4 -> 1.25) to satisfy 60–95% approval rate on starter cohort; verify literal 8.0h for 40h employees spanning statutory holidays; verify pending requests.
  4. Create shared `src/workforce_generator/pipeline.py` and `tests/conftest.py` with session fixtures and `SeedSequence.spawn()`.
  5. Pre-commit verification: verify requirements encoding, untracked directories, and run full test suite.
- **Reviewed & Changed**:
  - Found the timezone bug: eliminated all local `.astimezone()` calls and enforced UTC-naive datetime arithmetic verified via dual-hashing tests under varying `TZ` environments.
  - Identified and fixed the UTF-16 requirements file: converted `requirements.txt` from UTF-16 LE with BOM to plain ASCII/UTF-8.

---

> **Note on this log.** Entries above (Stage A to B1) were logged at the time. Entries below are reconstructed from my chat with Claude (planning/review) and from commit messages; prompts are summarised, not verbatim. See [AI_USAGE.md](AI_USAGE.md).
> Standing rules in every prompt to the coding assistant: run Python as `.\.venv\Scripts\python.exe`; commit with `git add <paths>`, never `git add -A`; do not weaken tests to get green; show at most 20 lines of output.

## 2026-10-30: Stage B2 (Scheduler, Coverage Gaps, Weekly Hours)
*(Reconstructed from commit history)*

- **Prompts**:
  1. Implement shift scheduler with hard rule enforcement and rotation (`feat(stage-b2): implement shift scheduler with hard rule enforcement and rotation`).
  2. Implement `CoverageGap` and `WeeklyHoursSummary` derived table generators (`feat(stage-b2)`).
  3. Add comprehensive unit, reconciliation, determinism, and deliberate failure tests (`test(stage-b2)`).
  4. Update data dictionary and DESIGN.md with holiday premium, gap diagnostics, and baseline invariants (`docs(stage-b2)`).
  5. Rewrite scheduler with ISO-week two-pass, add `has_gap` to `CoverageGap` derivation, and add exhaustive unavailability audit (`fix(stage-b2)`).
  6. Expose `parse_iso_utc` in `rules.py` for shared use (`refactor(stage-b2)`).
  7. Add `has_gap` invariant, brute-force unavailability, zero-hour report, and independence tests (`test(stage-b2)`).
  8. Fix stage B2 coverage invariant (`Fix stage B2 coverage invariant`).
- **Reviewed & Changed**:
  - Found that the first pass of the scheduler had coverage invariant issues; guided a rewrite using an ISO-week two-pass approach.
  - Required addition of `has_gap` boolean column on `CoverageGap` and exhaustive brute-force unavailability auditing.

## 2026-10-30: Stage C (Swaps and Attendance)
*(Reconstructed from commit history)*

- **Prompts**:
  1. Generate audited shift swaps with lifecycle transitions and eligibility checks (`feat(stage-c): generate audited shift swaps`).
  2. Simulate attendance clock events linking strictly to active assignments with variance and exception tagging (`feat(stage-c): simulate attendance events`).
  3. Rebuild derived analytics (`CoverageGap`, `WeeklyHoursSummary`) post-swap (`feat(stage-c): rebuild analytics after swaps`).
  4. Add invariant, counterexample, and determinism tests for swaps and attendance (`test(stage-c): verify swaps and attendance invariants`).
  5. Update documentation with swap and attendance mechanics (`docs(stage-c)`).
  6. Performance: cache swap eligibility indexes to accelerate search (`perf(stage-c): cache swap eligibility indexes`).
  7. Rule enforcement: reject ineligible swap requests by finding (`feat(stage-c): reject ineligible swap requests by finding`).
  8. Update docs on applied swaps and hours cap (`docs(stage-c)`).
- **Reviewed & Changed**:
  - Identified performance bottlenecks during swap matching; required eligibility caching.
  - Enforced strict rejection of ineligible swaps and post-swap analytics recomputation.

## 2026-10-30: Stage D1a (CSV Export, Five Anomaly Types, Ground Truth)
*(Reconstructed from commit history)*

- **Prompts**:
  1. Write deterministic CSV datasets with manifests and SHA-256 hashes (`feat(export): write deterministic CSV datasets and manifests`).
  2. Inject five targeted dirty-data cases (`DOUBLE_BOOKED`, `LEAVE_OVERLAP`, `SKILL_ABSENT`, `OVERTIME_CAP_EXCEEDED`, `SWAP_WITHOUT_APPROVAL`) and produce `ground_truth.csv`/`ground_truth.json` (`feat(anomalies): inject targeted dirty-data cases`).
  3. Document CSV and anomaly outputs in DESIGN.md (`docs(stage-d1a): document CSV and anomaly outputs`).
  4. Verify injected rule collateral: check that anomalies only trigger intended rules where possible (`fix(anomalies): verify injected rule collateral`).
- **Reviewed & Changed**:
  - Required verification of rule collateral on injected anomalies to avoid unexpected rule cascade.

## 2026-10-31: Stage D1b (Validator Fixes, Vectorisation, Evaluation Script, Tests)

- **Prompts**:
  1. Run validator on exported data: first validator run crashed on a merge-suffix column (`KeyError`) and existing tests had stayed green because none ran `validate.py` on exported data; fix merge-suffix column handling.
  2. Clean data violations: all 308 clean violations traced to `REQUESTED` vs `SUBMITTED` vocabulary and actor rules in validator, not bad data; fix leave status vocabulary and actor rules (requester may cancel own leave, manager of another team may decide manager's leave).
  3. Validator performance: a hang in `_check_attendance` (per-row `to_datetime`) was fixed by vectorising with pandas operations (starter runtime dropped to ~0.3 s, full runtime dropped to ~19-22 s).
  4. Injector collateral control: dirty collateral originally went from `DUPLICATE_ACTIVE_ASSIGNMENT` 6, `REST_VIOLATION` 5, `MAX_CONSECUTIVE_DAYS` 10, `DOUBLE_BOOKED` 4 to exactly 3 per injected type plus 3 expected `MAX_CONSECUTIVE_DAYS` (`also_triggers`).
  5. Precision/recall evaluation: create `scripts/evaluate_validator.py` (the only place that reads ground truth) to match violations by issue_type and record overlap; achieved 100% precision and recall (15 anomalies starter, 75 full).
  6. Test suite expansion: add `tests/test_validate.py` with counterexamples and passing twins for each rule, test that base clean dataset gives zero violations, test that `validate.py` does not import `workforce_generator`, growing test count from 73 to 93.
  7. Update DESIGN.md Section 12 with validator evaluation and test specs.
- **Reviewed & Changed**:
  - Discovered validator crash on real data; insisted on testing `validate.py` directly against exported datasets.
  - Investigated clean violations and determined generator was correct per spec; fixed validator vocabulary and actor logic.
  - Interrupted hung attendance validation and guided pandas vectorisation.
  - Enforced `also_triggers` tracking for unavoidable collateral (7-shift week breaking 6-day consecutive rule).

## 2026-10-31: Stage E (Reproduce Scripts, JSON/SQL Export, Checksums)

- **Prompts**:
  1. Add reproducibility scripts (`scripts/reproduce.ps1` and `scripts/reproduce.sh`): create venv, install requirements, run pytest, generate starter and full datasets with fixed seed `20261022`, run `validate.py` on all 4 directories, run `evaluate_validator.py`. Note that `validate.py` exits 1 on dirty by design so scripts expect that. Add byte-identical determinism test (SHA-256).
  2. Committed datasets: commit `output/starter` to git. For `output/full` (367 MB, exceeds 50 MB), commit only `output/full/SHA256SUMS` and `output/full/README.md` documenting regeneration.
  3. JSON export: generate companion `{table}.json` per table for starter and full, tracked in `manifest.json` with row counts and SHA-256; add test verifying row count parity with CSVs.
  4. SQL export: create `scripts/generate_sql.py` generating SQLite-compatible `schema.sql` (CREATE TABLE with PK and FK) and `starter_inserts.sql` (INSERT data for clean starter); verify via in-memory SQLite row count test.
- **Reviewed & Changed**:
  - Explicitly designed reproducibility scripts to handle exit code 1 from `validate.py` on dirty directories as expected behavior.
  - Executed `reproduce.ps1` end-to-end on Windows and verified git status stayed clean.
  - Acknowledged `reproduce.sh` was written but NOT run (Windows development environment).
  - Resolved nullable constraints in SQL schema for initial lifecycle state events.

## 2026-10-31: Documentation and Final Checks

- **Prompts**:
  1. Add `.gitattributes`: `* text=auto`, `*.csv -text`, `*.json -text`, `SHA256SUMS -text` so platform line endings do not alter checksums; verify starter checksums still match.
  2. Write `README.md`: purpose, requirements, install, quick start, full-scale regeneration, validation/evaluation commands, directory layout, table list with row counts, ground truth format, anomaly types and `also_triggers`, what is tested and what is not (state honestly that `reproduce.sh` was not run while `reproduce.ps1` was run), runtimes.
  3. Update `DESIGN.md` for Stage E (JSON, SQL, checksums, reproducibility) and verify every table and column name against actual CSV headers.
  4. Run fresh clone check (clone to temp directory, create venv, install requirements, run pytest); passed 93 tests.
- **Reviewed & Changed**:
  - Verified `.gitattributes` preserved binary checksum integrity on CSV and JSON files.
  - Conducted fresh clone test verifying zero repository leakage and 100% test pass rate (93 passed).

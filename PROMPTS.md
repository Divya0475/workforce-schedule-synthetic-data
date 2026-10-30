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

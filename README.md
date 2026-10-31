# Workforce Scheduling Synthetic Data Generator

A reproducible synthetic data generator (depends on pandas, numpy and pyyaml) written in Python 3.10+ simulating an enterprise Workforce Management (WFM) scheduling domain. It models capacity-feasible schedules, complex labor compliance rules, leave/swap lifecycle event logs, and attendance variances. It also features a synthetic anomaly injection engine paired with an independent validation and evaluation suite.

---

## Table of Contents
- [Purpose](#purpose)
- [System Requirements](#system-requirements)
- [Installation](#installation)
- [Quick Start (Starter Dataset)](#quick-start-starter-dataset)
- [Full-Scale Dataset Regeneration](#full-scale-dataset-regeneration)
- [Validation & Evaluation](#validation--evaluation)
- [Directory Layout](#directory-layout)
- [Tables & Starter Dataset Row Counts](#tables--starter-dataset-row-counts)
- [Ground Truth Schema & Format](#ground-truth-schema--format)
- [Anomaly Types & Collateral Rules (`also_triggers`)](#anomaly-types--collateral-rules-also_triggers)
- [Testing & Test Coverage](#testing--test-coverage)
- [Pipeline Runtimes](#pipeline-runtimes)
- [Data Provenance](#data-provenance)
- [Fairness Report](#fairness-report)
- [AI Usage](#ai-usage)

---

## Purpose

Workforce scheduling in operations-intensive domains (e.g., healthcare, logistics, continuous production) requires balancing shift coverage requirements against statutory labor law constraints:
1. **Minimum Coverage**: Ensuring sufficient headcount and critical certified skills are present for every shift.
2. **Labor Compliance**: Obeying mandatory rest intervals (minimum 11h between consecutive shifts), weekly hour limits (40h contract, 48h overtime cap), and mandatory rest days (maximum 6 consecutive working days).
3. **Leave Processing**: Evaluating leave requests against coverage requirements to prevent critical deficits.
4. **Shift Swaps**: Peer-to-peer shift swaps requiring matching qualifications, rest-interval validation, and manager approval.
5. **Operational Realities**: Simulating attendance variance (punctuality, late check-in, early departure, overtime, unexcused absence).

The generator exports:
- **Clean datasets**: 100% compliant, mathematically validated schedules with zero violations.
- **Dirty datasets**: Controlled, mathematically isolated anomalies injected into schedules for benchmarking data quality tools and compliance validators.
- **Ground Truth**: Exact records of injected anomalies with primary and unavoidable collateral violation types.
- **Multi-Format Exports**: Companion CSV, pretty-printed JSON records per table, cryptographic manifests (`manifest.json`), and SQLite DDL schema with data insert scripts.

---

## System Requirements

- **Python**: Version 3.10 or 3.11+
- **OS**: Windows (PowerShell), Linux, or macOS (POSIX shell)
- **Virtual Environment**: Standard Python `venv`
- **Dependencies**: Listed in `requirements.txt` (`pandas`, `numpy`, `pyyaml`, `pytest`, `python-dateutil`, etc.)

---

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Divya0475/workforce-schedule-synthetic-data.git
   cd workforce-schedule-synthetic-data
   ```

2. **Create and activate a virtual environment:**
   - **Windows (PowerShell):**
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\Activate.ps1
     ```
   - **Linux / macOS:**
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```

3. **Install dependencies:**
   - **Windows:**
     ```powershell
     .\.venv\Scripts\python.exe -m pip install -r requirements.txt
     ```
   - **POSIX:**
     ```bash
     ./.venv/bin/python -m pip install -r requirements.txt
     ```

---

## Quick Start (Starter Dataset)

The starter profile generates a 14-day schedule (Jan 1 – Jan 14, 2026) across 5 teams (50 employees) with 3 injected anomalies per issue type (15 anomalies total).

Pre-generated starter artifacts are committed directly in `output/starter/`. To regenerate or export to another folder:

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe run_generator.py --profile starter --out output

# Export SQLite DDL schema and starter INSERT statements
.\.venv\Scripts\python.exe scripts/generate_sql.py --out output/starter
```

### Run everything in one command

To run the complete end-to-end verification and regeneration workflow:
- **Windows**: `powershell -ExecutionPolicy Bypass -File scripts\reproduce.ps1` (run and passed)
- **POSIX**: `bash scripts/reproduce.sh` (written but not run)

Note: This takes several minutes because it also generates the full dataset.

---

## Full-Scale Dataset Regeneration

The full production profile models **2,000 employees**, **100 teams**, and a **90-day planning horizon** (Q1 2026, Jan 1 – Mar 31) with **15 injected anomalies per issue type** (75 anomalies total).

Because the full dataset is ~367 MB (exceeding GitHub's recommended repository size limits), only its cryptographic checksum file (`output/full/SHA256SUMS`) and instructions (`output/full/README.md`) are tracked in Git.

To regenerate the full profile and verify checksum integrity:
```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe run_generator.py --profile full --out output

# Verify cryptographic hashes against output/full/SHA256SUMS
.\.venv\Scripts\python.exe -c "
import hashlib
from pathlib import Path
full_dir = Path('output/full')
sums_file = full_dir / 'SHA256SUMS'
expected = dict(line.strip().split('  ', 1) for line in sums_file.read_text(encoding='utf-8').splitlines() if line.strip())
mismatches = []
for h, rel in expected.items():
    p = Path(rel)
    if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != h:
        mismatches.append(rel)
print(f'Mismatches: {len(mismatches)}')
"
```

---

## Validation & Evaluation

### 1. Independent Rule Validator (`validate.py`)
Validates against 60 distinct rule codes covering business logic, schedule feasibility, and labor compliance:
`AFTER_TERMINATION`, `ASSIGNMENT_EFFECTIVE_ORDER`, `ASSIGNMENT_TIME_ORDER`, `ATTENDANCE_DURING_APPROVED_LEAVE`, `ATTENDANCE_MISSING_EVENTS`, `ATTENDANCE_ON_SWAPPED_OUT`, `ATTENDANCE_RECONCILIATION`, `BEFORE_HIRE`, `CERTIFICATION_DATE_ORDER`, `COVERAGE_GAP_MISSING`, `COVERAGE_GAP_RECONCILIATION`, `DATE_INVALID`, `DOUBLE_BOOKED`, `DUPLICATE_ACTIVE_ASSIGNMENT`, `EMPLOYEE_DATE_ORDER`, `ENUM_INVALID`, `FOREIGN_KEY_COLUMN_MISSING`, `LEAVE_DATE_ORDER`, `LEAVE_EVENT_TIMESTAMP_ORDER`, `LEAVE_INVALID_MANAGER_ACTOR`, `LEAVE_INVALID_TRANSITION`, `LEAVE_MISSING_EVENTS`, `LEAVE_OVERLAP`, `LEAVE_REQUESTOR_ACTOR`, `LEAVE_STATUS_MISMATCH`, `LEAVE_SYSTEM_ACTOR_HAS_ID`, `MANAGER_SCHEDULED`, `MAX_CONSECUTIVE_DAYS`, `MISSING_TABLE`, `OVERTIME_CAP_EXCEEDED`, `PRIMARY_KEY_COLUMN_MISSING`, `PRIMARY_KEY_DUPLICATE`, `REST_VIOLATION`, `SKILL_ABSENT`, `SWAP_APPLIED_TIMESTAMP_MISMATCH`, `SWAP_DIFFERENT_ISO_WEEK`, `SWAP_DIFFERENT_TEAM`, `SWAP_EVENT_CHAIN`, `SWAP_EVENT_TIMESTAMP_ORDER`, `SWAP_HISTORY_INCOMPLETE`, `SWAP_INVALID_INITIAL_EVENT`, `SWAP_INVALID_MANAGER_ACTOR`, `SWAP_INVALID_TRANSITION`, `SWAP_IN_PLACE_OVERWRITE`, `SWAP_MISSING_EVENTS`, `SWAP_OLD_HISTORY_NOT_CLOSED`, `SWAP_REJECTION_WITHOUT_REASON`, `SWAP_REPLACEMENT_COUNT`, `SWAP_REPLACEMENT_INVALID`, `SWAP_REPLACEMENT_LINEAGE`, `SWAP_REQUEST_NOT_BEFORE_SHIFTS`, `SWAP_STATUS_MISMATCH`, `SWAP_STUCK_APPROVED`, `SWAP_SYSTEM_ACTOR_HAS_ID`, `SWAP_TIME_ORDER`, `SWAP_UNAPPLIED_HAS_REPLACEMENTS`, `SWAP_WITHOUT_APPROVAL`, `TIMESTAMP_INVALID`, `WEEKLY_HOURS_RECONCILIATION`, `WEEKLY_HOURS_ROW_COUNT`.
```powershell
# Clean directory: exits with code 0 (zero violations)
.\.venv\Scripts\python.exe validate.py --data-dir output/starter/clean --out output/tmp_clean_report

# Dirty directory: exits with code 1 (violations detected)
.\.venv\Scripts\python.exe validate.py --data-dir output/starter/dirty --out output/tmp_dirty_report
```

**Expected output:**
- Starter clean prints `Violations: 0` and exits `0`.
- Starter dirty prints `Violations: 18` (3 each of `DOUBLE_BOOKED`, `LEAVE_OVERLAP`, `OVERTIME_CAP_EXCEEDED`, `SKILL_ABSENT`, `SWAP_WITHOUT_APPROVAL` plus 3 `MAX_CONSECUTIVE_DAYS`) and exits `1`.

### 2. Evaluation Suite (`scripts/evaluate_validator.py`)
Matches detected rule violations against ground truth records by matching `issue_type` and overlapping `record_id` / `related_record_ids`. Evaluates Precision and Recall per type and overall:
```powershell
.\.venv\Scripts\python.exe scripts/evaluate_validator.py `
  --clean-dir output/starter/clean `
  --dirty-dir output/starter/dirty `
  --ground-truth output/starter/ground_truth/ground_truth.csv
```
**Acceptance Criteria Verified:**
- `clean` has exactly **0** violations.
- `dirty` has **100% recall** across all five injected anomaly types with zero unexpected false positives.

---

## Directory Layout

```
workforce-schedule-synthetic-data/
├── .gitattributes             # Preserves raw LF line endings on CSV, JSON, and checksum files
├── .gitignore                 # Ignores venvs, cache, and large output/full raw files
├── DESIGN.md                  # Comprehensive technical specification and design document
├── README.md                  # Project overview, quickstart, and reproducibility instructions
├── requirements.txt           # Pinned dependencies
├── pytest.ini                 # Pytest test suite configuration
├── run_generator.py           # Top-level CLI for generation pipeline
├── validate.py                # Standalone rule validator
├── config/
│   └── default.yaml           # Global generator parameters, seed, shift codes, rules
├── docs/
│   ├── data_dictionary.md     # Detailed data dictionary of all 15 entities
│   └── er_diagram.md          # Mermaid entity-relationship diagram
├── output/
│   ├── starter/               # Committed starter dataset (3 MB)
│   │   ├── clean/             # 15 CSVs + 15 JSONs (100% compliant)
│   │   ├── dirty/             # 15 CSVs + 15 JSONs (with 15 injected anomalies)
│   │   ├── ground_truth/      # ground_truth.csv & ground_truth.json
│   │   ├── validation/        # Committed validation & fairness summaries (starter only)
│   │   ├── manifest.json      # File index, row counts, and SHA-256 hashes
│   │   ├── schema.sql         # SQLite-compatible DDL schema (CREATE TABLE + PK + FK)
│   │   └── starter_inserts.sql# DML INSERT statements for clean starter dataset
│   └── full/                  # Full-scale dataset (~367 MB, regenerated locally)
│       ├── README.md          # Regeneration guide for full profile
│       └── SHA256SUMS         # Cryptographic SHA-256 reference hashes
├── scripts/
│   ├── evaluate_validator.py  # Precision/recall benchmark script
│   ├── generate_sql.py        # Generates schema.sql and starter_inserts.sql
│   ├── report_fairness.py  # Descriptive fairness and Gini coefficient report
│   ├── reproduce.ps1          # End-to-end Windows PowerShell reproducibility runner
│   └── reproduce.sh           # End-to-end POSIX Bash reproducibility runner
├── src/
│   └── workforce_generator/   # Modular generator package
│       ├── anomalies.py       # Isolated anomaly injector engine
│       ├── export.py          # CSV, JSON, and manifest exporter
│       ├── pipeline.py        # Generation pipeline coordinator
│       └── ...                # Entity, generator, and model submodules
└── tests/                     # 100 passing unit, integration, and export tests
```

---

## Tables & Starter Dataset Row Counts

The generator exports 15 relational tables (clean and dirty variants):

| Table Name | Clean Rows | Dirty Rows | Primary Key | Description |
|---|---|---|---|---|
| `skills` | 11 | 11 | `skill_id` | Operational skills and certifications catalog |
| `teams` | 5 | 5 | `team_id` | Organizational work teams and timezones |
| `holidays` | 1 | 1 | `holiday_id` | Statutory and public holiday calendar entries |
| `shift_templates` | 3 | 3 | `shift_template_id` | Shift definitions (A: Morning, B: Afternoon, C: Overnight) |
| `employees` | 50 | 50 | `employee_id` | Workforce roster, roles, contract hours, and FTE |
| `employee_skills` | 102 | 102 | `employee_skill_id` | Skill proficiencies, certifications, and validity dates |
| `coverage_requirements`| 309 | 309 | `coverage_id` | Staffing demand matrices per team, shift, and skill |
| `shift_assignments` | 333 | 369 | `assignment_id` | Operational shift schedules and employee allocations |
| `leave_requests` | 100 | 100 | `leave_request_id` | Employee leave applications and approval statuses |
| `leave_events` | 201 | 201 | `leave_event_id` | State transition audit log for leave requests |
| `swap_requests` | 6 | 9 | `swap_request_id` | Shift swap transactions between peer employees |
| `swap_events` | 13 | 19 | `swap_event_id` | State transition audit log for swap requests |
| `attendance_events` | 648 | 703 | `attendance_event_id` | Clock-in / clock-out telemetry and variance tracking |
| `coverage_gaps` | 309 | 309 | `gap_id` | Derived analytics on actual vs. target shift staffing |
| `weekly_hours_summaries`| 150 | 150 | `summary_id` | Weekly payroll hours, overtime roll-up, and cap flags |

---

## Ground Truth Schema & Format

The ground truth records every injected anomaly in both CSV (`ground_truth.csv`) and JSON (`ground_truth.json`):

| Column | Type | Description |
|---|---|---|
| `anomaly_id` | String | Unique anomaly identifier (e.g., `ANM-0001`) |
| `issue_type` | String | The targeted compliance violation type |
| `entity` | String | Target entity name (e.g., `ShiftAssignment`, `SwapRequest`) |
| `record_id` | String | Primary record ID exhibiting the anomaly |
| `related_record_ids` | JSON List | Associated record IDs (e.g., swapped assignments, week shifts) |
| `employee_id` | String | Target employee affected by the anomaly |
| `description` | String | Human-readable explanation of the injected condition |
| `also_triggers` | JSON List | Expected collateral rule violations triggered by this anomaly |

Example JSON row:
```json
{
  "anomaly_id": "ANM-0001",
  "issue_type": "OVERTIME_CAP_EXCEEDED",
  "entity": "ShiftAssignment",
  "record_id": "ASN-2026-000334",
  "related_record_ids": [
    "ASN-2026-000334", "ASN-2026-000335", "ASN-2026-000336",
    "ASN-2026-000337", "ASN-2026-000338", "ASN-2026-000339",
    "ASN-2026-000340"
  ],
  "employee_id": "EMP-000006",
  "description": "Employee has seven ACTIVE shifts in one full ISO week (52.5 paid hours).",
  "also_triggers": [
    "MAX_CONSECUTIVE_DAYS"
  ]
}
```

---

## Anomaly Types & Collateral Rules (`also_triggers`)

The anomaly injector targets 5 distinct violation classes:

1. **`DOUBLE_BOOKED`**: Injects an active shift that overlaps another active shift for the same employee. Target selection is gated by rule checks to ensure no collateral violations fire. (`also_triggers: []`)
2. **`LEAVE_OVERLAP`**: Injects an active shift directly inside an approved leave window for the employee. Targets are pre-filtered so that rest interval, overtime, and consecutive day limits remain intact. (`also_triggers: []`)
3. **`OVERTIME_CAP_EXCEEDED`**: Assigns an employee 7 shifts (52.5 paid hours) in a single ISO week, exceeding the 48-hour statutory cap. Because working 7 consecutive days in a single week mathematically violates the 6-consecutive-day limit, this rule unavoidably triggers collateral detection. (`also_triggers: ["MAX_CONSECUTIVE_DAYS"]`)
4. **`SKILL_ABSENT`**: Injects an assignment requiring a certified skill that the assigned employee does not possess on the shift date. (`also_triggers: []`)
5. **`SWAP_WITHOUT_APPROVAL`**: Mutates swap request state transitions from `REQUESTED` directly to `APPLIED` without required `APPROVED` manager events, swapping underlying assignment ownership. (`also_triggers: []`)

---

## Testing & Test Coverage

The project is governed by **100 automated unit, integration, and export tests** executed via `pytest`:
- **Generator Core & Feasibility Tests**: Verify headcount constraints, role distributions, manager assignment exclusions, and zero unassigned shifts.
- **Validator Counterexample Tests**: For every validator rule, counterexamples trigger expected violation codes, while twin compliant examples pass.
- **Base Dataset Compliance Tests**: Asserts that `clean` datasets produce zero violations under `validate.py`.
- **Anomaly Set Equivalence Tests**: Asserts that for every injected anomaly in dirty starter data, the set of detected rules equals `{issue_type} ∪ also_triggers`.
- **Export Consistency Tests**:
  - Deterministic CSV generation (identical seed produces byte-identical CSV hashes).
  - JSON exports exist for every table and match companion CSV row counts.
  - SQLite DDL schema creation and in-memory execution verify exact row count parity with CSV datasets.

### Reproducibility Script Verification Status
- **`scripts/reproduce.ps1` (Windows PowerShell)**: Run end to end on Windows; passed all tests, generation, validation, and evaluation checks cleanly.
- **`scripts/reproduce.sh` (POSIX Bash)**: Written with identical logic, but was **not run** during development (Windows environment).

To run the complete test suite:
```powershell
.\.venv\Scripts\python.exe -m pytest -q
# Expected output: 100 passed in ~20-25s
```

---

## Pipeline Runtimes

Measured on a Windows developer workstation:

| Step | Starter Profile (50 emp, 14 days) | Full Profile (2,000 emp, 90 days) |
|---|---|---|
| **Pipeline Generation** | 0.10 s | 37.32 s |
| **Anomaly Injection & Dirty Recompute** | 0.07 s | 65.58 s |
| **CSV & JSON Export** | 0.55 s | 114.34 s |
| **Total Generation Runtime** | **0.73 s** | **217.26 s (~3.6 min)** |
| **Rule Validation (`validate.py` on clean)** | 0.32 s | 26.24 s |
| **Validator Evaluation (`evaluate_validator.py`)** | 0.66 s (clean: 0.33 s, dirty: 0.33 s) | 47.21 s (clean: 24.39 s, dirty: 22.82 s) |
| **Total Test Suite (`pytest`)** | **19.01 s** (100 tests) | N/A |

Timings vary with machine load; full generation measured between about 116 s and 217 s on the same machine.

---

## Data Provenance

- **Fully synthetic data.** No external or public datasets were used. No real personal data is present.
- All employee names are randomly generated. Email addresses use the reserved example domain `@workforce.example.com` (RFC 2606).
- The generator is seeded with `20261022` for reproducibility. Exact commands:

```bash
python run_generator.py --profile starter --out output   # seed 20261022
python run_generator.py --profile full    --out output   # seed 20261022
```

- `output/starter/schema.sql` and `output/starter/starter_inserts.sql` are provided for the starter profile only; the full profile is regenerated locally (see [Full-Scale Dataset Regeneration](#full-scale-dataset-regeneration)).

---

## Fairness Report

`scripts/report_fairness.py` generates descriptive fairness metrics for non-manager employees in a clean dataset.  
**These are descriptive metrics and not a fairness guarantee.**

```bash
python scripts/report_fairness.py --data-dir output/starter/clean --out output/starter/validation
```

The committed report is at `output/starter/validation/fairness_summary.md`.

**Measured results for starter clean (39 non-manager employees):**

| Metric | Min | Max | Mean | Std | Gini |
|---|---:|---:|---:|---:|---:|
| Shift count | 1 | 12 | 8.38 | 2.36 | 0.1488 |
| Paid hours | 8.00 | 96.00 | 67.08 | 18.86 | 0.1488 |
| Night shifts | 0 | 8 | 1.59 | 2.19 | 0.6791 |
| Weekend shifts | 0 | 4 | 1.67 | 1.53 | 0.5002 |

---

## AI Usage

This project was developed with LLM assistance:
- **Planning & Review**: Claude (chat) was used to structure prompts, review test results, and plan incremental implementation stages.
- **Implementation**: VS Code coding assistant implemented code, tests, schemas, and documentation under strict procedural constraints.
- Full details on methodology, validations, and human review decisions are documented in [AI_USAGE.md](AI_USAGE.md).
- The complete prompt history across all project stages is recorded in [PROMPTS.md](PROMPTS.md).

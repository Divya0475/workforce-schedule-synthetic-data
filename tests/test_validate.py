"""
tests/test_validate.py
======================
Tests for validate.py (the standalone CSV validator).

Design constraints:
- validate.py must NOT import workforce_generator.
- validate_dataset must be deterministic.
- Hand-built counterexample datasets exercise each rule.
- The real starter clean/dirty datasets are exported to tmp_path and validated.
"""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_validate():
    """Load validate.py as a module (not a package import)."""
    spec = importlib.util.spec_from_file_location("validate", REPO_ROOT / "validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def validate():
    return _load_validate()


# ---------------------------------------------------------------------------
# Minimal header-only rows for empty tables
# ---------------------------------------------------------------------------

_EMPTY_HEADERS: dict[str, list[str]] = {
    "holidays": ["holiday_id", "calendar_date", "name", "jurisdiction", "is_statutory"],
    "employee_skills": [
        "employee_skill_id", "employee_id", "skill_id", "proficiency_level",
        "certified_date", "expiry_date", "is_primary",
    ],
    "leave_requests": [
        "leave_request_id", "employee_id", "leave_type", "start_date", "end_date",
        "requested_hours", "current_status", "submitted_at", "decided_at", "decision_reason",
    ],
    "leave_events": [
        "leave_event_id", "leave_request_id", "from_status", "to_status",
        "event_timestamp", "actor_type", "actor_id", "reason",
    ],
    "swap_requests": [
        "swap_request_id", "requestor_employee_id", "requestor_assignment_id",
        "target_employee_id", "target_assignment_id", "current_status",
        "manager_employee_id", "requested_at", "decided_at", "applied_at", "rejection_reason",
    ],
    "swap_events": [
        "swap_event_id", "swap_request_id", "from_status", "to_status",
        "event_timestamp", "actor_type", "actor_id", "notes",
    ],
    "attendance_events": [
        "attendance_event_id", "employee_id", "assignment_id", "event_type",
        "event_timestamp", "source", "variance_minutes", "worked_minutes",
        "is_exception", "exception_type",
    ],
    "coverage_gaps": [
        "gap_id", "coverage_id", "effective_date", "team_id", "shift_template_id",
        "skill_id", "required_headcount", "scheduled_headcount", "gap_headcount",
        "target_shortfall", "reason", "is_critical", "has_gap", "computed_at",
    ],
    "weekly_hours_summaries": [
        "summary_id", "employee_id", "iso_year", "iso_week", "is_partial_week",
        "contract_hours", "scheduled_hours", "regular_hours", "overtime_hours",
        "exceeds_overtime_cap", "computed_at",
    ],
}


def _base_assignment() -> dict[str, str]:
    return {
        "assignment_id": "ASN-001",
        "shift_instance_id": "SII-TEAM001-20260105-DAY",
        "employee_id": "EMP-002",
        "team_id": "TEAM-001",
        "shift_template_id": "SHF-001",
        "shift_date": "2026-01-05",
        "scheduled_start": "2026-01-05T08:00:00Z",
        "scheduled_end": "2026-01-05T16:00:00Z",
        "duration_hours": "7.5",
        "assigned_role": "STAFF",
        "assignment_status": "ACTIVE",
        "effective_from": "2026-01-01T00:00:00Z",
        "effective_to": "",
        "swap_request_id": "",
        "replaces_assignment_id": "",
        "fulfills_skill_id": "SKL-A",
        "is_holiday_premium": "false",
    }


def _base_tables() -> dict[str, list[dict[str, str]]]:
    """Return the smallest valid dataset that passes all checks."""
    from datetime import date, timedelta
    week_dates = [(date(2026, 1, 5) + timedelta(days=i)).isoformat() for i in range(7)]
    return {
        "teams": [
            {"team_id": "TEAM-001", "name": "Alpha", "department": "Ops",
             "timezone": "UTC", "is_active": "true"},
        ],
        "skills": [
            {"skill_id": "SKL-A", "code": "SKL_A", "name": "Skill A",
             "category": "Clinical", "description": ""},
        ],
        "employees": [
            {"employee_id": "EMP-001", "team_id": "TEAM-001",
             "first_name": "Alice", "last_name": "Smith",
             "email": "alice@example.com", "role": "MANAGER",
             "weekly_contract_hours": "37.5", "fte": "1.0",
             "hire_date": "2025-01-01", "termination_date": "", "is_active": "true"},
            {"employee_id": "EMP-002", "team_id": "TEAM-001",
             "first_name": "Bob", "last_name": "Jones",
             "email": "bob@example.com", "role": "STAFF",
             "weekly_contract_hours": "37.5", "fte": "1.0",
             "hire_date": "2025-01-01", "termination_date": "", "is_active": "true"},
        ],
        "employee_skills": [
            {"employee_skill_id": "ES-001", "employee_id": "EMP-002",
             "skill_id": "SKL-A", "proficiency_level": "COMPETENT",
             "certified_date": "2025-01-01", "expiry_date": "", "is_primary": "true"},
        ],
        "shift_templates": [
            {"shift_template_id": "SHF-001", "template_code": "DAY",
             "name": "Day Shift", "start_time": "08:00", "end_time": "16:00",
             "duration_hours": "7.5", "unpaid_break_minutes": "30",
             "is_overnight": "false"},
        ],
        "holidays": [],
        "coverage_requirements": [
            {"coverage_id": f"COV-{i+1:03d}", "effective_date": d,
             "team_id": "TEAM-001", "shift_template_id": "SHF-001",
             "skill_id": "SKL-A", "min_headcount": "1" if i == 0 else "0",
             "target_headcount": "1" if i == 0 else "0"}
            for i, d in enumerate(week_dates)
        ],
        "leave_requests": [],
        "leave_events": [],
        "shift_assignments": [_base_assignment()],
        "swap_requests": [],
        "swap_events": [],
        "attendance_events": [
            {"attendance_event_id": "ATT-EMP-002-20260105T080000Z", "employee_id": "EMP-002",
             "assignment_id": "ASN-001", "event_type": "CLOCK_IN",
             "event_timestamp": "2026-01-05T08:00:00Z", "source": "BIOMETRIC",
             "variance_minutes": "0", "worked_minutes": "",
             "is_exception": "false", "exception_type": ""},
            {"attendance_event_id": "ATT-EMP-002-20260105T160000Z", "employee_id": "EMP-002",
             "assignment_id": "ASN-001", "event_type": "CLOCK_OUT",
             "event_timestamp": "2026-01-05T16:00:00Z", "source": "BIOMETRIC",
             "variance_minutes": "0", "worked_minutes": "480",
             "is_exception": "false", "exception_type": ""},
        ],
        "coverage_gaps": [
            {"gap_id": f"GAP-{i+1:03d}", "coverage_id": f"COV-{i+1:03d}",
             "effective_date": d,
             "team_id": "TEAM-001", "shift_template_id": "SHF-001",
             "skill_id": "SKL-A", "required_headcount": "1" if i == 0 else "0",
             "scheduled_headcount": "1" if i == 0 else "0", "gap_headcount": "0",
             "target_shortfall": "0", "reason": "", "is_critical": "false",
             "has_gap": "false", "computed_at": "2026-01-01T00:00:00Z"}
            for i, d in enumerate(week_dates)
        ],
        "weekly_hours_summaries": [
            {"summary_id": "WHS-001", "employee_id": "EMP-001",
             "iso_year": "2026", "iso_week": "2", "is_partial_week": "false",
             "contract_hours": "37.5", "scheduled_hours": "0.0",
             "regular_hours": "0.0", "overtime_hours": "0.0",
             "exceeds_overtime_cap": "false",
             "computed_at": "2026-01-01T00:00:00Z"},
            {"summary_id": "WHS-002", "employee_id": "EMP-002",
             "iso_year": "2026", "iso_week": "2", "is_partial_week": "false",
             "contract_hours": "37.5", "scheduled_hours": "7.5",
             "regular_hours": "7.5", "overtime_hours": "0.0",
             "exceeds_overtime_cap": "false",
             "computed_at": "2026-01-01T00:00:00Z"},
        ],
    }


def _write_tables(path: Path, tables: dict[str, list[dict[str, str]]]) -> None:
    """Write all table lists as CSVs under path/."""
    path.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        fpath = path / f"{name}.csv"
        if not rows:
            headers = _EMPTY_HEADERS.get(name, [name + "_id"])
            fpath.write_text(",".join(headers) + "\n", encoding="utf-8")
        else:
            with fpath.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)


def _rules(violations: list[dict]) -> list[str]:
    return [v["rule"] for v in violations]


# ---------------------------------------------------------------------------
# 1. Import guard: validate.py must not import workforce_generator
# ---------------------------------------------------------------------------

def test_validate_imports_no_workforce_generator():
    """validate.py source must not reference workforce_generator."""
    src = (REPO_ROOT / "validate.py").read_text(encoding="utf-8")
    assert "workforce_generator" not in src, (
        "validate.py must not import workforce_generator"
    )


# ---------------------------------------------------------------------------
# 2. Starter clean/dirty: export to tmp_path and validate
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def starter_dirs(tmp_path_factory, starter_pipeline_data, starter_config):
    """Export starter clean + dirty to a fresh tmp directory."""
    from workforce_generator.anomalies import generate_dirty_dataset
    from workforce_generator.export import write_dataset

    base = tmp_path_factory.mktemp("starter_validate")
    cfg = starter_config
    clean_tables = starter_pipeline_data
    clean_export = {k: v for k, v in clean_tables.items() if k != "config"}

    streams = np.random.SeedSequence(cfg.seed).spawn(6)[5].spawn(2)
    dirty_tables, gt = generate_dirty_dataset(
        cfg,
        clean_tables,
        np.random.default_rng(streams[0]),
        np.random.default_rng(streams[1]),
        counts_per_type=3,
    )
    dirty_export = {k: v for k, v in dirty_tables.items() if k != "config"}
    write_dataset(str(base), "starter", clean_export, dirty_export, gt)
    return base / "starter"


def test_starter_clean_zero_violations(validate, starter_dirs):
    """validate_dataset on the exported clean starter must return 0 violations."""
    violations = validate.validate_dataset(str(starter_dirs / "clean"))
    assert violations == [], (
        f"Expected 0 violations, got {len(violations)}: "
        + str([v['rule'] for v in violations[:5]])
    )


def test_base_dataset_zero_violations(validate, tmp_path):
    """The base hand-built dataset must produce exactly [] violations."""
    tables = _base_tables()
    _write_tables(tmp_path / "base", tables)
    violations = validate.validate_dataset(str(tmp_path / "base"))
    assert violations == [], f"Base dataset expected [], got: {violations}"


def test_starter_dirty_has_violations(validate, starter_dirs):
    """validate_dataset on dirty starter must have >0 violations and all 5 injected types."""
    violations = validate.validate_dataset(str(starter_dirs / "dirty"))
    assert len(violations) > 0, "Dirty dataset must have at least one violation"
    rules_fired = set(_rules(violations))
    expected_five = {
        "DOUBLE_BOOKED",
        "LEAVE_OVERLAP",
        "SKILL_ABSENT",
        "OVERTIME_CAP_EXCEEDED",
        "SWAP_WITHOUT_APPROVAL",
    }
    assert expected_five <= rules_fired, (
        f"Missing expected injected anomaly types in dirty: {expected_five - rules_fired}"
    )


# ---------------------------------------------------------------------------
# 3. Determinism: same dir twice -> identical results
# ---------------------------------------------------------------------------

def test_validate_determinism(validate, starter_dirs):
    """Two calls on the same directory must produce identical sorted violations."""
    r1 = validate.validate_dataset(str(starter_dirs / "clean"))
    r2 = validate.validate_dataset(str(starter_dirs / "clean"))
    assert r1 == r2, "validate_dataset is not deterministic"


# ---------------------------------------------------------------------------
# 4. Per-rule counterexample tests
# ---------------------------------------------------------------------------

def test_enum_invalid_triggers_and_twin_passes(validate, tmp_path):
    """ENUM_INVALID fires for an unknown role; corrected dataset passes."""
    bad = _base_tables()
    bad["employees"][0]["role"] = "JANITOR"  # not in ENUMS
    _write_tables(tmp_path / "bad", bad)
    assert "ENUM_INVALID" in _rules(validate.validate_dataset(str(tmp_path / "bad")))

    good = _base_tables()
    _write_tables(tmp_path / "good", good)
    assert "ENUM_INVALID" not in _rules(validate.validate_dataset(str(tmp_path / "good")))


def test_leave_invalid_transition_triggers_and_twin_passes(validate, tmp_path):
    """LEAVE_INVALID_TRANSITION fires for SUBMITTED->SUBMITTED; valid chain passes."""
    bad = _base_tables()
    bad["leave_requests"] = [
        {"leave_request_id": "LVE-001", "employee_id": "EMP-002",
         "leave_type": "ANNUAL", "start_date": "2026-01-10", "end_date": "2026-01-10",
         "requested_hours": "7.5", "current_status": "SUBMITTED",
         "submitted_at": "2025-12-01T10:00:00Z", "decided_at": "",
         "decision_reason": ""},
    ]
    bad["leave_events"] = [
        {"leave_event_id": "EVT-001", "leave_request_id": "LVE-001",
         "from_status": "", "to_status": "SUBMITTED",
         "event_timestamp": "2025-12-01T10:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-002", "reason": ""},
        # Invalid: SUBMITTED -> SUBMITTED
        {"leave_event_id": "EVT-002", "leave_request_id": "LVE-001",
         "from_status": "SUBMITTED", "to_status": "SUBMITTED",
         "event_timestamp": "2025-12-01T11:00:00Z",
         "actor_type": "SYSTEM", "actor_id": "", "reason": ""},
    ]
    _write_tables(tmp_path / "bad_trans", bad)
    assert "LEAVE_INVALID_TRANSITION" in _rules(
        validate.validate_dataset(str(tmp_path / "bad_trans"))
    )

    good = _base_tables()
    good["leave_requests"] = [
        {"leave_request_id": "LVE-001", "employee_id": "EMP-002",
         "leave_type": "ANNUAL", "start_date": "2026-01-10", "end_date": "2026-01-10",
         "requested_hours": "7.5", "current_status": "APPROVED",
         "submitted_at": "2025-12-01T10:00:00Z",
         "decided_at": "2025-12-01T11:00:00Z", "decision_reason": ""},
    ]
    good["leave_events"] = [
        {"leave_event_id": "EVT-001", "leave_request_id": "LVE-001",
         "from_status": "", "to_status": "SUBMITTED",
         "event_timestamp": "2025-12-01T10:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-002", "reason": ""},
        {"leave_event_id": "EVT-002", "leave_request_id": "LVE-001",
         "from_status": "SUBMITTED", "to_status": "APPROVED",
         "event_timestamp": "2025-12-01T11:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-001", "reason": ""},
    ]
    _write_tables(tmp_path / "good_trans", good)
    assert "LEAVE_INVALID_TRANSITION" not in _rules(
        validate.validate_dataset(str(tmp_path / "good_trans"))
    )


def test_leave_invalid_manager_actor_triggers_and_twin_passes(validate, tmp_path):
    """LEAVE_INVALID_MANAGER_ACTOR fires when a STAFF self-approves leave."""
    bad = _base_tables()
    bad["leave_requests"] = [
        {"leave_request_id": "LVE-001", "employee_id": "EMP-002",
         "leave_type": "ANNUAL", "start_date": "2026-01-10", "end_date": "2026-01-10",
         "requested_hours": "7.5", "current_status": "APPROVED",
         "submitted_at": "2025-12-01T10:00:00Z",
         "decided_at": "2025-12-01T11:00:00Z", "decision_reason": ""},
    ]
    bad["leave_events"] = [
        {"leave_event_id": "EVT-001", "leave_request_id": "LVE-001",
         "from_status": "", "to_status": "SUBMITTED",
         "event_timestamp": "2025-12-01T10:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-002", "reason": ""},
        # EMP-002 (STAFF) approves their own leave — invalid
        {"leave_event_id": "EVT-002", "leave_request_id": "LVE-001",
         "from_status": "SUBMITTED", "to_status": "APPROVED",
         "event_timestamp": "2025-12-01T11:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-002", "reason": ""},
    ]
    _write_tables(tmp_path / "bad_actor", bad)
    assert "LEAVE_INVALID_MANAGER_ACTOR" in _rules(
        validate.validate_dataset(str(tmp_path / "bad_actor"))
    )

    # Good: manager EMP-001 approves leave for staff EMP-002
    good = _base_tables()
    good["leave_requests"] = bad["leave_requests"][:]
    good["leave_events"] = [
        {"leave_event_id": "EVT-001", "leave_request_id": "LVE-001",
         "from_status": "", "to_status": "SUBMITTED",
         "event_timestamp": "2025-12-01T10:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-002", "reason": ""},
        {"leave_event_id": "EVT-002", "leave_request_id": "LVE-001",
         "from_status": "SUBMITTED", "to_status": "APPROVED",
         "event_timestamp": "2025-12-01T11:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-001", "reason": ""},
    ]
    _write_tables(tmp_path / "good_actor", good)
    assert "LEAVE_INVALID_MANAGER_ACTOR" not in _rules(
        validate.validate_dataset(str(tmp_path / "good_actor"))
    )


def test_self_cancellation_is_valid(validate, tmp_path):
    """Requester setting CANCELLED (self-cancel) must NOT trigger LEAVE_INVALID_MANAGER_ACTOR."""
    tables = _base_tables()
    tables["leave_requests"] = [
        {"leave_request_id": "LVE-001", "employee_id": "EMP-002",
         "leave_type": "ANNUAL", "start_date": "2026-01-10", "end_date": "2026-01-10",
         "requested_hours": "7.5", "current_status": "CANCELLED",
         "submitted_at": "2025-12-01T10:00:00Z", "decided_at": "",
         "decision_reason": ""},
    ]
    tables["leave_events"] = [
        {"leave_event_id": "EVT-001", "leave_request_id": "LVE-001",
         "from_status": "", "to_status": "SUBMITTED",
         "event_timestamp": "2025-12-01T10:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-002", "reason": ""},
        # Requester self-cancels — must be valid
        {"leave_event_id": "EVT-002", "leave_request_id": "LVE-001",
         "from_status": "SUBMITTED", "to_status": "CANCELLED",
         "event_timestamp": "2025-12-01T11:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-002", "reason": ""},
    ]
    _write_tables(tmp_path / "self_cancel", tables)
    violations = validate.validate_dataset(str(tmp_path / "self_cancel"))
    assert "LEAVE_INVALID_MANAGER_ACTOR" not in _rules(violations), (
        "Self-cancellation by requester must be valid"
    )


def test_double_booked_triggers_and_twin_passes(validate, tmp_path):
    """DOUBLE_BOOKED fires when two ACTIVE shifts overlap for the same employee."""
    bad = _base_tables()
    extra = _base_assignment().copy()
    extra["assignment_id"] = "ASN-002"
    extra["shift_instance_id"] = "SII-TEAM001-20260105-DAY-DUP"
    extra["scheduled_start"] = "2026-01-05T09:00:00Z"  # overlaps 08-16
    extra["scheduled_end"] = "2026-01-05T17:00:00Z"
    extra["fulfills_skill_id"] = ""
    bad["shift_assignments"].append(extra)
    _write_tables(tmp_path / "bad_db", bad)
    assert "DOUBLE_BOOKED" in _rules(validate.validate_dataset(str(tmp_path / "bad_db")))

    good = _base_tables()
    _write_tables(tmp_path / "good_db", good)
    assert "DOUBLE_BOOKED" not in _rules(validate.validate_dataset(str(tmp_path / "good_db")))


def test_leave_overlap_triggers_and_twin_passes(validate, tmp_path):
    """LEAVE_OVERLAP fires when an ACTIVE assignment falls on an APPROVED leave date."""
    bad = _base_tables()
    bad["leave_requests"] = [
        {"leave_request_id": "LVE-001", "employee_id": "EMP-002",
         "leave_type": "ANNUAL", "start_date": "2026-01-05", "end_date": "2026-01-05",
         "requested_hours": "7.5", "current_status": "APPROVED",
         "submitted_at": "2025-12-01T10:00:00Z",
         "decided_at": "2025-12-01T11:00:00Z", "decision_reason": ""},
    ]
    bad["leave_events"] = [
        {"leave_event_id": "EVT-001", "leave_request_id": "LVE-001",
         "from_status": "", "to_status": "SUBMITTED",
         "event_timestamp": "2025-12-01T10:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-002", "reason": ""},
        {"leave_event_id": "EVT-002", "leave_request_id": "LVE-001",
         "from_status": "SUBMITTED", "to_status": "APPROVED",
         "event_timestamp": "2025-12-01T11:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-001", "reason": ""},
    ]
    _write_tables(tmp_path / "bad_lo", bad)
    assert "LEAVE_OVERLAP" in _rules(validate.validate_dataset(str(tmp_path / "bad_lo")))

    good = _base_tables()
    _write_tables(tmp_path / "good_lo", good)
    assert "LEAVE_OVERLAP" not in _rules(validate.validate_dataset(str(tmp_path / "good_lo")))


def test_skill_absent_triggers_and_twin_passes(validate, tmp_path):
    """SKILL_ABSENT fires when employee lacks certification for the skill they fulfil."""
    bad = _base_tables()
    bad["employee_skills"] = []  # remove EMP-002's SKL-A cert
    _write_tables(tmp_path / "bad_sk", bad)
    assert "SKILL_ABSENT" in _rules(validate.validate_dataset(str(tmp_path / "bad_sk")))

    good = _base_tables()
    _write_tables(tmp_path / "good_sk", good)
    assert "SKILL_ABSENT" not in _rules(validate.validate_dataset(str(tmp_path / "good_sk")))


def test_duplicate_active_assignment_triggers_and_twin_passes(validate, tmp_path):
    """DUPLICATE_ACTIVE_ASSIGNMENT fires when same employee+shift_instance has two ACTIVE rows."""
    bad = _base_tables()
    extra = _base_assignment().copy()
    extra["assignment_id"] = "ASN-002"
    # Same shift_instance_id as ASN-001 -> duplicate
    bad["shift_assignments"].append(extra)
    _write_tables(tmp_path / "bad_dup", bad)
    assert "DUPLICATE_ACTIVE_ASSIGNMENT" in _rules(
        validate.validate_dataset(str(tmp_path / "bad_dup"))
    )

    good = _base_tables()
    _write_tables(tmp_path / "good_dup", good)
    assert "DUPLICATE_ACTIVE_ASSIGNMENT" not in _rules(
        validate.validate_dataset(str(tmp_path / "good_dup"))
    )


def test_rest_violation_triggers_and_twin_passes(validate, tmp_path):
    """REST_VIOLATION fires when rest between shifts is < 11h; twin passes with >= 11h."""
    import copy
    clean = _base_tables()

    # Bad: second shift starts 4 hours after first shift ends (20:00 vs 16:00)
    bad = copy.deepcopy(clean)
    extra = copy.deepcopy(bad["shift_assignments"][0])
    extra["assignment_id"] = "ASN-002"
    extra["shift_instance_id"] = "SII-002"
    extra["shift_date"] = "2026-01-05"
    extra["scheduled_start"] = "2026-01-05T20:00:00Z"
    extra["scheduled_end"] = "2026-01-06T04:00:00Z"
    bad["shift_assignments"].append(extra)
    _write_tables(tmp_path / "bad_rest", bad)
    assert "REST_VIOLATION" in _rules(validate.validate_dataset(str(tmp_path / "bad_rest")))

    # Good twin: second shift starts 16 hours after first shift ends (08:00 next day)
    good = copy.deepcopy(clean)
    extra_good = copy.deepcopy(good["shift_assignments"][0])
    extra_good["assignment_id"] = "ASN-002"
    extra_good["shift_instance_id"] = "SII-002"
    extra_good["shift_date"] = "2026-01-06"
    extra_good["scheduled_start"] = "2026-01-06T08:00:00Z"
    extra_good["scheduled_end"] = "2026-01-06T16:00:00Z"
    good["shift_assignments"].append(extra_good)
    _write_tables(tmp_path / "good_rest", good)
    assert "REST_VIOLATION" not in _rules(validate.validate_dataset(str(tmp_path / "good_rest")))


def test_max_consecutive_days_triggers_and_twin_passes(validate, tmp_path):
    """MAX_CONSECUTIVE_DAYS fires on 7 consecutive days; twin with 6 passes."""
    import copy
    from datetime import date, timedelta
    clean = _base_tables()
    base_asn = clean["shift_assignments"][0]

    # Bad: 7 consecutive work days (Mon Jan 5 - Sun Jan 11)
    bad = copy.deepcopy(clean)
    bad["shift_assignments"] = [
        {
            **base_asn,
            "assignment_id": f"ASN-{i+1:03d}",
            "shift_instance_id": f"SII-{i+1:03d}",
            "shift_date": (date(2026, 1, 5) + timedelta(days=i)).isoformat(),
            "scheduled_start": f"{(date(2026, 1, 5) + timedelta(days=i)).isoformat()}T08:00:00Z",
            "scheduled_end": f"{(date(2026, 1, 5) + timedelta(days=i)).isoformat()}T16:00:00Z",
        }
        for i in range(7)
    ]
    _write_tables(tmp_path / "bad_consec", bad)
    assert "MAX_CONSECUTIVE_DAYS" in _rules(validate.validate_dataset(str(tmp_path / "bad_consec")))

    # Good twin: 6 consecutive work days (Mon Jan 5 - Sat Jan 10)
    good = copy.deepcopy(clean)
    good["shift_assignments"] = [
        {
            **base_asn,
            "assignment_id": f"ASN-{i+1:03d}",
            "shift_instance_id": f"SII-{i+1:03d}",
            "shift_date": (date(2026, 1, 5) + timedelta(days=i)).isoformat(),
            "scheduled_start": f"{(date(2026, 1, 5) + timedelta(days=i)).isoformat()}T08:00:00Z",
            "scheduled_end": f"{(date(2026, 1, 5) + timedelta(days=i)).isoformat()}T16:00:00Z",
        }
        for i in range(6)
    ]
    _write_tables(tmp_path / "good_consec", good)
    assert "MAX_CONSECUTIVE_DAYS" not in _rules(validate.validate_dataset(str(tmp_path / "good_consec")))


def test_overtime_cap_exceeded_triggers_and_twin_passes(validate, tmp_path):
    """OVERTIME_CAP_EXCEEDED fires when weekly hours > 48; twin with <= 48 passes."""
    import copy
    from datetime import date, timedelta
    clean = _base_tables()
    base_asn = clean["shift_assignments"][0]

    # Bad: 7 shifts of 7.5h = 52.5 hours > 48h in full ISO week
    bad = copy.deepcopy(clean)
    bad["shift_assignments"] = [
        {
            **base_asn,
            "assignment_id": f"ASN-{i+1:03d}",
            "shift_instance_id": f"SII-{i+1:03d}",
            "shift_date": (date(2026, 1, 5) + timedelta(days=i)).isoformat(),
            "scheduled_start": f"{(date(2026, 1, 5) + timedelta(days=i)).isoformat()}T08:00:00Z",
            "scheduled_end": f"{(date(2026, 1, 5) + timedelta(days=i)).isoformat()}T16:00:00Z",
        }
        for i in range(7)
    ]
    _write_tables(tmp_path / "bad_ot", bad)
    assert "OVERTIME_CAP_EXCEEDED" in _rules(validate.validate_dataset(str(tmp_path / "bad_ot")))

    # Good twin: 5 shifts of 7.5h = 37.5 hours <= 48h
    good = copy.deepcopy(clean)
    good["shift_assignments"] = [
        {
            **base_asn,
            "assignment_id": f"ASN-{i+1:03d}",
            "shift_instance_id": f"SII-{i+1:03d}",
            "shift_date": (date(2026, 1, 5) + timedelta(days=i)).isoformat(),
            "scheduled_start": f"{(date(2026, 1, 5) + timedelta(days=i)).isoformat()}T08:00:00Z",
            "scheduled_end": f"{(date(2026, 1, 5) + timedelta(days=i)).isoformat()}T16:00:00Z",
        }
        for i in range(5)
    ]
    _write_tables(tmp_path / "good_ot", good)
    assert "OVERTIME_CAP_EXCEEDED" not in _rules(validate.validate_dataset(str(tmp_path / "good_ot")))


def test_swap_without_approval_triggers_and_twin_passes(validate, tmp_path):
    """SWAP_WITHOUT_APPROVAL fires when APPLIED swap lacks an APPROVED event; twin with APPROVED passes."""
    import copy
    clean = _base_tables()
    # Add a second staff employee EMP-003 for the swap exchange
    clean["employees"].append({
        "employee_id": "EMP-003", "team_id": "TEAM-001",
        "first_name": "Charlie", "last_name": "Brown",
        "email": "charlie@example.com", "role": "STAFF",
        "weekly_contract_hours": "37.5", "fte": "1.0",
        "hire_date": "2025-01-01", "termination_date": "", "is_active": "true",
    })
    clean["employee_skills"].append({
        "employee_skill_id": "ES-002", "employee_id": "EMP-003",
        "skill_id": "SKL-A", "proficiency_level": "COMPETENT",
        "certified_date": "2025-01-01", "expiry_date": "", "is_primary": "true",
    })
    # EMP-003 original shift on Jan 6
    clean["shift_assignments"].append({
        "assignment_id": "ASN-002", "shift_instance_id": "SII-002",
        "employee_id": "EMP-003", "team_id": "TEAM-001",
        "shift_template_id": "SHF-001", "shift_date": "2026-01-06",
        "scheduled_start": "2026-01-06T08:00:00Z", "scheduled_end": "2026-01-06T16:00:00Z",
        "duration_hours": "7.5", "assigned_role": "STAFF",
        "assignment_status": "SWAPPED_OUT", "effective_from": "2026-01-01T00:00:00Z",
        "effective_to": "2026-01-04T12:00:00Z", "swap_request_id": "SWP-001",
        "replaces_assignment_id": "", "fulfills_skill_id": "SKL-A",
        "is_holiday_premium": "false",
    })
    # Update ASN-001 to SWAPPED_OUT
    clean["shift_assignments"][0]["assignment_status"] = "SWAPPED_OUT"
    clean["shift_assignments"][0]["effective_to"] = "2026-01-04T12:00:00Z"
    clean["shift_assignments"][0]["swap_request_id"] = "SWP-001"
    # Replacement assignments
    clean["shift_assignments"].extend([
        {
            "assignment_id": "ASN-003", "shift_instance_id": "SII-001",
            "employee_id": "EMP-003", "team_id": "TEAM-001",
            "shift_template_id": "SHF-001", "shift_date": "2026-01-05",
            "scheduled_start": "2026-01-05T08:00:00Z", "scheduled_end": "2026-01-05T16:00:00Z",
            "duration_hours": "7.5", "assigned_role": "STAFF",
            "assignment_status": "ACTIVE", "effective_from": "2026-01-04T12:00:00Z",
            "effective_to": "", "swap_request_id": "SWP-001",
            "replaces_assignment_id": "ASN-001", "fulfills_skill_id": "SKL-A",
            "is_holiday_premium": "false",
        },
        {
            "assignment_id": "ASN-004", "shift_instance_id": "SII-002",
            "employee_id": "EMP-002", "team_id": "TEAM-001",
            "shift_template_id": "SHF-001", "shift_date": "2026-01-06",
            "scheduled_start": "2026-01-06T08:00:00Z", "scheduled_end": "2026-01-06T16:00:00Z",
            "duration_hours": "7.5", "assigned_role": "STAFF",
            "assignment_status": "ACTIVE", "effective_from": "2026-01-04T12:00:00Z",
            "effective_to": "", "swap_request_id": "SWP-001",
            "replaces_assignment_id": "ASN-002", "fulfills_skill_id": "SKL-A",
            "is_holiday_premium": "false",
        },
    ])
    clean["swap_requests"] = [{
        "swap_request_id": "SWP-001",
        "requestor_employee_id": "EMP-002", "requestor_assignment_id": "ASN-001",
        "target_employee_id": "EMP-003", "target_assignment_id": "ASN-002",
        "current_status": "APPLIED", "manager_employee_id": "EMP-001",
        "requested_at": "2026-01-04T10:00:00Z",
        "decided_at": "2026-01-04T11:00:00Z",
        "applied_at": "2026-01-04T12:00:00Z",
        "rejection_reason": "",
    }]

    # Good twin: has APPROVED event
    good = copy.deepcopy(clean)
    good["swap_events"] = [
        {"swap_event_id": "EVT-001", "swap_request_id": "SWP-001",
         "from_status": "", "to_status": "REQUESTED",
         "event_timestamp": "2026-01-04T10:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-002", "notes": ""},
        {"swap_event_id": "EVT-002", "swap_request_id": "SWP-001",
         "from_status": "REQUESTED", "to_status": "APPROVED",
         "event_timestamp": "2026-01-04T11:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-001", "notes": ""},
        {"swap_event_id": "EVT-003", "swap_request_id": "SWP-001",
         "from_status": "APPROVED", "to_status": "APPLIED",
         "event_timestamp": "2026-01-04T12:00:00Z",
         "actor_type": "SYSTEM", "actor_id": "", "notes": ""},
    ]
    _write_tables(tmp_path / "good_swap", good)
    assert "SWAP_WITHOUT_APPROVAL" not in _rules(validate.validate_dataset(str(tmp_path / "good_swap")))

    # Bad: skips APPROVED (REQUESTED -> APPLIED)
    bad = copy.deepcopy(clean)
    bad["swap_requests"][0]["manager_employee_id"] = ""
    bad["swap_requests"][0]["decided_at"] = ""
    bad["swap_events"] = [
        {"swap_event_id": "EVT-001", "swap_request_id": "SWP-001",
         "from_status": "", "to_status": "REQUESTED",
         "event_timestamp": "2026-01-04T10:00:00Z",
         "actor_type": "EMPLOYEE", "actor_id": "EMP-002", "notes": ""},
        {"swap_event_id": "EVT-003", "swap_request_id": "SWP-001",
         "from_status": "REQUESTED", "to_status": "APPLIED",
         "event_timestamp": "2026-01-04T12:00:00Z",
         "actor_type": "SYSTEM", "actor_id": "", "notes": ""},
    ]
    _write_tables(tmp_path / "bad_swap", bad)
    assert "SWAP_WITHOUT_APPROVAL" in _rules(validate.validate_dataset(str(tmp_path / "bad_swap")))

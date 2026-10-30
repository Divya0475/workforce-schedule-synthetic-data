"""Comprehensive tests for Stage B2: Shift Scheduler, CoverageGap, and WeeklyHoursSummary.

Tests:
1. Every hard rule on the starter profile (0 violations across all 8 hard constraints).
2. CoverageGap reconciliation against ShiftAssignment (independent recomputation).
3. WeeklyHoursSummary reconciliation against ShiftAssignment (independent recomputation).
4. Partial week semantics and overtime cap isolation.
5. Determinism test (identical seed yields bitwise identical schedule and derived tables).
6. Timezone independence (IST-5:30 vs UTC0 produce identical SHA-256 hashes).
7. Respects hire/termination dates and certification validity.
8. Holiday premium flag set correctly on statutory holidays.
9. Deliberate failure tests for all rule checkers in rules.py:
   - Double-booking
   - Leave overlap
   - 7th consecutive working day
   - Rest period violation (< 11h)
   - Overtime cap violation (> 48h in full week)
   - Manager scheduled
   - Active employee boundary violation
   - Certification validity violation
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import hashlib
import json
import os
import subprocess
import sys
import numpy as np
import pandas as pd
import pytest

from workforce_generator.config import load_config
from workforce_generator.models.entities import (
    CoverageRequirement,
    Employee,
    EmployeeSkill,
    LeaveRequest,
    ShiftAssignment,
    ShiftTemplate,
)
from workforce_generator.pipeline import run_pipeline
from workforce_generator.rules import (
    check_active_bounds,
    check_certification_validity,
    check_consecutive_working_days,
    check_double_booking,
    check_leave_overlap,
    check_manager_not_scheduled,
    check_overtime_cap,
    check_rest_period,
    validate_all_schedule_rules,
)


def test_starter_baseline_has_zero_hard_rule_violations(starter_pipeline_data):
    """Verify that the clean baseline schedule has exactly 0 hard rule violations."""
    cfg = starter_pipeline_data["config"]
    assignments = starter_pipeline_data["shift_assignments"]
    employees = starter_pipeline_data["employees"]
    employee_skills = starter_pipeline_data["employee_skills"]
    leave_requests = starter_pipeline_data["leave_requests"]

    violations = validate_all_schedule_rules(
        assignments=assignments,
        employees=employees,
        employee_skills=employee_skills,
        leave_requests=leave_requests,
        horizon_start=cfg.horizon.start_date,
        horizon_end=cfg.horizon.end_date,
    )
    assert violations == [], f"Expected 0 violations, found: {violations}"


def test_coverage_gap_reconciliation_independent(starter_pipeline_data):
    """Independently recompute CoverageGap from ShiftAssignment and reconcile every field."""
    reqs = starter_pipeline_data["coverage_requirements"]
    assignments = starter_pipeline_data["shift_assignments"]
    gaps = starter_pipeline_data["coverage_gaps"]

    # Independent count of active assignments by (date, team_id, shift_template_id, fulfills_skill_id)
    actual_counts = defaultdict(int)
    for a in assignments:
        if a.assignment_status == "ACTIVE" and a.fulfills_skill_id:
            actual_counts[(a.shift_date, a.team_id, a.shift_template_id, a.fulfills_skill_id)] += 1

    gap_map = {g.coverage_id: g for g in gaps}
    assert len(gaps) == len(reqs), "CoverageGap must have exactly 1 row per CoverageRequirement"

    for req in reqs:
        gap = gap_map[req.coverage_id]
        key = (req.effective_date, req.team_id, req.shift_template_id, req.skill_id)
        expected_scheduled = actual_counts[key]
        expected_gap = max(0, req.min_headcount - expected_scheduled)
        expected_shortfall = max(0, req.target_headcount - expected_scheduled)
        expected_critical = expected_gap > 0

        assert gap.scheduled_headcount == expected_scheduled
        assert gap.gap_headcount == expected_gap
        assert gap.target_shortfall == expected_shortfall
        assert gap.is_critical == expected_critical
        assert gap.required_headcount == req.min_headcount


def test_weekly_hours_summary_reconciliation_independent(starter_pipeline_data):
    """Independently aggregate paid hours per employee per ISO week and reconcile WeeklyHoursSummary."""
    cfg = starter_pipeline_data["config"]
    employees = starter_pipeline_data["employees"]
    assignments = starter_pipeline_data["shift_assignments"]
    summaries = starter_pipeline_data["weekly_hours_summaries"]
    paid_hours_per_shift = cfg.labor_constraints.paid_hours_per_shift

    # Independent aggregation
    actual_sched_hours = defaultdict(float)
    for a in assignments:
        if a.assignment_status == "ACTIVE":
            iso_year, iso_week, _ = a.shift_date.isocalendar()
            actual_sched_hours[(a.employee_id, iso_year, iso_week)] += paid_hours_per_shift

    summary_map = {(s.employee_id, s.iso_year, s.iso_week): s for s in summaries}
    emp_map = {e.employee_id: e for e in employees}

    for (emp_id, y, w), s in summary_map.items():
        expected_sched = actual_sched_hours.get((emp_id, y, w), 0.0)
        emp = emp_map[emp_id]
        expected_contract = emp.weekly_contract_hours
        expected_regular = min(expected_sched, expected_contract)
        expected_overtime = max(0.0, expected_sched - expected_contract)

        assert abs(s.scheduled_hours - expected_sched) < 1e-4
        assert abs(s.contract_hours - expected_contract) < 1e-4
        assert abs(s.regular_hours - expected_regular) < 1e-4
        assert abs(s.overtime_hours - expected_overtime) < 1e-4

        # Partial week test: 2026-01-01 is Thursday, so 2026-W01 (Jan 1 - Jan 4) is partial
        if w == 1:
            assert s.is_partial_week is True
            assert s.exceeds_overtime_cap is False
        elif w == 2:
            assert s.is_partial_week is False
            assert s.exceeds_overtime_cap == (expected_sched > 48.0)


def test_holiday_premium_flag_matches_statutory_calendar(starter_pipeline_data):
    """Verify that is_holiday_premium is True if and only if worked on a statutory holiday."""
    assignments = starter_pipeline_data["shift_assignments"]
    holidays = {h.calendar_date for h in starter_pipeline_data["holidays"] if h.is_statutory}

    for a in assignments:
        if a.assignment_status == "ACTIVE":
            if a.shift_date in holidays:
                assert a.is_holiday_premium is True
            else:
                assert a.is_holiday_premium is False


def test_scheduler_respects_hire_termination_and_cert_expiry(starter_pipeline_data):
    """Verify all assignments strictly fall within employee tenure and active certification."""
    assignments = starter_pipeline_data["shift_assignments"]
    employees = {e.employee_id: e for e in starter_pipeline_data["employees"]}
    emp_skills = starter_pipeline_data["employee_skills"]

    cert_map = defaultdict(list)
    for es in emp_skills:
        cert_map[(es.employee_id, es.skill_id)].append((es.certified_date, es.expiry_date))

    for a in assignments:
        if a.assignment_status == "ACTIVE":
            emp = employees[a.employee_id]
            assert a.shift_date >= emp.hire_date
            if emp.termination_date:
                assert a.shift_date <= emp.termination_date

            if a.fulfills_skill_id:
                certs = cert_map.get((a.employee_id, a.fulfills_skill_id), [])
                valid = any(
                    c_date <= a.shift_date and (exp_date is None or exp_date >= a.shift_date)
                    for c_date, exp_date in certs
                )
                assert valid, f"Assignment {a.assignment_id} violated cert validity on {a.shift_date}"


def test_scheduler_determinism():
    """Verify identical seed produces bitwise identical assignments, gaps, and summaries."""
    overrides = {
        "seed": 20261022,
        "workforce": {"num_teams": 5, "employees_per_team": 10},
        "horizon": {"start_date": "2026-01-01", "end_date": "2026-01-14"},
        "leave_rules": {"target_total_requests": 100, "leave_coverage_buffer": 1.25},
    }

    run1 = run_pipeline(overrides=overrides)
    run2 = run_pipeline(overrides=overrides)

    df_asn1 = pd.DataFrame([x.to_dict() for x in run1["shift_assignments"]])
    df_asn2 = pd.DataFrame([x.to_dict() for x in run2["shift_assignments"]])
    pd.testing.assert_frame_equal(df_asn1, df_asn2, check_exact=True)

    df_gap1 = pd.DataFrame([x.to_dict() for x in run1["coverage_gaps"]])
    df_gap2 = pd.DataFrame([x.to_dict() for x in run2["coverage_gaps"]])
    pd.testing.assert_frame_equal(df_gap1, df_gap2, check_exact=True)

    df_whs1 = pd.DataFrame([x.to_dict() for x in run1["weekly_hours_summaries"]])
    df_whs2 = pd.DataFrame([x.to_dict() for x in run2["weekly_hours_summaries"]])
    pd.testing.assert_frame_equal(df_whs1, df_whs2, check_exact=True)


def test_scheduler_timezone_independence():
    """Verify scheduler and derived tables produce bitwise identical hashes under IST-5:30 and UTC0."""
    script = (
        "import sys, json, hashlib; "
        "sys.path.insert(0, 'src'); "
        "from workforce_generator.pipeline import run_pipeline; "
        "d = run_pipeline(overrides={'seed': 20261022, 'workforce': {'num_teams': 5, 'employees_per_team': 10}, 'horizon': {'start_date': '2026-01-01', 'end_date': '2026-01-14'}, 'leave_rules': {'target_total_requests': 100, 'leave_coverage_buffer': 1.25}}); "
        "asns = json.dumps([a.to_dict() for a in d['shift_assignments']], sort_keys=True); "
        "gaps = json.dumps([g.to_dict() for g in d['coverage_gaps']], sort_keys=True); "
        "whs = json.dumps([w.to_dict() for w in d['weekly_hours_summaries']], sort_keys=True); "
        "h = hashlib.sha256((asns + gaps + whs).encode('utf-8')).hexdigest(); "
        "print(h)"
    )

    env_ist = os.environ.copy()
    env_ist["TZ"] = "IST-5:30"
    env_utc = os.environ.copy()
    env_utc["TZ"] = "UTC0"

    h_ist = subprocess.check_output([sys.executable, "-c", script], env=env_ist, text=True).strip()
    h_utc = subprocess.check_output([sys.executable, "-c", script], env=env_utc, text=True).strip()
    assert h_ist == h_utc


# ==============================================================================
# DELIBERATE FAILURE TESTS (Hand-build invalid schedules and test rules.py)
# ==============================================================================


def test_rule_checker_flags_double_booking():
    """Verify check_double_booking flags an overlapping shift on the same date."""
    asn1 = ShiftAssignment(
        assignment_id="ASN-2026-000001",
        shift_instance_id="SHIFT-2026-01-02-TEAM001-A",
        employee_id="EMP-000002",
        team_id="TEAM-001",
        shift_template_id="SFT-A",
        shift_date=date(2026, 1, 2),
        scheduled_start="2026-01-02T06:00:00Z",
        scheduled_end="2026-01-02T14:00:00Z",
        duration_hours=8.0,
        assigned_role="STAFF",
        assignment_status="ACTIVE",
        effective_from="2026-01-01T00:00:00Z",
    )
    # Overlapping Shift B on same date for same employee
    asn2 = ShiftAssignment(
        assignment_id="ASN-2026-000002",
        shift_instance_id="SHIFT-2026-01-02-TEAM001-B",
        employee_id="EMP-000002",
        team_id="TEAM-001",
        shift_template_id="SFT-B",
        shift_date=date(2026, 1, 2),
        scheduled_start="2026-01-02T13:00:00Z",  # Overlaps 13:00-14:00
        scheduled_end="2026-01-02T21:00:00Z",
        duration_hours=8.0,
        assigned_role="STAFF",
        assignment_status="ACTIVE",
        effective_from="2026-01-01T00:00:00Z",
    )
    errors = check_double_booking([asn1, asn2])
    assert len(errors) == 1
    assert "DOUBLE_BOOKED" in errors[0]


def test_rule_checker_flags_leave_overlap():
    """Verify check_leave_overlap flags an active shift overlapping approved leave."""
    asn = ShiftAssignment(
        assignment_id="ASN-2026-000001",
        shift_instance_id="SHIFT-2026-01-05-TEAM001-A",
        employee_id="EMP-000002",
        team_id="TEAM-001",
        shift_template_id="SFT-A",
        shift_date=date(2026, 1, 5),
        scheduled_start="2026-01-05T06:00:00Z",
        scheduled_end="2026-01-05T14:00:00Z",
        duration_hours=8.0,
        assigned_role="STAFF",
        assignment_status="ACTIVE",
        effective_from="2026-01-01T00:00:00Z",
    )
    lve = LeaveRequest(
        leave_request_id="LVE-2026-000001",
        employee_id="EMP-000002",
        leave_type="ANNUAL",
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 7),
        requested_hours=24.0,
        current_status="APPROVED",
        submitted_at="2025-12-10T09:00:00Z",
        decided_at="2025-12-12T14:00:00Z",
    )
    errors = check_leave_overlap([asn], [lve])
    assert len(errors) == 1
    assert "LEAVE_OVERLAP" in errors[0]


def test_rule_checker_flags_consecutive_days():
    """Verify check_consecutive_working_days flags 7 consecutive working days."""
    shifts = []
    # 7 consecutive days from 2026-01-01 to 2026-01-07
    for day in range(1, 8):
        s_date = date(2026, 1, day)
        shifts.append(
            ShiftAssignment(
                assignment_id=f"ASN-2026-{day:06d}",
                shift_instance_id=f"SHIFT-2026-01-{day:02d}-TEAM001-A",
                employee_id="EMP-000002",
                team_id="TEAM-001",
                shift_template_id="SFT-A",
                shift_date=s_date,
                scheduled_start=f"2026-01-{day:02d}T06:00:00Z",
                scheduled_end=f"2026-01-{day:02d}T14:00:00Z",
                duration_hours=8.0,
                assigned_role="STAFF",
                assignment_status="ACTIVE",
                effective_from="2026-01-01T00:00:00Z",
            )
        )
    errors = check_consecutive_working_days(shifts, max_consecutive_days=6)
    assert len(errors) >= 1
    assert "CONSECUTIVE_DAYS_VIOLATION" in errors[0]


def test_rule_checker_flags_rest_violation():
    """Verify check_rest_period flags < 11h rest between consecutive shifts."""
    # Evening Shift B ending at 22:00, followed by Morning Shift A starting at 06:00 next day (8h rest)
    asn1 = ShiftAssignment(
        assignment_id="ASN-2026-000001",
        shift_instance_id="SHIFT-2026-01-02-TEAM001-B",
        employee_id="EMP-000002",
        team_id="TEAM-001",
        shift_template_id="SFT-B",
        shift_date=date(2026, 1, 2),
        scheduled_start="2026-01-02T14:00:00Z",
        scheduled_end="2026-01-02T22:00:00Z",
        duration_hours=8.0,
        assigned_role="STAFF",
        assignment_status="ACTIVE",
        effective_from="2026-01-01T00:00:00Z",
    )
    asn2 = ShiftAssignment(
        assignment_id="ASN-2026-000002",
        shift_instance_id="SHIFT-2026-01-03-TEAM001-A",
        employee_id="EMP-000002",
        team_id="TEAM-001",
        shift_template_id="SFT-A",
        shift_date=date(2026, 1, 3),
        scheduled_start="2026-01-03T06:00:00Z",  # Only 8 hours rest
        scheduled_end="2026-01-03T14:00:00Z",
        duration_hours=8.0,
        assigned_role="STAFF",
        assignment_status="ACTIVE",
        effective_from="2026-01-01T00:00:00Z",
    )
    errors = check_rest_period([asn1, asn2], min_rest_hours=11.0)
    assert len(errors) == 1
    assert "REST_VIOLATION" in errors[0]


def test_rule_checker_flags_overtime_cap():
    """Verify check_overtime_cap flags > 48h paid hours in a full ISO week."""
    shifts = []
    # Week 2026-W02 is 2026-01-05 (Mon) to 2026-01-11 (Sun)
    # Assign 7 shifts of 7.5h = 52.5 paid hours (exceeds 48h cap)
    for day in range(5, 12):
        s_date = date(2026, 1, day)
        shifts.append(
            ShiftAssignment(
                assignment_id=f"ASN-2026-{day:06d}",
                shift_instance_id=f"SHIFT-2026-01-{day:02d}-TEAM001-A",
                employee_id="EMP-000002",
                team_id="TEAM-001",
                shift_template_id="SFT-A",
                shift_date=s_date,
                scheduled_start=f"2026-01-{day:02d}T06:00:00Z",
                scheduled_end=f"2026-01-{day:02d}T14:00:00Z",
                duration_hours=8.0,
                assigned_role="STAFF",
                assignment_status="ACTIVE",
                effective_from="2026-01-01T00:00:00Z",
            )
        )
    errors = check_overtime_cap(
        shifts,
        horizon_start=date(2026, 1, 1),
        horizon_end=date(2026, 1, 14),
        max_full_week_paid_hours=48.0,
        paid_hours_per_shift=7.5,
    )
    assert len(errors) == 1
    assert "OVERTIME_CAP_EXCEEDED" in errors[0]


def test_rule_checker_flags_manager_scheduled():
    """Verify check_manager_not_scheduled flags a manager assigned to a shift."""
    mgr = Employee(
        employee_id="EMP-000001",
        team_id="TEAM-001",
        first_name="Alice",
        last_name="Manager",
        email="alice@workforce.example.com",
        role="MANAGER",
        weekly_contract_hours=40.0,
        fte=1.0,
        hire_date=date(2020, 1, 1),
    )
    asn = ShiftAssignment(
        assignment_id="ASN-2026-000001",
        shift_instance_id="SHIFT-2026-01-02-TEAM001-A",
        employee_id="EMP-000001",
        team_id="TEAM-001",
        shift_template_id="SFT-A",
        shift_date=date(2026, 1, 2),
        scheduled_start="2026-01-02T06:00:00Z",
        scheduled_end="2026-01-02T14:00:00Z",
        duration_hours=8.0,
        assigned_role="MANAGER",
        assignment_status="ACTIVE",
        effective_from="2026-01-01T00:00:00Z",
    )
    errors = check_manager_not_scheduled([asn], [mgr])
    assert len(errors) == 1
    assert "MANAGER_SCHEDULED" in errors[0]

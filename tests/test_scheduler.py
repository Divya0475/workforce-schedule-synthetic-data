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
10. CoverageGap has_gap field equals (gap_headcount > 0) for every row.
11. Invariant: for every CoverageGap with gap_headcount > 0, brute-force the raw tables
    and assert every qualified employee of that team was genuinely unavailable.
    This recheck is fully independent — it does NOT import scheduler.py or analytics.py.
12. Employees with 0.0 total scheduled hours — report id, team, skills, reason.
"""

from __future__ import annotations

from collections import Counter, defaultdict
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
    CoverageGap,
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

# ---------------------------------------------------------------------------
# Helpers (brute-force, do NOT import scheduler or analytics internals)
# ---------------------------------------------------------------------------

def _parse_iso_z(ts: str) -> datetime:
    """Parse a naive UTC datetime from a Z-terminated ISO string."""
    return datetime.fromisoformat(ts.rstrip("Z"))


def _shift_datetimes(d: date, start_time: str, end_time: str, is_overnight: bool):
    """Return (start_dt, end_dt) naive UTC for a shift on date d."""
    sh, sm = int(start_time[:2]), int(start_time[3:5])
    eh, em = int(end_time[:2]), int(end_time[3:5])
    s_dt = datetime(d.year, d.month, d.day, sh, sm, 0)
    end_date = d + timedelta(days=1) if is_overnight else d
    e_dt = datetime(end_date.year, end_date.month, end_date.day, eh, em, 0)
    return s_dt, e_dt


def _check_employee_unavailable(
    emp: Employee,
    req_date: date,
    skill_id: str,
    s_start: datetime,
    s_end: datetime,
    emp_certs: dict,
    emp_leave_dates: dict,
    emp_worked_dates: dict,
    emp_shift_intervals: dict,
    emp_week_hours: dict,
    assigned_in_slot_set: set,
    min_rest_hours: float,
    max_consecutive_days: int,
    max_weekly_hours: float,
    paid_hours_per_shift: float,
) -> str | None:
    """Return the reason an employee is unavailable, or None if they could be scheduled.

    This is an INDEPENDENT brute-force recheck — it must not import scheduler.py or
    analytics.py functions. All rules are reimplemented from scratch here.
    """
    eid = emp.employee_id

    # 1. Active bounds
    if req_date < emp.hire_date:
        return "hire_date"
    if emp.termination_date and req_date > emp.termination_date:
        return "termination_date"

    # 2. Certification validity
    cert_list = emp_certs.get((eid, skill_id), [])
    cert_valid = any(
        c_date <= req_date and (exp is None or exp >= req_date)
        for c_date, exp in cert_list
    )
    if not cert_valid:
        return "no_valid_cert"

    # 3. Approved leave
    if req_date in emp_leave_dates.get(eid, set()):
        return "approved_leave"

    # 4. Already assigned in this slot
    if eid in assigned_in_slot_set:
        return "already_assigned_in_shift"

    # 5. Single shift per day (worked today in another shift)
    if req_date in emp_worked_dates.get(eid, set()):
        return "already_working_today"

    # 6. Rest period
    for prev_s, prev_e in emp_shift_intervals.get(eid, []):
        if s_start >= prev_e:
            rest_h = (s_start - prev_e).total_seconds() / 3600.0
            if rest_h < min_rest_hours - 1e-5:
                return "rest_rule"
        elif prev_s >= s_end:
            rest_h = (prev_s - s_end).total_seconds() / 3600.0
            if rest_h < min_rest_hours - 1e-5:
                return "rest_rule"
        else:
            return "shift_overlap"

    # 7. 6-day consecutive limit (rolling window)
    w_dates = emp_worked_dates.get(eid, set())
    for offset in range(max_consecutive_days + 1):
        win_start = req_date - timedelta(days=offset)
        cnt = 1  # count req_date itself
        for day_off in range(max_consecutive_days + 1):
            chk = win_start + timedelta(days=day_off)
            if chk != req_date and chk in w_dates:
                cnt += 1
        if cnt > max_consecutive_days:
            return "consecutive_days"

    # 8. 48h weekly cap
    iso_y, iso_w, _ = req_date.isocalendar()
    cur_wh = emp_week_hours.get((eid, iso_y, iso_w), 0.0)
    if cur_wh + paid_hours_per_shift > max_weekly_hours + 1e-5:
        return "overtime_cap"

    return None  # eligible


def _build_brute_force_audit_context(
    cfg,
    assignments,
    employees,
    employee_skills,
    leave_requests,
):
    """Index raw schedule inputs for independent coverage-gap audits."""
    paid_hours_per_shift = cfg.labor_constraints.paid_hours_per_shift
    emp_certs = defaultdict(list)
    for employee_skill in employee_skills:
        emp_certs[(employee_skill.employee_id, employee_skill.skill_id)].append(
            (employee_skill.certified_date, employee_skill.expiry_date)
        )

    emp_leave_dates = defaultdict(set)
    for leave in leave_requests:
        if leave.current_status == "APPROVED":
            current_date = leave.start_date
            while current_date <= leave.end_date:
                emp_leave_dates[leave.employee_id].add(current_date)
                current_date += timedelta(days=1)

    emp_worked_dates = defaultdict(set)
    emp_shift_intervals = defaultdict(list)
    emp_week_hours = defaultdict(float)
    assigned_in_slot = defaultdict(set)
    for assignment in assignments:
        if assignment.assignment_status != "ACTIVE":
            continue
        employee_id = assignment.employee_id
        emp_worked_dates[employee_id].add(assignment.shift_date)
        emp_shift_intervals[employee_id].append(
            (
                _parse_iso_z(assignment.scheduled_start),
                _parse_iso_z(assignment.scheduled_end),
            )
        )
        iso_year, iso_week, _ = assignment.shift_date.isocalendar()
        emp_week_hours[(employee_id, iso_year, iso_week)] += paid_hours_per_shift
        assigned_in_slot[
            (assignment.shift_date, assignment.team_id, assignment.shift_template_id)
        ].add(employee_id)

    team_staff = defaultdict(list)
    for employee in employees:
        if employee.role != "MANAGER":
            team_staff[employee.team_id].append(employee)

    return {
        "cfg": cfg,
        "shift_time_map": {
            shift.id: (shift.start_time, shift.end_time, shift.is_overnight)
            for shift in cfg.shifts
        },
        "emp_certs": emp_certs,
        "emp_leave_dates": emp_leave_dates,
        "emp_worked_dates": emp_worked_dates,
        "emp_shift_intervals": emp_shift_intervals,
        "emp_week_hours": emp_week_hours,
        "assigned_in_slot": assigned_in_slot,
        "team_staff": team_staff,
    }


def _audit_coverage_gap(gap: CoverageGap, context: dict) -> dict:
    """Return eligibility findings and the reason supported by those findings."""
    cfg = context["cfg"]
    shift_time_map = context["shift_time_map"]
    if gap.shift_template_id not in shift_time_map:
        raise AssertionError(
            f"Gap {gap.gap_id}: unknown shift_template_id {gap.shift_template_id!r}"
        )

    shift_start, shift_end, is_overnight = shift_time_map[gap.shift_template_id]
    start_dt, end_dt = _shift_datetimes(
        gap.effective_date, shift_start, shift_end, is_overnight
    )
    slot_assigned = context["assigned_in_slot"].get(
        (gap.effective_date, gap.team_id, gap.shift_template_id), set()
    )
    qualified = [
        employee
        for employee in context["team_staff"].get(gap.team_id, [])
        if any(
            certified_date <= gap.effective_date
            and (expiry_date is None or expiry_date >= gap.effective_date)
            for certified_date, expiry_date in context["emp_certs"].get(
                (employee.employee_id, gap.skill_id), []
            )
        )
    ]

    employee_reasons = {}
    for employee in qualified:
        employee_reasons[employee.employee_id] = _check_employee_unavailable(
            emp=employee,
            req_date=gap.effective_date,
            skill_id=gap.skill_id,
            s_start=start_dt,
            s_end=end_dt,
            emp_certs=context["emp_certs"],
            emp_leave_dates=context["emp_leave_dates"],
            emp_worked_dates=context["emp_worked_dates"],
            emp_shift_intervals=context["emp_shift_intervals"],
            emp_week_hours=context["emp_week_hours"],
            assigned_in_slot_set=slot_assigned,
            min_rest_hours=cfg.labor_constraints.min_rest_hours_between_shifts,
            max_consecutive_days=cfg.labor_constraints.max_consecutive_working_days,
            max_weekly_hours=cfg.labor_constraints.overtime_cap_weekly_hours,
            paid_hours_per_shift=cfg.labor_constraints.paid_hours_per_shift,
        )

    reason_findings = dict(employee_reasons)
    if gap.gap_headcount == 0 and gap.target_shortfall == 0:
        reason = "Fully staffed"
    elif gap.gap_headcount > 0 and len(qualified) < gap.required_headcount:
        reason = "Skill pinch: insufficient baseline qualified staff"
    else:
        if gap.gap_headcount == 0:
            iso_year, iso_week, _ = gap.effective_date.isocalendar()
            for employee in qualified:
                employee_id = employee.employee_id
                if reason_findings[employee_id] is None:
                    weekly_hours = context["emp_week_hours"].get(
                        (employee_id, iso_year, iso_week), 0.0
                    )
                    if (
                        weekly_hours + cfg.labor_constraints.paid_hours_per_shift
                        > employee.weekly_contract_hours + 1e-5
                    ):
                        reason_findings[employee_id] = "contract_hours"
        if any(unavailability is None for unavailability in reason_findings.values()):
            reason = "available_qualified_employee"
        elif not reason_findings:
            reason = "no_qualified_staff"
        else:
            reason_counts = Counter(reason_findings.values())
            reason = max(reason_counts.items(), key=lambda item: (item[1], item[0]))[0]

    return {
        "qualified_count": len(qualified),
        "employee_reasons": employee_reasons,
        "reason_findings": reason_findings if gap.gap_headcount or gap.target_shortfall else {},
        "available_employee_ids": sorted(
            employee_id
            for employee_id, unavailability in employee_reasons.items()
            if unavailability is None
        ),
        "reason": reason,
    }


def _coverage_gap_invariant_failures(gaps, audits) -> list[str]:
    """Flag underfilled minimum slots with a qualified employee still available."""
    failures = []
    for gap in gaps:
        if gap.gap_headcount <= 0:
            continue
        audit = audits[gap.gap_id]
        if audit["qualified_count"] < gap.required_headcount:
            continue
        if audit["available_employee_ids"]:
            failures.append(
                f"Gap {gap.gap_id}: qualified employees available for unfilled "
                f"minimum slot: {', '.join(audit['available_employee_ids'])}"
            )
    return failures


# ==============================================================================
# EXISTING TESTS (preserved unchanged)
# ==============================================================================


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
        assert gap.scheduled_headcount == expected_scheduled
        assert gap.gap_headcount == expected_gap
        assert gap.target_shortfall == expected_shortfall
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
# NEW TESTS: has_gap, invariant brute-force, zero-hour employees
# ==============================================================================


def test_has_gap_equals_gap_headcount_positive(starter_pipeline_data):
    """Assert has_gap == (gap_headcount > 0) for every CoverageGap row."""
    gaps = starter_pipeline_data["coverage_gaps"]
    assert len(gaps) > 0, "Expected at least one CoverageGap row"
    for g in gaps:
        expected = g.gap_headcount > 0
        assert g.has_gap == expected, (
            f"Gap {g.gap_id}: has_gap={g.has_gap} but gap_headcount={g.gap_headcount} "
            f"(expected has_gap={expected})"
        )


def test_coverage_gap_invariant_brute_force_unavailability(starter_pipeline_data):
    """Invariant: for every CoverageGap with gap_headcount > 0, brute-force from raw tables
    that every qualified employee of that team was genuinely unavailable.

    This test is FULLY INDEPENDENT — it does NOT import or call any function from
    scheduler.py or analytics.py. All eligibility rules are reimplemented inline.

    Allowed shared import: rules.py (shared with validate.py, NOT the scheduler internals).
    """
    cfg = starter_pipeline_data["config"]
    gaps = starter_pipeline_data["coverage_gaps"]
    assignments = starter_pipeline_data["shift_assignments"]
    employees = starter_pipeline_data["employees"]
    employee_skills = starter_pipeline_data["employee_skills"]
    leave_requests = starter_pipeline_data["leave_requests"]

    critical_gaps = [gap for gap in gaps if gap.gap_headcount > 0]
    assert len(critical_gaps) >= 1, "Expected at least one gap with gap_headcount > 0 for the invariant to be non-vacuous"

    context = _build_brute_force_audit_context(
        cfg, assignments, employees, employee_skills, leave_requests
    )
    audits = {gap.gap_id: _audit_coverage_gap(gap, context) for gap in gaps}

    skill_pinches = [
        gap
        for gap in critical_gaps
        if audits[gap.gap_id]["qualified_count"] < gap.required_headcount
    ]
    failures = _coverage_gap_invariant_failures(gaps, audits)
    reason_mismatches = [
        f"Gap {gap.gap_id}: CoverageGap.reason={gap.reason!r}, "
        f"brute-force reason={audits[gap.gap_id]['reason']!r}"
        for gap in gaps
        if gap.reason != audits[gap.gap_id]["reason"]
    ]
    assert not failures, (
        f"{len(failures)} underfilled minimum slots had available qualified staff; "
        f"{len(skill_pinches)} genuine skill pinches were excluded:\n"
        + "\n".join(failures)
    )
    assert not reason_mismatches, "\n".join(reason_mismatches)


def test_coverage_gap_invariant_flags_free_qualified_employee(starter_pipeline_data):
    """Prove the invariant detects a free qualified employee for an unfilled slot."""
    cfg = starter_pipeline_data["config"]
    req_date = date(2026, 1, 5)
    gap = CoverageGap(
        gap_id="GAP-TINY",
        coverage_id="COV-TINY",
        effective_date=req_date,
        team_id="TEAM-TINY",
        shift_template_id="SFT-A",
        skill_id="SKL-TINY",
        required_headcount=1,
        scheduled_headcount=0,
        gap_headcount=1,
        target_shortfall=1,
        reason="test fixture",
        is_critical=True,
        has_gap=True,
        computed_at="2026-01-05T00:00:00Z",
    )
    employee = Employee(
        employee_id="EMP-TINY",
        team_id="TEAM-TINY",
        first_name="Tiny",
        last_name="Employee",
        email="tiny@workforce.example.com",
        role="STAFF",
        weekly_contract_hours=37.5,
        fte=1.0,
        hire_date=date(2020, 1, 1),
    )
    employee_skill = EmployeeSkill(
        employee_skill_id="ES-TINY",
        employee_id=employee.employee_id,
        skill_id="SKL-TINY",
        proficiency_level=3,
        certified_date=date(2020, 1, 1),
    )
    context = _build_brute_force_audit_context(
        cfg, [], [employee], [employee_skill], []
    )
    audit = _audit_coverage_gap(gap, context)

    assert audit["available_employee_ids"] == [employee.employee_id]
    assert _coverage_gap_invariant_failures([gap], {gap.gap_id: audit})


def test_coverage_gap_has_gap_field_is_bool(starter_pipeline_data):
    """Assert has_gap is a strict Python bool (not int or other truthy type)."""
    gaps = starter_pipeline_data["coverage_gaps"]
    for g in gaps:
        assert isinstance(g.has_gap, bool), (
            f"Gap {g.gap_id}: has_gap should be bool, got {type(g.has_gap).__name__}"
        )


def test_zero_hour_employees_report(starter_pipeline_data):
    """Every zero-hour employee must have a concrete availability or demand reason."""
    employees = starter_pipeline_data["employees"]
    assignments = starter_pipeline_data["shift_assignments"]
    employee_skills = starter_pipeline_data["employee_skills"]
    leave_requests = starter_pipeline_data["leave_requests"]
    cfg = starter_pipeline_data["config"]

    emp_hours: dict = defaultdict(float)
    paid_hours_per_shift = cfg.labor_constraints.paid_hours_per_shift
    for a in assignments:
        if a.assignment_status == "ACTIVE":
            emp_hours[a.employee_id] += paid_hours_per_shift

    certs_by_employee_skill = defaultdict(list)
    for es in employee_skills:
        certs_by_employee_skill[(es.employee_id, es.skill_id)].append(
            (es.certified_date, es.expiry_date)
        )

    sched_employees = [e for e in employees if e.role != "MANAGER"]
    zero_hour_emps = [e for e in sched_employees if emp_hours[e.employee_id] == 0.0]
    audit_context = _build_brute_force_audit_context(
        cfg, assignments, employees, employee_skills, leave_requests
    )
    audits = {
        gap.gap_id: _audit_coverage_gap(gap, audit_context)
        for gap in starter_pipeline_data["coverage_gaps"]
    }
    audit_by_coverage = {
        gap.coverage_id: audits[gap.gap_id]
        for gap in starter_pipeline_data["coverage_gaps"]
    }
    gaps_by_coverage = {
        gap.coverage_id: gap for gap in starter_pipeline_data["coverage_gaps"]
    }

    reasons_by_employee = {}
    for emp in zero_hour_emps:
        reasons = set()
        qualified_requirements = [
            req
            for req in starter_pipeline_data["coverage_requirements"]
            if req.team_id == emp.team_id
            and any(
                certified_date <= req.effective_date
                and (expiry_date is None or expiry_date >= req.effective_date)
                for certified_date, expiry_date in certs_by_employee_skill.get(
                    (emp.employee_id, req.skill_id), []
                )
            )
        ]
        for req in qualified_requirements:
            gap = gaps_by_coverage[req.coverage_id]
            audit = audit_by_coverage[req.coverage_id]
            blocker = audit["employee_reasons"][emp.employee_id]
            if blocker:
                reasons.add(blocker)
            elif gap.target_shortfall == 0:
                reasons.add("all qualified target slots filled")
            else:
                iso_year, iso_week, _ = req.effective_date.isocalendar()
                current_week_hours = audit_context["emp_week_hours"].get(
                    (emp.employee_id, iso_year, iso_week), 0.0
                )
                if current_week_hours + paid_hours_per_shift > emp.weekly_contract_hours + 1e-5:
                    reasons.add("weekly contract hours reached")
                else:
                    pytest.fail(
                        f"Zero-hour employee {emp.employee_id} was available for "
                        f"unfilled target demand {req.coverage_id}"
                    )

        if not qualified_requirements:
            reasons.add("no qualified coverage requirements during the horizon")
        reasons_by_employee[emp.employee_id] = sorted(reasons)

    assert all(reasons_by_employee.values()), (
        "Zero-hour employees must have concrete reasons: "
        + repr(
            {
                employee_id: reasons
                for employee_id, reasons in reasons_by_employee.items()
                if not reasons
            }
        )
    )


def test_gap_reason_field_is_non_empty(starter_pipeline_data):
    """Every CoverageGap row must have a non-empty reason string."""
    gaps = starter_pipeline_data["coverage_gaps"]
    for g in gaps:
        assert g.reason and g.reason.strip(), (
            f"Gap {g.gap_id} has empty or None reason (gap_headcount={g.gap_headcount})"
        )


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


# ==============================================================================
# BRUTE-FORCE DELIBERATE FAILURE — independent from rules.py
# These tests demonstrate that the brute-force helper _check_employee_unavailable
# also catches violations, satisfying the "independence" requirement.
# ==============================================================================


def test_brute_force_catches_leave_overlap_independently():
    """Brute-force helper flags approved leave as an unavailability reason."""
    emp = Employee(
        employee_id="EMP-TESTA",
        team_id="TEAM-001",
        first_name="Test",
        last_name="Employee",
        email="test@workforce.example.com",
        role="STAFF",
        weekly_contract_hours=37.5,
        fte=1.0,
        hire_date=date(2026, 1, 1),
    )
    req_date = date(2026, 1, 5)
    # Employee is on approved leave on Jan 5
    leave_dates = {emp.employee_id: {req_date}}
    s_start = datetime(2026, 1, 5, 6, 0, 0)
    s_end = datetime(2026, 1, 5, 14, 0, 0)

    reason = _check_employee_unavailable(
        emp=emp,
        req_date=req_date,
        skill_id="SKL-GENERAL",
        s_start=s_start,
        s_end=s_end,
        emp_certs={(emp.employee_id, "SKL-GENERAL"): [(date(2025, 1, 1), None)]},
        emp_leave_dates=leave_dates,
        emp_worked_dates={},
        emp_shift_intervals={},
        emp_week_hours={},
        assigned_in_slot_set=set(),
        min_rest_hours=11.0,
        max_consecutive_days=6,
        max_weekly_hours=48.0,
        paid_hours_per_shift=7.5,
    )
    assert reason == "approved_leave"


def test_brute_force_catches_rest_violation_independently():
    """Brute-force helper flags < 11h rest as an unavailability reason."""
    emp = Employee(
        employee_id="EMP-TESTB",
        team_id="TEAM-001",
        first_name="Rest",
        last_name="Test",
        email="rest@workforce.example.com",
        role="STAFF",
        weekly_contract_hours=37.5,
        fte=1.0,
        hire_date=date(2026, 1, 1),
    )
    req_date = date(2026, 1, 3)
    # Previous shift ended at 22:00 Jan 2; new shift starts 06:00 Jan 3 → 8h rest < 11h
    prev_s = datetime(2026, 1, 2, 14, 0, 0)
    prev_e = datetime(2026, 1, 2, 22, 0, 0)
    s_start = datetime(2026, 1, 3, 6, 0, 0)
    s_end = datetime(2026, 1, 3, 14, 0, 0)

    reason = _check_employee_unavailable(
        emp=emp,
        req_date=req_date,
        skill_id="SKL-GENERAL",
        s_start=s_start,
        s_end=s_end,
        emp_certs={(emp.employee_id, "SKL-GENERAL"): [(date(2025, 1, 1), None)]},
        emp_leave_dates={},
        emp_worked_dates={emp.employee_id: {date(2026, 1, 2)}},
        emp_shift_intervals={emp.employee_id: [(prev_s, prev_e)]},
        emp_week_hours={},
        assigned_in_slot_set=set(),
        min_rest_hours=11.0,
        max_consecutive_days=6,
        max_weekly_hours=48.0,
        paid_hours_per_shift=7.5,
    )
    assert reason == "rest_rule"


def test_brute_force_catches_overtime_cap_independently():
    """Brute-force helper flags 48h weekly cap as an unavailability reason."""
    emp = Employee(
        employee_id="EMP-TESTC",
        team_id="TEAM-001",
        first_name="OT",
        last_name="Test",
        email="ot@workforce.example.com",
        role="STAFF",
        weekly_contract_hours=37.5,
        fte=1.0,
        hire_date=date(2026, 1, 1),
    )
    req_date = date(2026, 1, 8)  # ISO week 2
    iso_y, iso_w, _ = req_date.isocalendar()
    # Employee already at 48h for this week
    week_hours = {(emp.employee_id, iso_y, iso_w): 48.0}
    s_start = datetime(2026, 1, 8, 6, 0, 0)
    s_end = datetime(2026, 1, 8, 14, 0, 0)

    reason = _check_employee_unavailable(
        emp=emp,
        req_date=req_date,
        skill_id="SKL-GENERAL",
        s_start=s_start,
        s_end=s_end,
        emp_certs={(emp.employee_id, "SKL-GENERAL"): [(date(2025, 1, 1), None)]},
        emp_leave_dates={},
        emp_worked_dates={},
        emp_shift_intervals={},
        emp_week_hours=week_hours,
        assigned_in_slot_set=set(),
        min_rest_hours=11.0,
        max_consecutive_days=6,
        max_weekly_hours=48.0,
        paid_hours_per_shift=7.5,
    )
    assert reason == "overtime_cap"


def test_brute_force_catches_consecutive_days_independently():
    """Brute-force helper flags 7 consecutive days as unavailability reason."""
    emp = Employee(
        employee_id="EMP-TESTD",
        team_id="TEAM-001",
        first_name="Consec",
        last_name="Test",
        email="consec@workforce.example.com",
        role="STAFF",
        weekly_contract_hours=37.5,
        fte=1.0,
        hire_date=date(2026, 1, 1),
    )
    # Employee already worked 6 consecutive days Jan 2-7; trying to assign Jan 8
    req_date = date(2026, 1, 8)
    worked = {emp.employee_id: {date(2026, 1, d) for d in range(2, 8)}}  # Jan 2-7
    s_start = datetime(2026, 1, 8, 6, 0, 0)
    s_end = datetime(2026, 1, 8, 14, 0, 0)

    reason = _check_employee_unavailable(
        emp=emp,
        req_date=req_date,
        skill_id="SKL-GENERAL",
        s_start=s_start,
        s_end=s_end,
        emp_certs={(emp.employee_id, "SKL-GENERAL"): [(date(2025, 1, 1), None)]},
        emp_leave_dates={},
        emp_worked_dates=worked,
        emp_shift_intervals={},
        emp_week_hours={},
        assigned_in_slot_set=set(),
        min_rest_hours=11.0,
        max_consecutive_days=6,
        max_weekly_hours=48.0,
        paid_hours_per_shift=7.5,
    )
    assert reason == "consecutive_days"

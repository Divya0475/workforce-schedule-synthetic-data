"""Domain rules and constraint verification functions for workforce scheduling.

Reusable by the scheduler, unit tests, and the independent validator (validate.py).
Evaluates hard scheduling constraints:
1. Double-booking (overlapping shifts for the same employee)
2. Leave overlap (active shift overlapping approved leave)
3. Skill qualification & certification validity
4. Rest period compliance (>= 11.0h between consecutive shifts)
5. Maximum consecutive working days (<= 6 days in any rolling 7-day window)
6. Statutory overtime ceiling (<= 48.0 paid hours per full ISO week)
7. Manager exclusion (managers are never scheduled on operational shifts)
8. Active employee bounds (shift date within hire_date and termination_date)
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from workforce_generator.models.entities import (
    Employee,
    EmployeeSkill,
    LeaveRequest,
    ShiftAssignment,
    ShiftTemplate,
)


def get_shift_datetimes(
    shift_date: date,
    shift_template: ShiftTemplate,
) -> Tuple[datetime, datetime]:
    """Return naive UTC datetimes for scheduled start and end of a shift."""
    start_time_parts = [int(p) for p in shift_template.start_time.split(":")]
    end_time_parts = [int(p) for p in shift_template.end_time.split(":")]

    dt_start = datetime.combine(
        shift_date,
        datetime.min.time(),
    ).replace(
        hour=start_time_parts[0],
        minute=start_time_parts[1],
        second=start_time_parts[2] if len(start_time_parts) > 2 else 0,
    )

    if shift_template.is_overnight:
        end_date = shift_date + timedelta(days=1)
    else:
        end_date = shift_date

    dt_end = datetime.combine(
        end_date,
        datetime.min.time(),
    ).replace(
        hour=end_time_parts[0],
        minute=end_time_parts[1],
        second=end_time_parts[2] if len(end_time_parts) > 2 else 0,
    )

    return dt_start, dt_end


def parse_iso_utc(ts_str: str) -> datetime:
    """Parse a naive UTC datetime from ISO string ending in Z or naive ISO."""
    clean = ts_str.rstrip("Z")
    return datetime.fromisoformat(clean)


def check_double_booking(assignments: List[ShiftAssignment]) -> List[str]:
    """Check that no employee is scheduled on overlapping active shifts on the same date.

    Returns:
        List of error descriptions (empty if valid).
    """
    errors = []
    # Group by employee and shift_date
    emp_day_shifts = defaultdict(list)
    for a in assignments:
        if a.assignment_status == "ACTIVE":
            emp_day_shifts[(a.employee_id, a.shift_date)].append(a)

    for (emp_id, d), shifts in emp_day_shifts.items():
        if len(shifts) > 1:
            # Check pairwise overlap
            for i in range(len(shifts)):
                for j in range(i + 1, len(shifts)):
                    s1 = parse_iso_utc(shifts[i].scheduled_start)
                    e1 = parse_iso_utc(shifts[i].scheduled_end)
                    s2 = parse_iso_utc(shifts[j].scheduled_start)
                    e2 = parse_iso_utc(shifts[j].scheduled_end)
                    # Overlap: start1 < end2 and start2 < end1
                    if s1 < e2 and s2 < e1:
                        errors.append(
                            f"DOUBLE_BOOKED: Employee {emp_id} has overlapping active shifts on {d} "
                            f"({shifts[i].assignment_id} [{s1} - {e1}] and {shifts[j].assignment_id} [{s2} - {e2}])"
                        )
    return errors


def check_leave_overlap(
    assignments: List[ShiftAssignment],
    leave_requests: List[LeaveRequest],
) -> List[str]:
    """Check that no employee with an APPROVED leave request has an ACTIVE shift assignment on a leave date.

    Returns:
        List of error descriptions.
    """
    errors = []
    # Build map of employee_id -> set of approved leave dates
    emp_approved_dates = defaultdict(set)
    for lve in leave_requests:
        if lve.current_status == "APPROVED":
            cur = lve.start_date
            while cur <= lve.end_date:
                emp_approved_dates[lve.employee_id].add(cur)
                cur += timedelta(days=1)

    for a in assignments:
        if a.assignment_status == "ACTIVE":
            if a.shift_date in emp_approved_dates[a.employee_id]:
                errors.append(
                    f"LEAVE_OVERLAP: Employee {a.employee_id} has active assignment {a.assignment_id} "
                    f"on {a.shift_date} during approved leave"
                )
    return errors


def check_manager_not_scheduled(
    assignments: List[ShiftAssignment],
    employees: List[Employee],
) -> List[str]:
    """Check that managers are never scheduled on operational shift assignments."""
    errors = []
    emp_map = {e.employee_id: e for e in employees}
    for a in assignments:
        if a.assignment_status == "ACTIVE":
            emp = emp_map.get(a.employee_id)
            if emp and emp.role == "MANAGER":
                errors.append(
                    f"MANAGER_SCHEDULED: Manager {a.employee_id} is assigned to shift {a.assignment_id} on {a.shift_date}"
                )
    return errors


def check_active_bounds(
    assignments: List[ShiftAssignment],
    employees: List[Employee],
) -> List[str]:
    """Check that shift assignments fall within employee hire_date and termination_date."""
    errors = []
    emp_map = {e.employee_id: e for e in employees}
    for a in assignments:
        if a.assignment_status == "ACTIVE":
            emp = emp_map.get(a.employee_id)
            if emp:
                if a.shift_date < emp.hire_date:
                    errors.append(
                        f"BEFORE_HIRE: Employee {a.employee_id} shift {a.shift_date} is before hire_date {emp.hire_date}"
                    )
                if emp.termination_date and a.shift_date > emp.termination_date:
                    errors.append(
                        f"AFTER_TERMINATION: Employee {a.employee_id} shift {a.shift_date} is after termination_date {emp.termination_date}"
                    )
    return errors


def check_certification_validity(
    assignments: List[ShiftAssignment],
    employee_skills: List[EmployeeSkill],
) -> List[str]:
    """Check that any assignment fulfilling a skill has a valid, non-expired certification on shift_date."""
    errors = []
    # (employee_id, skill_id) -> list of (certified_date, expiry_date)
    cert_map = defaultdict(list)
    for es in employee_skills:
        cert_map[(es.employee_id, es.skill_id)].append((es.certified_date, es.expiry_date))

    for a in assignments:
        if a.assignment_status == "ACTIVE" and a.fulfills_skill_id:
            certs = cert_map.get((a.employee_id, a.fulfills_skill_id), [])
            valid = any(
                c_date <= a.shift_date and (exp_date is None or exp_date >= a.shift_date)
                for c_date, exp_date in certs
            )
            if not valid:
                errors.append(
                    f"INVALID_CERTIFICATION: Employee {a.employee_id} assigned to fulfill {a.fulfills_skill_id} "
                    f"on {a.shift_date} without a valid certification"
                )
    return errors


def check_rest_period(
    assignments: List[ShiftAssignment],
    min_rest_hours: float = 11.0,
) -> List[str]:
    """Check that adjacent active shifts for each employee have at least min_rest_hours separation.

    Returns:
        List of error descriptions.
    """
    errors = []
    emp_shifts = defaultdict(list)
    for a in assignments:
        if a.assignment_status == "ACTIVE":
            start_dt = parse_iso_utc(a.scheduled_start)
            end_dt = parse_iso_utc(a.scheduled_end)
            emp_shifts[a.employee_id].append((start_dt, end_dt, a.assignment_id, a.shift_date))

    for emp_id, shift_list in emp_shifts.items():
        # Sort chronologically by scheduled_start
        shift_list.sort(key=lambda x: x[0])
        for i in range(len(shift_list) - 1):
            cur_end = shift_list[i][1]
            next_start = shift_list[i + 1][0]
            rest_hours = (next_start - cur_end).total_seconds() / 3600.0
            if rest_hours < min_rest_hours - 1e-5:
                errors.append(
                    f"REST_VIOLATION: Employee {emp_id} between {shift_list[i][2]} (ends {cur_end}) "
                    f"and {shift_list[i + 1][2]} (starts {next_start}) has only {rest_hours:.1f}h rest (< {min_rest_hours}h)"
                )
    return errors


def check_consecutive_working_days(
    assignments: List[ShiftAssignment],
    max_consecutive_days: int = 6,
) -> List[str]:
    """Check that no employee works more than max_consecutive_days in any rolling window.

    Every rolling window of (max_consecutive_days + 1) days must have at least one day without shifts.
    """
    errors = []
    emp_dates = defaultdict(set)
    for a in assignments:
        if a.assignment_status == "ACTIVE":
            emp_dates[a.employee_id].add(a.shift_date)

    window_size = max_consecutive_days + 1  # 7 days

    for emp_id, worked_dates in emp_dates.items():
        if not worked_dates:
            continue
        sorted_dates = sorted(worked_dates)
        min_date = sorted_dates[0]
        max_date = sorted_dates[-1]

        cur_d = min_date
        while cur_d <= max_date - timedelta(days=max_consecutive_days):
            window_days = [cur_d + timedelta(days=offset) for offset in range(window_size)]
            worked_in_window = sum(1 for wd in window_days if wd in worked_dates)
            if worked_in_window > max_consecutive_days:
                errors.append(
                    f"CONSECUTIVE_DAYS_VIOLATION: Employee {emp_id} worked {worked_in_window} days "
                    f"in 7-day window starting {cur_d} (exceeds max {max_consecutive_days} consecutive days)"
                )
            cur_d += timedelta(days=1)

    return errors


def check_overtime_cap(
    assignments: List[ShiftAssignment],
    horizon_start: date,
    horizon_end: date,
    max_full_week_paid_hours: float = 48.0,
    paid_hours_per_shift: float = 7.5,
) -> List[str]:
    """Check that no employee exceeds 48.0 paid hours in any full ISO calendar week.

    Partial weeks (truncated by horizon start/end) are exempt. Overnight shifts count toward
    the ISO week of their start date (shift_date).
    """
    errors = []
    # Group paid hours by (employee_id, iso_year, iso_week)
    emp_week_hours = defaultdict(float)

    for a in assignments:
        if a.assignment_status == "ACTIVE":
            iso_year, iso_week, _ = a.shift_date.isocalendar()
            emp_week_hours[(a.employee_id, iso_year, iso_week)] += paid_hours_per_shift

    # Determine which (iso_year, iso_week) pairs are partial
    # A full ISO week has all 7 days (Mon-Sun) within [horizon_start, horizon_end]
    partial_weeks = set()
    for (emp_id, y, w) in emp_week_hours.keys():
        # Get Monday of this ISO week
        mon = date.fromisocalendar(y, w, 1)
        sun = date.fromisocalendar(y, w, 7)
        if mon < horizon_start or sun > horizon_end:
            partial_weeks.add((y, w))

    for (emp_id, y, w), total_paid in emp_week_hours.items():
        if (y, w) not in partial_weeks:
            if total_paid > max_full_week_paid_hours + 1e-5:
                errors.append(
                    f"OVERTIME_CAP_EXCEEDED: Employee {emp_id} worked {total_paid:.1f} paid hours in full week {y}-W{w:02d} "
                    f"(exceeds statutory cap {max_full_week_paid_hours}h)"
                )

    return errors


def validate_all_schedule_rules(
    assignments: List[ShiftAssignment],
    employees: List[Employee],
    employee_skills: List[EmployeeSkill],
    leave_requests: List[LeaveRequest],
    horizon_start: date,
    horizon_end: date,
) -> List[str]:
    """Run all schedule invariant checks and return a unified list of rule violations."""
    violations: List[str] = []
    violations.extend(check_double_booking(assignments))
    violations.extend(check_leave_overlap(assignments, leave_requests))
    violations.extend(check_manager_not_scheduled(assignments, employees))
    violations.extend(check_active_bounds(assignments, employees))
    violations.extend(check_certification_validity(assignments, employee_skills))
    violations.extend(check_rest_period(assignments))
    violations.extend(check_consecutive_working_days(assignments))
    violations.extend(check_overtime_cap(assignments, horizon_start, horizon_end))
    return violations

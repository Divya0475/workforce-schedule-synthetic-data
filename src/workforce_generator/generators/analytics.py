"""Derived Analytics Table Generators: CoverageGap and WeeklyHoursSummary.

These projections are computed deterministically from operational records (ShiftAssignment,
CoverageRequirement, Employee, EmployeeSkill, LeaveRequest) post-scheduling.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from workforce_generator.config import GeneratorConfig
from workforce_generator.models.entities import (
    CoverageGap,
    CoverageRequirement,
    Employee,
    EmployeeSkill,
    LeaveRequest,
    ShiftAssignment,
    WeeklyHoursSummary,
)
from workforce_generator.models.identifiers import (
    format_coverage_gap_id,
    format_weekly_hours_summary_id,
)
from workforce_generator.rules import parse_iso_utc


def compute_coverage_gaps(
    cfg: GeneratorConfig,
    coverage_reqs: List[CoverageRequirement],
    assignments: List[ShiftAssignment],
    employees: List[Employee],
    employee_skills: List[EmployeeSkill],
    leave_requests: List[LeaveRequest],
) -> List[CoverageGap]:
    """Compute CoverageGap analytical projection comparing requirements against active assignments.

    Args:
        cfg: Generator configuration.
        coverage_reqs: List of CoverageRequirement records.
        assignments: List of ShiftAssignment records.
        employees: List of Employee records.
        employee_skills: List of EmployeeSkill records.
        leave_requests: List of LeaveRequest records.

    Returns:
        List of CoverageGap dataclass instances.
    """
    now_ts = f"{cfg.horizon.start_date.isoformat()}T00:00:00Z"

    # Index active assignments by (shift_date, team_id, shift_template_id, fulfills_skill_id) -> count
    scheduled_by_slot: Dict[Tuple[date, str, str, str], int] = defaultdict(int)
    for a in assignments:
        if a.assignment_status == "ACTIVE" and a.fulfills_skill_id:
            key = (a.shift_date, a.team_id, a.shift_template_id, a.fulfills_skill_id)
            scheduled_by_slot[key] += 1

    # Pre-index approved leave dates per employee
    emp_leaves: Dict[str, Set[date]] = defaultdict(set)
    emp_sick_leaves: Dict[str, Set[date]] = defaultdict(set)
    for lve in leave_requests:
        if lve.current_status == "APPROVED":
            cur = lve.start_date
            while cur <= lve.end_date:
                emp_leaves[lve.employee_id].add(cur)
                if lve.leave_type == "SICK":
                    emp_sick_leaves[lve.employee_id].add(cur)
                cur += timedelta(days=1)

    # Pre-index employee skills
    emp_skills_map: Dict[str, Set[str]] = defaultdict(set)
    for es in employee_skills:
        emp_skills_map[es.employee_id].add(es.skill_id)

    # Team staff map (non-managers)
    team_staff: Dict[str, List[Employee]] = defaultdict(list)
    for e in employees:
        if e.role != "MANAGER":
            team_staff[e.team_id].append(e)

    gaps: List[CoverageGap] = []
    gap_seq = 0

    for req in coverage_reqs:
        key = (req.effective_date, req.team_id, req.shift_template_id, req.skill_id)
        scheduled_count = scheduled_by_slot.get(key, 0)
        gap_headcount = max(0, req.min_headcount - scheduled_count)
        target_shortfall = max(0, req.target_headcount - scheduled_count)
        is_critical = gap_headcount > 0

        # Diagnose primary reason for shortfall / gap
        if gap_headcount == 0 and target_shortfall == 0:
            reason = "Fully staffed"
        else:
            # Diagnose reason
            staff_list = team_staff.get(req.team_id, [])
            qualified_staff = [
                s for s in staff_list
                if req.skill_id in emp_skills_map[s.employee_id]
                and s.hire_date <= req.effective_date
                and (s.termination_date is None or s.termination_date >= req.effective_date)
            ]

            if len(qualified_staff) < req.min_headcount:
                reason = "Skill pinch: insufficient baseline qualified staff"
            else:
                # Check if qualified staff are on leave
                sick_count = sum(1 for s in qualified_staff if req.effective_date in emp_sick_leaves[s.employee_id])
                planned_leave_count = sum(1 for s in qualified_staff if req.effective_date in emp_leaves[s.employee_id] and req.effective_date not in emp_sick_leaves[s.employee_id])
                if sick_count > 0:
                    reason = "Unscheduled sick leave absence"
                elif planned_leave_count > 0:
                    reason = "Approved leave reduction"
                elif target_shortfall > 0 and gap_headcount == 0:
                    reason = "Target buffer omitted to prevent contractual overtime"
                else:
                    reason = "Rest rule and working hour constraints"

        gap_seq += 1
        gap_id = format_coverage_gap_id(req.effective_date.year, gap_seq)

        gaps.append(
            CoverageGap(
                gap_id=gap_id,
                coverage_id=req.coverage_id,
                effective_date=req.effective_date,
                team_id=req.team_id,
                shift_template_id=req.shift_template_id,
                skill_id=req.skill_id,
                required_headcount=req.min_headcount,
                scheduled_headcount=scheduled_count,
                gap_headcount=gap_headcount,
                target_shortfall=target_shortfall,
                reason=reason,
                is_critical=is_critical,
                computed_at=now_ts,
            )
        )

    return gaps


def compute_weekly_hours_summaries(
    cfg: GeneratorConfig,
    employees: List[Employee],
    assignments: List[ShiftAssignment],
) -> List[WeeklyHoursSummary]:
    """Compute WeeklyHoursSummary analytical projection aggregating paid hours per employee per ISO week.

    Overnight shifts count toward the ISO week of their start date (shift_date).
    Partial weeks are identified and exempt from statutory overtime cap checks.
    """
    horizon_start = cfg.horizon.start_date
    horizon_end = cfg.horizon.end_date
    paid_hours_per_shift = cfg.labor_constraints.paid_hours_per_shift
    max_ot_cap = cfg.labor_constraints.overtime_cap_weekly_hours
    now_ts = f"{horizon_start.isoformat()}T00:00:00Z"

    # Identify all (iso_year, iso_week) within the horizon
    # An ISO week is partial if Monday < horizon_start or Sunday > horizon_end
    all_iso_weeks: Set[Tuple[int, int]] = set()
    cur = horizon_start
    while cur <= horizon_end:
        all_iso_weeks.add(cur.isocalendar()[:2])
        cur += timedelta(days=1)

    partial_weeks_map: Dict[Tuple[int, int], bool] = {}
    for (y, w) in all_iso_weeks:
        mon = date.fromisocalendar(y, w, 1)
        sun = date.fromisocalendar(y, w, 7)
        is_partial = (mon < horizon_start or sun > horizon_end)
        partial_weeks_map[(y, w)] = is_partial

    # Aggregate active assignment paid hours by (employee_id, iso_year, iso_week)
    emp_sched_hours: Dict[Tuple[str, int, int], float] = defaultdict(float)
    for a in assignments:
        if a.assignment_status == "ACTIVE":
            iso_year, iso_week, _ = a.shift_date.isocalendar()
            emp_sched_hours[(a.employee_id, iso_year, iso_week)] += paid_hours_per_shift

    summaries: List[WeeklyHoursSummary] = []
    sorted_weeks = sorted(all_iso_weeks)

    for emp in employees:
        emp_id = emp.employee_id
        contract_h = emp.weekly_contract_hours

        for (iso_year, iso_week) in sorted_weeks:
            is_partial = partial_weeks_map[(iso_year, iso_week)]
            sched_h = emp_sched_hours.get((emp_id, iso_year, iso_week), 0.0)
            regular_h = min(sched_h, contract_h)
            overtime_h = max(0.0, sched_h - contract_h)
            exceeds_cap = (not is_partial) and (sched_h > max_ot_cap + 1e-5)
            summary_id = format_weekly_hours_summary_id(emp_id, iso_year, iso_week)

            summaries.append(
                WeeklyHoursSummary(
                    summary_id=summary_id,
                    employee_id=emp_id,
                    iso_year=iso_year,
                    iso_week=iso_week,
                    is_partial_week=is_partial,
                    contract_hours=contract_h,
                    scheduled_hours=sched_h,
                    regular_hours=regular_h,
                    overtime_hours=overtime_h,
                    exceeds_overtime_cap=exceeds_cap,
                    computed_at=now_ts,
                )
            )

    return summaries

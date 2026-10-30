"""Derived Analytics Table Generators: CoverageGap and WeeklyHoursSummary.

These projections are computed deterministically from operational records (ShiftAssignment,
CoverageRequirement, Employee, EmployeeSkill, LeaveRequest) post-scheduling.
"""

from __future__ import annotations

from collections import Counter, defaultdict
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
from workforce_generator.rules import get_shift_datetimes, parse_iso_utc


def compute_coverage_gaps(
    cfg: GeneratorConfig,
    coverage_reqs: List[CoverageRequirement],
    assignments: List[ShiftAssignment],
    employees: List[Employee],
    employee_skills: List[EmployeeSkill],
    leave_requests: List[LeaveRequest],
) -> List[CoverageGap]:
    """Compute CoverageGap analytical projection comparing requirements against active assignments.

    For every CoverageGap with gap_headcount > 0, performs an exhaustive unavailability audit
    across all qualified employees of that team to identify the primary failure reason:
    - Not yet hired or already terminated
    - Approved leave (statutory sick or planned)
    - Already assigned to this shift slot
    - Rest period violation (< 11h from another scheduled shift)
    - Rolling 6-day consecutive working limit reached
    - Overtime ceiling exceeded (> 48h weekly paid hours)
    """
    now_ts = f"{cfg.horizon.start_date.isoformat()}T00:00:00Z"
    paid_hours_per_shift = cfg.labor_constraints.paid_hours_per_shift
    min_rest_hours = cfg.labor_constraints.min_rest_hours_between_shifts
    max_consecutive_days = cfg.labor_constraints.max_consecutive_working_days
    max_weekly_hours = cfg.labor_constraints.overtime_cap_weekly_hours

    shift_objs_map = {s.id: s for s in cfg.shifts}

    # Index active assignments by (shift_date, team_id, shift_template_id, fulfills_skill_id) -> count
    scheduled_by_slot: Dict[Tuple[date, str, str, str], int] = defaultdict(int)
    # Index active assignments by (shift_date, team_id, shift_template_id) -> Set of employee_ids
    assigned_in_slot: Dict[Tuple[date, str, str], Set[str]] = defaultdict(set)
    # Employee worked dates: employee_id -> Set[date]
    emp_worked_dates: Dict[str, Set[date]] = defaultdict(set)
    # Employee shift intervals: employee_id -> List[(start_dt, end_dt)]
    emp_shift_intervals: Dict[str, List[Tuple[datetime, datetime]]] = defaultdict(list)
    # Employee weekly paid hours: (employee_id, iso_year, iso_week) -> float
    emp_week_hours: Dict[Tuple[str, int, int], float] = defaultdict(float)

    active_assignments = [
        assignment for assignment in assignments if assignment.assignment_status == "ACTIVE"
    ]
    for assignment in active_assignments:
        if assignment.fulfills_skill_id:
            key = (
                assignment.shift_date,
                assignment.team_id,
                assignment.shift_template_id,
                assignment.fulfills_skill_id,
            )
            scheduled_by_slot[key] += 1
        assigned_in_slot[
            (assignment.shift_date, assignment.team_id, assignment.shift_template_id)
        ].add(assignment.employee_id)
        emp_worked_dates[assignment.employee_id].add(assignment.shift_date)
        start_dt = parse_iso_utc(assignment.scheduled_start)
        end_dt = parse_iso_utc(assignment.scheduled_end)
        emp_shift_intervals[assignment.employee_id].append((start_dt, end_dt))
        iso_year, iso_week, _ = assignment.shift_date.isocalendar()
        emp_week_hours[(assignment.employee_id, iso_year, iso_week)] += paid_hours_per_shift

    # Pre-index approved leave dates per employee
    emp_leaves: Dict[str, Set[date]] = defaultdict(set)
    for lve in leave_requests:
        if lve.current_status == "APPROVED":
            cur = lve.start_date
            while cur <= lve.end_date:
                emp_leaves[lve.employee_id].add(cur)
                cur += timedelta(days=1)

    # Pre-index employee skills with valid dates: employee_id -> Dict[skill_id, List[(certified_date, expiry_date)]]
    emp_certs: Dict[str, Dict[str, List[Tuple[date, Optional[date]]]]] = defaultdict(lambda: defaultdict(list))
    for es in employee_skills:
        emp_certs[es.employee_id][es.skill_id].append((es.certified_date, es.expiry_date))

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
        has_gap = gap_headcount > 0

        # Diagnose the reason from each qualified employee's first blocking constraint.
        if gap_headcount == 0 and target_shortfall == 0:
            reason = "Fully staffed"
        else:
            staff_list = team_staff.get(req.team_id, [])
            qualified_staff = [
                s for s in staff_list
                if any(
                    cd <= req.effective_date and (ed is None or ed >= req.effective_date)
                    for cd, ed in emp_certs[s.employee_id].get(req.skill_id, [])
                )
            ]
            if gap_headcount > 0 and len(qualified_staff) < req.min_headcount:
                reason = "Skill pinch: insufficient baseline qualified staff"
            else:
                s_tmpl = shift_objs_map[req.shift_template_id]
                s_start, s_end = get_shift_datetimes(req.effective_date, s_tmpl)
                iso_y, iso_w, _ = req.effective_date.isocalendar()
                slot_assigned = assigned_in_slot.get(
                    (req.effective_date, req.team_id, req.shift_template_id), set()
                )
                unavailability_reasons = []
                available_count = 0
                for s in qualified_staff:
                    sid = s.employee_id
                    if req.effective_date < s.hire_date:
                        unavailability_reasons.append("hire_date")
                    elif s.termination_date and req.effective_date > s.termination_date:
                        unavailability_reasons.append("termination_date")
                    elif req.effective_date in emp_leaves[sid]:
                        unavailability_reasons.append("approved_leave")
                    elif sid in slot_assigned:
                        unavailability_reasons.append("already_assigned_in_shift")
                    elif req.effective_date in emp_worked_dates[sid]:
                        unavailability_reasons.append("already_working_today")
                    else:
                        rest_violated = False
                        for prev_s, prev_e in emp_shift_intervals[sid]:
                            if s_start >= prev_e:
                                rest_h = (s_start - prev_e).total_seconds() / 3600.0
                                if rest_h < min_rest_hours - 1e-5:
                                    rest_violated = True
                                    break
                            elif prev_s >= s_end:
                                rest_h = (prev_s - s_end).total_seconds() / 3600.0
                                if rest_h < min_rest_hours - 1e-5:
                                    rest_violated = True
                                    break
                            else:
                                rest_violated = True
                                break
                        if rest_violated:
                            unavailability_reasons.append("rest_rule")
                            continue

                        w_dates = emp_worked_dates[sid]
                        cons_violated = False
                        for start_offset in range(max_consecutive_days + 1):
                            win_start = req.effective_date - timedelta(days=start_offset)
                            cnt = 1
                            for day_off in range(max_consecutive_days + 1):
                                chk_d = win_start + timedelta(days=day_off)
                                if chk_d != req.effective_date and chk_d in w_dates:
                                    cnt += 1
                            if cnt > max_consecutive_days:
                                cons_violated = True
                                break
                        if cons_violated:
                            unavailability_reasons.append("consecutive_days")
                            continue

                        cur_wh = emp_week_hours[(sid, iso_y, iso_w)]
                        if cur_wh + paid_hours_per_shift > max_weekly_hours + 1e-5:
                            unavailability_reasons.append("overtime_cap")
                            continue

                        if (
                            gap_headcount == 0
                            and cur_wh + paid_hours_per_shift
                            > s.weekly_contract_hours + 1e-5
                        ):
                            unavailability_reasons.append("contract_hours")
                            continue

                        available_count += 1

                if available_count:
                    reason = "available_qualified_employee"
                elif unavailability_reasons:
                    reason = max(
                        Counter(unavailability_reasons).items(),
                        key=lambda item: (item[1], item[0]),
                    )[0]
                else:
                    reason = "no_qualified_staff"

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
                has_gap=has_gap,
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

    emp_sched_hours: Dict[Tuple[str, int, int], float] = defaultdict(float)
    active_assignments = [
        assignment for assignment in assignments if assignment.assignment_status == "ACTIVE"
    ]
    for assignment in active_assignments:
        iso_year, iso_week, _ = assignment.shift_date.isocalendar()
        emp_sched_hours[(assignment.employee_id, iso_year, iso_week)] += paid_hours_per_shift

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

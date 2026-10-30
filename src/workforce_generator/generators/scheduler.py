"""Shift Scheduler for synthetic workforce generation.

Schedules operational staff week by week:
- PASS 1: For each ISO week, fills ALL min_headcount slots across every day and shift of that week
  (scarcest skills first). Overtime (paid hours above weekly_contract_hours, up to the 48h cap)
  is allowed ONLY in pass 1 to ensure mandatory coverage floors are satisfied.
- PASS 2: For each ISO week, fills toward target_headcount soft buffers ONLY for employees who are
  still below their weekly_contract_hours.

Candidate qualification and eligibility gates:
- Schedulable active staff (hire_date <= shift_date <= termination_date, non-manager).
- Valid certification on the shift date for the fulfilled skill.
- No approved leave (statutory sick and planned approved leaves act as strict unavailability).
- No overlapping shift (single shift per employee per date).
- At least 11.0h rest between consecutive shifts.
- At most 6 consecutive working days.
- At most 48.0 paid hours per ISO calendar week (7.5 paid hours per 8.0h shift).

Fairness and rotation:
- Priority given to eligible candidates with the fewest cumulative scheduled paid hours in the current ISO week.
- Tie-break by lowest total cumulative scheduled hours across the horizon (prevents staff starvation).
- Night (Shift C) and weekend assignments track historical frequency per employee to rotate unsocial shifts evenly.

Assignment metadata:
- fulfills_skill_id: The specific skill slot fulfilled.
- assignment_status: 'ACTIVE'.
- effective_from: Horizon start timestamp ("YYYY-01-01T00:00:00Z").
- effective_to: None.
- is_holiday_premium: True if worked on a statutory holiday.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
import numpy as np

from workforce_generator.config import GeneratorConfig
from workforce_generator.models.entities import (
    CoverageRequirement,
    Employee,
    EmployeeSkill,
    HolidayCalendar,
    LeaveRequest,
    ShiftAssignment,
    ShiftTemplate,
)
from workforce_generator.models.identifiers import (
    format_assignment_id,
    format_shift_instance_id,
)
from workforce_generator.rules import get_shift_datetimes


def schedule_shifts(
    cfg: GeneratorConfig,
    rng: np.random.Generator,
    employees: List[Employee],
    employee_skills: List[EmployeeSkill],
    coverage_reqs: List[CoverageRequirement],
    holidays: List[HolidayCalendar],
    leave_requests: List[LeaveRequest],
) -> List[ShiftAssignment]:
    """Execute the workforce shift scheduling engine with ISO-week-level two-pass scheduling.

    Returns:
        List of generated ShiftAssignment records in ACTIVE status.
    """
    horizon_start = cfg.horizon.start_date
    horizon_end = cfg.horizon.end_date
    paid_hours_per_shift = cfg.labor_constraints.paid_hours_per_shift
    min_rest_hours = cfg.labor_constraints.min_rest_hours_between_shifts
    max_consecutive_days = cfg.labor_constraints.max_consecutive_working_days
    max_weekly_hours = cfg.labor_constraints.overtime_cap_weekly_hours

    shift_objs_map = {
        s.id: ShiftTemplate(
            shift_template_id=s.id,
            template_code=s.code,
            name=s.name,
            start_time=s.start_time,
            end_time=s.end_time,
            duration_hours=s.duration_hours,
            unpaid_break_minutes=s.unpaid_break_minutes,
            is_overnight=s.is_overnight,
        )
        for s in cfg.shifts
    }

    statutory_dates: Set[date] = {
        h.calendar_date for h in holidays if h.is_statutory
    }

    # Pre-index employees by team (non-manager only)
    sched_employees_by_team: Dict[str, List[Employee]] = defaultdict(list)
    emp_map: Dict[str, Employee] = {}
    for e in employees:
        emp_map[e.employee_id] = e
        if e.role != "MANAGER":
            sched_employees_by_team[e.team_id].append(e)

    # Pre-index skills by employee: employee_id -> Dict[skill_id, List[(certified_date, expiry_date)]]
    emp_certs: Dict[str, Dict[str, List[Tuple[date, Optional[date]]]]] = defaultdict(lambda: defaultdict(list))
    for es in employee_skills:
        emp_certs[es.employee_id][es.skill_id].append((es.certified_date, es.expiry_date))

    # Pre-index approved leave dates per employee: employee_id -> Set[date]
    emp_leave_dates: Dict[str, Set[date]] = defaultdict(set)
    for lve in leave_requests:
        if lve.current_status == "APPROVED":
            cur = lve.start_date
            while cur <= lve.end_date:
                emp_leave_dates[lve.employee_id].add(cur)
                cur += timedelta(days=1)

    # Pre-index coverage requirements by (effective_date, team_id, shift_template_id) -> List[CoverageRequirement]
    coverage_by_slot: Dict[Tuple[date, str, str], List[CoverageRequirement]] = defaultdict(list)
    for req in coverage_reqs:
        coverage_by_slot[(req.effective_date, req.team_id, req.shift_template_id)].append(req)

    # Pre-calculate skill scarcity per team to prioritize rarest skills
    team_skill_holder_count: Dict[Tuple[str, str], int] = defaultdict(int)
    for t_id, t_emps in sched_employees_by_team.items():
        for e in t_emps:
            for sk_id in emp_certs[e.employee_id].keys():
                team_skill_holder_count[(t_id, sk_id)] += 1

    # Employee schedule state tracking:
    # 1. Worked dates: employee_id -> Set[date]
    emp_worked_dates: Dict[str, Set[date]] = defaultdict(set)
    # 2. Chronological shifts: employee_id -> List[(scheduled_start_dt, scheduled_end_dt)]
    emp_shift_intervals: Dict[str, List[Tuple[datetime, datetime]]] = defaultdict(list)
    # 3. Weekly paid hours: (employee_id, iso_year, iso_week) -> float
    emp_week_hours: Dict[Tuple[str, int, int], float] = defaultdict(float)
    # 4. Total horizon paid hours: employee_id -> float (for fairness tie-breaking)
    emp_total_hours: Dict[str, float] = defaultdict(float)
    # 5. Rotation tracking:
    emp_night_shifts: Dict[str, int] = defaultdict(int)
    emp_weekend_shifts: Dict[str, int] = defaultdict(int)

    assignments: List[ShiftAssignment] = []
    assignment_seq = 0
    effective_from_ts = f"{horizon_start.isoformat()}T00:00:00Z"

    # Pre-index all dates in horizon grouped by (iso_year, iso_week)
    weeks_to_dates: Dict[Tuple[int, int], List[date]] = defaultdict(list)
    cur = horizon_start
    while cur <= horizon_end:
        iso_y, iso_w, _ = cur.isocalendar()
        weeks_to_dates[(iso_y, iso_w)].append(cur)
        cur += timedelta(days=1)

    sorted_iso_weeks = sorted(weeks_to_dates.keys())
    shift_ids_ordered = ["SFT-A", "SFT-B", "SFT-C"]
    teams_sorted = sorted(sched_employees_by_team.keys())

    # Pre-build list of all slot tasks for each week
    # A slot task is: (shift_date, shift_t_id, team_id, req, slot_type)
    for iso_week_key in sorted_iso_weeks:
        week_dates = weeks_to_dates[iso_week_key]

        # Gather all slots for this week
        week_slots = []
        for d in week_dates:
            for s_id in shift_ids_ordered:
                for t_id in teams_sorted:
                    reqs = coverage_by_slot.get((d, t_id, s_id), [])
                    if reqs:
                        week_slots.append((d, s_id, t_id, reqs))

        # Helper to check candidate eligibility for a shift slot
        def is_eligible(
            emp: Employee,
            d: date,
            s_tmpl: ShiftTemplate,
            s_start: datetime,
            s_end: datetime,
            req_skill: str,
            allow_overtime: bool,
        ) -> bool:
            e_id = emp.employee_id

            # Active bounds
            if d < emp.hire_date:
                return False
            if emp.termination_date and d > emp.termination_date:
                return False

            # Single shift per day
            if d in emp_worked_dates[e_id]:
                return False

            # No approved leave
            if d in emp_leave_dates[e_id]:
                return False

            # Valid certification
            certs = emp_certs[e_id].get(req_skill, [])
            if not any(cd <= d and (ed is None or ed >= d) for cd, ed in certs):
                return False

            # Overtime / contract limit
            cur_wh = emp_week_hours[(e_id, iso_week_key[0], iso_week_key[1])]
            new_wh = cur_wh + paid_hours_per_shift
            if allow_overtime:
                if new_wh > max_weekly_hours + 1e-5:
                    return False
            else:
                if new_wh > emp.weekly_contract_hours + 1e-5:
                    return False

            # Rest period (check against all previously scheduled shifts of this employee)
            # Efficient check: test shifts within 48h window
            intervals = emp_shift_intervals[e_id]
            for prev_start, prev_end in intervals:
                # If s_start is after prev_end, rest is s_start - prev_end
                # If prev_start is after s_end, rest is prev_start - s_end
                if s_start >= prev_end:
                    rest_h = (s_start - prev_end).total_seconds() / 3600.0
                    if rest_h < min_rest_hours - 1e-5:
                        return False
                elif prev_start >= s_end:
                    rest_h = (prev_start - s_end).total_seconds() / 3600.0
                    if rest_h < min_rest_hours - 1e-5:
                        return False
                else:
                    # Overlap!
                    return False

            # Consecutive days limit (<= 6 days in any 7-day rolling window)
            # Temporarily add d to emp_worked_dates and check windows covering d
            w_dates = emp_worked_dates[e_id]
            for start_offset in range(max_consecutive_days + 1):
                win_start = d - timedelta(days=start_offset)
                worked_count = 1  # including d
                for day_off in range(max_consecutive_days + 1):
                    check_d = win_start + timedelta(days=day_off)
                    if check_d != d and check_d in w_dates:
                        worked_count += 1
                if worked_count > max_consecutive_days:
                    return False

            return True

        # ======================================================================
        # PASS 1: Fill ALL min_headcount slots for every day & shift of the week
        # (Scarcest skills first; overtime allowed up to 48h)
        # ======================================================================
        # Flatten all min requirement slots across the entire week
        min_tasks = []
        for d, s_id, t_id, reqs in week_slots:
            s_tmpl = shift_objs_map[s_id]
            s_start, s_end = get_shift_datetimes(d, s_tmpl)
            for req in reqs:
                scarcity = team_skill_holder_count.get((t_id, req.skill_id), 999)
                for _ in range(req.min_headcount):
                    min_tasks.append({
                        "date": d,
                        "shift_template_id": s_id,
                        "team_id": t_id,
                        "shift_tmpl": s_tmpl,
                        "start_dt": s_start,
                        "end_dt": s_end,
                        "skill_id": req.skill_id,
                        "scarcity": scarcity,
                    })

        # Sort min tasks: scarcest skills first, then chronological
        min_tasks.sort(key=lambda x: (x["scarcity"], x["date"], x["shift_template_id"]))

        for task in min_tasks:
            d = task["date"]
            s_id = task["shift_template_id"]
            t_id = task["team_id"]
            s_tmpl = task["shift_tmpl"]
            s_start = task["start_dt"]
            s_end = task["end_dt"]
            sk_id = task["skill_id"]

            team_staff = sched_employees_by_team[t_id]
            best_cand = None
            best_score = None

            for emp in team_staff:
                if not is_eligible(emp, d, s_tmpl, s_start, s_end, sk_id, allow_overtime=True):
                    continue

                e_id = emp.employee_id
                wk_h = emp_week_hours[(e_id, iso_week_key[0], iso_week_key[1])]
                tot_h = emp_total_hours[e_id]
                rot_pen = 0
                if s_tmpl.is_overnight:
                    rot_pen += emp_night_shifts[e_id]
                if d.weekday() in (5, 6):
                    rot_pen += emp_weekend_shifts[e_id]

                # Scoring: lowest weekly hours first, tie-break by lowest horizon total, then rotation
                score = (wk_h, tot_h, rot_pen, rng.random())
                if best_score is None or score < best_score:
                    best_score = score
                    best_cand = emp

            if best_cand is not None:
                emp_id = best_cand.employee_id
                assignment_seq += 1
                shift_inst_id = format_shift_instance_id(d, t_id, s_tmpl.template_code)
                asn_id = format_assignment_id(d.year, assignment_seq)
                is_statutory = d in statutory_dates

                asn = ShiftAssignment(
                    assignment_id=asn_id,
                    shift_instance_id=shift_inst_id,
                    employee_id=emp_id,
                    team_id=t_id,
                    shift_template_id=s_id,
                    shift_date=d,
                    scheduled_start=s_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    scheduled_end=s_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    duration_hours=s_tmpl.duration_hours,
                    assigned_role=best_cand.role,
                    assignment_status="ACTIVE",
                    effective_from=effective_from_ts,
                    effective_to=None,
                    swap_request_id=None,
                    replaces_assignment_id=None,
                    fulfills_skill_id=sk_id,
                    is_holiday_premium=is_statutory,
                )
                assignments.append(asn)

                emp_worked_dates[emp_id].add(d)
                emp_shift_intervals[emp_id].append((s_start, s_end))
                emp_week_hours[(emp_id, iso_week_key[0], iso_week_key[1])] += paid_hours_per_shift
                emp_total_hours[emp_id] += paid_hours_per_shift
                if s_tmpl.is_overnight:
                    emp_night_shifts[emp_id] += 1
                if d.weekday() in (5, 6):
                    emp_weekend_shifts[emp_id] += 1

        # ======================================================================
        # PASS 2: Fill toward target_headcount soft buffers
        # (Only employees strictly below weekly_contract_hours; NO overtime)
        # ======================================================================
        target_tasks = []
        for d, s_id, t_id, reqs in week_slots:
            s_tmpl = shift_objs_map[s_id]
            s_start, s_end = get_shift_datetimes(d, s_tmpl)
            for req in reqs:
                target_buffer = max(0, req.target_headcount - req.min_headcount)
                scarcity = team_skill_holder_count.get((t_id, req.skill_id), 999)
                for _ in range(target_buffer):
                    target_tasks.append({
                        "date": d,
                        "shift_template_id": s_id,
                        "team_id": t_id,
                        "shift_tmpl": s_tmpl,
                        "start_dt": s_start,
                        "end_dt": s_end,
                        "skill_id": req.skill_id,
                        "scarcity": scarcity,
                    })

        target_tasks.sort(key=lambda x: (x["scarcity"], x["date"], x["shift_template_id"]))

        for task in target_tasks:
            d = task["date"]
            s_id = task["shift_template_id"]
            t_id = task["team_id"]
            s_tmpl = task["shift_tmpl"]
            s_start = task["start_dt"]
            s_end = task["end_dt"]
            sk_id = task["skill_id"]

            team_staff = sched_employees_by_team[t_id]
            best_cand = None
            best_score = None

            for emp in team_staff:
                # Strictly NO overtime in pass 2
                if not is_eligible(emp, d, s_tmpl, s_start, s_end, sk_id, allow_overtime=False):
                    continue

                e_id = emp.employee_id
                wk_h = emp_week_hours[(e_id, iso_week_key[0], iso_week_key[1])]
                tot_h = emp_total_hours[e_id]
                rot_pen = 0
                if s_tmpl.is_overnight:
                    rot_pen += emp_night_shifts[e_id]
                if d.weekday() in (5, 6):
                    rot_pen += emp_weekend_shifts[e_id]

                score = (wk_h, tot_h, rot_pen, rng.random())
                if best_score is None or score < best_score:
                    best_score = score
                    best_cand = emp

            if best_cand is not None:
                emp_id = best_cand.employee_id
                assignment_seq += 1
                shift_inst_id = format_shift_instance_id(d, t_id, s_tmpl.template_code)
                asn_id = format_assignment_id(d.year, assignment_seq)
                is_statutory = d in statutory_dates

                asn = ShiftAssignment(
                    assignment_id=asn_id,
                    shift_instance_id=shift_inst_id,
                    employee_id=emp_id,
                    team_id=t_id,
                    shift_template_id=s_id,
                    shift_date=d,
                    scheduled_start=s_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    scheduled_end=s_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    duration_hours=s_tmpl.duration_hours,
                    assigned_role=best_cand.role,
                    assignment_status="ACTIVE",
                    effective_from=effective_from_ts,
                    effective_to=None,
                    swap_request_id=None,
                    replaces_assignment_id=None,
                    fulfills_skill_id=sk_id,
                    is_holiday_premium=is_statutory,
                )
                assignments.append(asn)

                emp_worked_dates[emp_id].add(d)
                emp_shift_intervals[emp_id].append((s_start, s_end))
                emp_week_hours[(emp_id, iso_week_key[0], iso_week_key[1])] += paid_hours_per_shift
                emp_total_hours[emp_id] += paid_hours_per_shift
                if s_tmpl.is_overnight:
                    emp_night_shifts[emp_id] += 1
                if d.weekday() in (5, 6):
                    emp_weekend_shifts[emp_id] += 1

    return assignments

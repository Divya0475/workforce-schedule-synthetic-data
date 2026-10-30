"""Shift Scheduler for synthetic workforce generation.

Schedules operational staff day by day, shift by shift:
1. Fills hard `min_headcount` slots first, prioritizing scarcest skills within the team.
2. Fills toward `target_headcount` soft buffers only while the candidate is below their `weekly_contract_hours`.
3. Candidate qualification and eligibility gates:
   - Schedulable active staff (hire_date <= shift_date <= termination_date, non-manager).
   - Valid certification on the shift date for the fulfilled skill.
   - No approved leave (statutory sick and planned approved leaves act as strict unavailability).
   - No overlapping shift (single shift per employee per date).
   - At least 11.0h rest between consecutive shifts.
   - At most 6 consecutive working days.
   - At most 48.0 paid hours per ISO calendar week (7.5 paid hours per 8.0h shift).
4. Overtime usage:
   - Paid hours above `weekly_contract_hours` (up to 48.0h) are permitted strictly to satisfy a `min_headcount` slot.
5. Fairness and rotation:
   - Priority given to eligible candidates with the fewest cumulative scheduled paid hours in the current ISO week.
   - Night (Shift C) and weekend assignments track historical frequency per employee to rotate unsocial shifts evenly.
6. Assignment metadata:
   - `fulfills_skill_id`: The specific skill slot fulfilled.
   - `assignment_status`: `'ACTIVE'`.
   - `effective_from`: Horizon start timestamp (`"YYYY-01-01T00:00:00Z"`).
   - `effective_to`: `None`.
   - `is_holiday_premium`: `True` if worked on a statutory holiday.
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
    """Execute the workforce shift scheduling engine.

    Returns:
        List of generated ShiftAssignment records in ACTIVE status.
    """
    horizon_start = cfg.horizon.start_date
    horizon_end = cfg.horizon.end_date
    paid_hours_per_shift = cfg.labor_constraints.paid_hours_per_shift
    min_rest_hours = cfg.labor_constraints.min_rest_hours_between_shifts
    max_consecutive_days = cfg.labor_constraints.max_consecutive_working_days
    max_weekly_hours = cfg.labor_constraints.overtime_cap_weekly_hours

    shift_templates_map = {s.id: s for s in cfg.shifts}
    # Shift templates dataclass objects for datetime lookups
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
    # Scarcity: total holders of skill in team
    team_skill_holder_count: Dict[Tuple[str, str], int] = defaultdict(int)
    for t_id, t_emps in sched_employees_by_team.items():
        for e in t_emps:
            for sk_id in emp_certs[e.employee_id].keys():
                team_skill_holder_count[(t_id, sk_id)] += 1

    # Employee schedule state tracking:
    # 1. Worked dates: employee_id -> Set[date]
    emp_worked_dates: Dict[str, Set[date]] = defaultdict(set)
    # 2. Last shift interval: employee_id -> Optional[(scheduled_start_dt, scheduled_end_dt)]
    emp_last_shift: Dict[str, Optional[Tuple[datetime, datetime]]] = defaultdict(lambda: None)
    # 3. Weekly paid hours: (employee_id, iso_year, iso_week) -> float
    emp_week_hours: Dict[Tuple[str, int, int], float] = defaultdict(float)
    # 4. Rotation tracking:
    #    - night shifts worked: employee_id -> int
    #    - weekend shifts worked: employee_id -> int
    emp_night_shifts: Dict[str, int] = defaultdict(int)
    emp_weekend_shifts: Dict[str, int] = defaultdict(int)

    assignments: List[ShiftAssignment] = []
    assignment_seq = 0
    effective_from_ts = f"{horizon_start.isoformat()}T00:00:00Z"

    # Iterate day by day
    cur_date = horizon_start
    total_days = (horizon_end - horizon_start).days + 1

    # Unique teams sorted
    teams_sorted = sorted(sched_employees_by_team.keys())
    shift_ids_ordered = ["SFT-A", "SFT-B", "SFT-C"]

    for day_idx in range(total_days):
        shift_date = cur_date + timedelta(days=day_idx)
        is_statutory = shift_date in statutory_dates
        is_weekend = shift_date.weekday() in (5, 6)
        iso_year, iso_week, _ = shift_date.isocalendar()

        for shift_t_id in shift_ids_ordered:
            shift_tmpl = shift_objs_map[shift_t_id]
            s_start_dt, s_end_dt = get_shift_datetimes(shift_date, shift_tmpl)
            s_start_str = s_start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            s_end_str = s_end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            is_night = shift_tmpl.is_overnight

            for team_id in teams_sorted:
                slot_reqs = coverage_by_slot.get((shift_date, team_id, shift_t_id), [])
                if not slot_reqs:
                    continue

                team_staff = sched_employees_by_team[team_id]
                # Track staff already assigned on this specific shift slot
                assigned_on_shift: Set[str] = set()

                # Sort requirements by skill scarcity (fewest holders first)
                sorted_reqs = sorted(
                    slot_reqs,
                    key=lambda r: (
                        team_skill_holder_count.get((team_id, r.skill_id), 999),
                        r.skill_id,
                    ),
                )

                # ==============================================================
                # PASS 1: Fill hard min_headcount slots
                # ==============================================================
                for req in sorted_reqs:
                    skill_id = req.skill_id
                    min_needed = req.min_headcount

                    for _ in range(min_needed):
                        # Find best eligible candidate
                        best_cand = None
                        best_score = None

                        for emp in team_staff:
                            emp_id = emp.employee_id
                            if emp_id in assigned_on_shift:
                                continue

                            # 1. Active employee hire/termination bounds
                            if shift_date < emp.hire_date:
                                continue
                            if emp.termination_date and shift_date > emp.termination_date:
                                continue

                            # 2. No overlapping shift on the same date (single shift per day rule)
                            if shift_date in emp_worked_dates[emp_id]:
                                continue

                            # 3. No approved leave on this date
                            if shift_date in emp_leave_dates[emp_id]:
                                continue

                            # 4. Valid certification on this date
                            certs = emp_certs[emp_id].get(skill_id, [])
                            valid_cert = any(
                                c_date <= shift_date and (exp_date is None or exp_date >= shift_date)
                                for c_date, exp_date in certs
                            )
                            if not valid_cert:
                                continue

                            # 5. Rest period compliance (>= 11h from last shift)
                            last_s = emp_last_shift[emp_id]
                            if last_s is not None:
                                prev_end = last_s[1]
                                rest_h = (s_start_dt - prev_end).total_seconds() / 3600.0
                                if rest_h < min_rest_hours - 1e-5:
                                    continue

                            # 6. Consecutive working days (<= 6 days)
                            # Adding shift_date would make consecutive days: check trailing window
                            recent_worked = 0
                            for offset in range(1, max_consecutive_days + 1):
                                if (shift_date - timedelta(days=offset)) in emp_worked_dates[emp_id]:
                                    recent_worked += 1
                                else:
                                    break
                            if recent_worked >= max_consecutive_days:
                                continue

                            # 7. Overtime cap check (<= 48h weekly paid hours)
                            cur_wk_hours = emp_week_hours[(emp_id, iso_year, iso_week)]
                            if cur_wk_hours + paid_hours_per_shift > max_weekly_hours + 1e-5:
                                continue

                            # Scoring for fairness:
                            # Primary: lowest weekly hours worked so far
                            # Secondary: rotation for night/weekend
                            rotation_penalty = 0
                            if is_night:
                                rotation_penalty += emp_night_shifts[emp_id]
                            if is_weekend:
                                rotation_penalty += emp_weekend_shifts[emp_id]

                            # Slight random tie-breaker using deterministic RNG
                            tie_breaker = rng.random()
                            score = (cur_wk_hours, rotation_penalty, tie_breaker)

                            if best_score is None or score < best_score:
                                best_score = score
                                best_cand = emp

                        if best_cand is not None:
                            emp_id = best_cand.employee_id
                            assignment_seq += 1
                            shift_inst_id = format_shift_instance_id(shift_date, team_id, shift_tmpl.template_code)
                            asn_id = format_assignment_id(shift_date.year, assignment_seq)

                            asn = ShiftAssignment(
                                assignment_id=asn_id,
                                shift_instance_id=shift_inst_id,
                                employee_id=emp_id,
                                team_id=team_id,
                                shift_template_id=shift_t_id,
                                shift_date=shift_date,
                                scheduled_start=s_start_str,
                                scheduled_end=s_end_str,
                                duration_hours=shift_tmpl.duration_hours,
                                assigned_role=best_cand.role,
                                assignment_status="ACTIVE",
                                effective_from=effective_from_ts,
                                effective_to=None,
                                swap_request_id=None,
                                replaces_assignment_id=None,
                                fulfills_skill_id=skill_id,
                                is_holiday_premium=is_statutory,
                            )
                            assignments.append(asn)
                            assigned_on_shift.add(emp_id)

                            # Update state
                            emp_worked_dates[emp_id].add(shift_date)
                            emp_last_shift[emp_id] = (s_start_dt, s_end_dt)
                            emp_week_hours[(emp_id, iso_year, iso_week)] += paid_hours_per_shift
                            if is_night:
                                emp_night_shifts[emp_id] += 1
                            if is_weekend:
                                emp_weekend_shifts[emp_id] += 1

                # ==============================================================
                # PASS 2: Fill toward target_headcount (regular hours only)
                # ==============================================================
                for req in sorted_reqs:
                    skill_id = req.skill_id
                    target_buffer_needed = max(0, req.target_headcount - req.min_headcount)
                    if target_buffer_needed <= 0:
                        continue

                    for _ in range(target_buffer_needed):
                        best_cand = None
                        best_score = None

                        for emp in team_staff:
                            emp_id = emp.employee_id
                            if emp_id in assigned_on_shift:
                                continue

                            # Target buffer rule: candidate must be strictly BELOW weekly_contract_hours
                            cur_wk_hours = emp_week_hours[(emp_id, iso_year, iso_week)]
                            if cur_wk_hours + paid_hours_per_shift > emp.weekly_contract_hours + 1e-5:
                                continue

                            # Standard checks
                            if shift_date < emp.hire_date:
                                continue
                            if emp.termination_date and shift_date > emp.termination_date:
                                continue
                            if shift_date in emp_worked_dates[emp_id]:
                                continue
                            if shift_date in emp_leave_dates[emp_id]:
                                continue

                            certs = emp_certs[emp_id].get(skill_id, [])
                            valid_cert = any(
                                c_date <= shift_date and (exp_date is None or exp_date >= shift_date)
                                for c_date, exp_date in certs
                            )
                            if not valid_cert:
                                continue

                            last_s = emp_last_shift[emp_id]
                            if last_s is not None:
                                prev_end = last_s[1]
                                rest_h = (s_start_dt - prev_end).total_seconds() / 3600.0
                                if rest_h < min_rest_hours - 1e-5:
                                    continue

                            recent_worked = 0
                            for offset in range(1, max_consecutive_days + 1):
                                if (shift_date - timedelta(days=offset)) in emp_worked_dates[emp_id]:
                                    recent_worked += 1
                                else:
                                    break
                            if recent_worked >= max_consecutive_days:
                                continue

                            rotation_penalty = 0
                            if is_night:
                                rotation_penalty += emp_night_shifts[emp_id]
                            if is_weekend:
                                rotation_penalty += emp_weekend_shifts[emp_id]

                            tie_breaker = rng.random()
                            score = (cur_wk_hours, rotation_penalty, tie_breaker)

                            if best_score is None or score < best_score:
                                best_score = score
                                best_cand = emp

                        if best_cand is not None:
                            emp_id = best_cand.employee_id
                            assignment_seq += 1
                            shift_inst_id = format_shift_instance_id(shift_date, team_id, shift_tmpl.template_code)
                            asn_id = format_assignment_id(shift_date.year, assignment_seq)

                            asn = ShiftAssignment(
                                assignment_id=asn_id,
                                shift_instance_id=shift_inst_id,
                                employee_id=emp_id,
                                team_id=team_id,
                                shift_template_id=shift_t_id,
                                shift_date=shift_date,
                                scheduled_start=s_start_str,
                                scheduled_end=s_end_str,
                                duration_hours=shift_tmpl.duration_hours,
                                assigned_role=best_cand.role,
                                assignment_status="ACTIVE",
                                effective_from=effective_from_ts,
                                effective_to=None,
                                swap_request_id=None,
                                replaces_assignment_id=None,
                                fulfills_skill_id=skill_id,
                                is_holiday_premium=is_statutory,
                            )
                            assignments.append(asn)
                            assigned_on_shift.add(emp_id)

                            emp_worked_dates[emp_id].add(shift_date)
                            emp_last_shift[emp_id] = (s_start_dt, s_end_dt)
                            emp_week_hours[(emp_id, iso_year, iso_week)] += paid_hours_per_shift
                            if is_night:
                                emp_night_shifts[emp_id] += 1
                            if is_weekend:
                                emp_weekend_shifts[emp_id] += 1

    return assignments

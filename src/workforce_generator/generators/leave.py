"""Generates LeaveRequest and LeaveRequestEvent records with coverage-aware decisions."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, time
import math
from typing import Dict, List, Optional, Set, Tuple
import numpy as np

from workforce_generator.config import GeneratorConfig
from workforce_generator.models.entities import (
    CoverageRequirement,
    Employee,
    EmployeeSkill,
    HolidayCalendar,
    LeaveRequest,
    LeaveRequestEvent,
)
from workforce_generator.models.identifiers import (
    format_leave_event_id,
    format_leave_request_id,
)


def generate_leave_requests_and_events(
    config: GeneratorConfig,
    rng: np.random.Generator,
    employees: List[Employee],
    employee_skills: List[EmployeeSkill],
    coverage_requirements: List[CoverageRequirement],
    holidays: List[HolidayCalendar],
) -> Tuple[List[LeaveRequest], List[LeaveRequestEvent]]:
    """Generates LeaveRequest and LeaveRequestEvent records adhering to all domain and coverage constraints."""
    horizon_start = config.horizon.start_date
    horizon_end = config.horizon.end_date
    horizon_days = (horizon_end - horizon_start).days + 1
    target_count = config.leave_rules.target_total_requests
    buffer_multiplier = config.leave_rules.leave_coverage_buffer

    # 1. Statutory holiday calendar dates lookup
    statutory_dates: Set[date] = {h.calendar_date for h in holidays if h.is_statutory}

    # 2. Team managers map: team_id -> manager_employee_id, list of managers, schedulable staff
    team_managers: Dict[str, str] = {}
    all_managers: List[Employee] = []
    schedulable_employees: List[Employee] = []
    emp_by_id: Dict[str, Employee] = {}

    for e in employees:
        emp_by_id[e.employee_id] = e
        if e.role == "MANAGER":
            team_managers[e.team_id] = e.employee_id
            all_managers.append(e)
        else:
            schedulable_employees.append(e)

    # 3. Employee skill mappings: emp_id -> set of skill_ids
    emp_skills: Dict[str, Set[str]] = defaultdict(set)
    for es in employee_skills:
        emp_skills[es.employee_id].add(es.skill_id)

    # 4. Coverage demand lookup: precompute demand_by_team_date[(team_id, date)] -> {skill_id: min_sum}
    demand_by_team_date: Dict[Tuple[str, date], Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for cov in coverage_requirements:
        demand_by_team_date[(cov.team_id, cov.effective_date)][cov.skill_id] += cov.min_headcount

    # 5. Precompute active qualified holders by team and skill: (team_id, skill_id) -> list of Employees
    team_skill_holders: Dict[Tuple[str, str], List[Employee]] = defaultdict(list)
    for staff in schedulable_employees:
        for sk_id in emp_skills[staff.employee_id]:
            team_skill_holders[(staff.team_id, sk_id)].append(staff)

    # 6. Holiday proximity weighting for Annual Leave
    day_weights = np.ones(horizon_days, dtype=float)
    date_list = [horizon_start + timedelta(days=i) for i in range(horizon_days)]
    for i, d in enumerate(date_list):
        min_dist = min([abs((d - h_d).days) for h_d in statutory_dates], default=999)
        if min_dist <= 2:
            day_weights[i] = 3.5
        elif min_dist <= 4:
            day_weights[i] = 2.0
        elif d.weekday() in (4, 0): # Friday or Monday
            day_weights[i] = 1.3
    day_probs = day_weights / day_weights.sum()

    # Leave type distribution
    leave_type_codes = ["ANNUAL", "SICK", "UNPAID", "PARENTAL", "BEREAVEMENT"]
    leave_type_weights = np.array([0.65, 0.22, 0.07, 0.03, 0.03], dtype=float)
    leave_type_weights /= leave_type_weights.sum()

    # 7. Generate Candidate Leave Requests
    candidate_requests: List[Dict] = []
    year = horizon_start.year

    for seq in range(1, target_count + 1):
        # Guarantee request #1 is an ANNUAL leave spanning statutory holiday 2026-01-01 and workday 2026-01-02
        if seq == 1 and date(2026, 1, 1) in statutory_dates and horizon_start <= date(2026, 1, 1):
            # Select full-time schedulable employee active across Jan 1-2
            candidates_jan1 = [
                e for e in schedulable_employees
                if e.hire_date <= date(2026, 1, 1)
                and (e.termination_date is None or e.termination_date >= date(2026, 1, 2))
                and e.weekly_contract_hours == 40.0
            ]
            emp = candidates_jan1[0] if candidates_jan1 else schedulable_employees[0]
            l_type = "ANNUAL"
            start_date = date(2026, 1, 1)
            end_date = date(2026, 1, 2)
            eff_start = emp.hire_date
            eff_end = emp.termination_date if emp.termination_date else horizon_end

            # Planned: submitted in late December 2025
            sub_dt = datetime(2025, 12, 15, 9, 0)
            dec_dt = datetime(2025, 12, 17, 14, 0)
            submitted_at = sub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            decided_at = dec_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            # 95% from schedulable employees, 5% from managers
            if all_managers and rng.random() < 0.05:
                emp = all_managers[int(rng.integers(0, len(all_managers)))]
            else:
                emp = schedulable_employees[int(rng.integers(0, len(schedulable_employees)))]

            eff_start = max(horizon_start, emp.hire_date)
            eff_end = min(horizon_end, emp.termination_date) if emp.termination_date else horizon_end

            # Strict > guard and safe fallback pool
            if eff_start >= eff_end:
                active_pool = [
                    e for e in schedulable_employees
                    if max(horizon_start, e.hire_date) < (min(horizon_end, e.termination_date) if e.termination_date else horizon_end)
                ]
                if not active_pool:
                    active_pool = schedulable_employees
                emp = active_pool[int(rng.integers(0, len(active_pool)))]
                eff_start = max(horizon_start, emp.hire_date)
                eff_end = min(horizon_end, emp.termination_date) if emp.termination_date else horizon_end

            l_type = str(rng.choice(leave_type_codes, p=leave_type_weights))

            if l_type == "SICK":
                duration = int(rng.choice([1, 2], p=[0.75, 0.25]))
                day_idx = int(rng.integers(0, (eff_end - eff_start).days + 1))
                start_date = eff_start + timedelta(days=day_idx)
                end_date = min(eff_end, start_date + timedelta(days=duration - 1))

                # Unplanned: submitted morning of start_date or evening before
                sub_hour = int(rng.integers(6, 9))
                sub_min = int(rng.integers(0, 60))
                # Can be submitted on start_date
                sub_dt = datetime.combine(start_date, time(sub_hour, sub_min))
                if rng.random() < 0.40:
                    sub_dt -= timedelta(hours=int(rng.integers(10, 16)))
                submitted_at = sub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

                # Decided within 1-3 hours
                dec_dt = sub_dt + timedelta(hours=int(rng.integers(1, 4)), minutes=int(rng.integers(5, 55)))
                decided_at = dec_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            elif l_type == "ANNUAL":
                # Shorter durations in starter (2-4 days) to fit tight 14-day horizon; 2-7 days at scale
                max_dur = 4 if horizon_days <= 14 else 7
                duration = int(rng.integers(2, max_dur + 1))
                active_slice_start = (eff_start - horizon_start).days
                active_slice_end = (eff_end - horizon_start).days + 1
                slice_probs = day_probs[active_slice_start:active_slice_end].copy()
                slice_probs /= slice_probs.sum()
                chosen_offset = int(rng.choice(len(slice_probs), p=slice_probs))
                start_date = eff_start + timedelta(days=chosen_offset)
                end_date = min(eff_end, start_date + timedelta(days=duration - 1))

                # Planned: submitted strictly BEFORE start_date (7 to 35 days in advance)
                notice_days = int(rng.integers(7, 36))
                sub_dt = datetime.combine(start_date - timedelta(days=notice_days), time(int(rng.integers(8, 17)), int(rng.integers(0, 60))))
                submitted_at = sub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

                dec_dt = sub_dt + timedelta(days=int(rng.integers(1, 4)), hours=int(rng.integers(1, 8)))
                decided_at = dec_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            elif l_type == "UNPAID":
                duration = int(rng.integers(1, 4))
                day_idx = int(rng.integers(0, max(1, (eff_end - eff_start).days - duration + 1)))
                start_date = eff_start + timedelta(days=day_idx)
                end_date = min(eff_end, start_date + timedelta(days=duration - 1))

                notice_days = int(rng.integers(3, 15))
                sub_dt = datetime.combine(start_date - timedelta(days=notice_days), time(9, 30))
                submitted_at = sub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                dec_dt = sub_dt + timedelta(days=int(rng.integers(1, 3)))
                decided_at = dec_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            elif l_type == "PARENTAL":
                duration = int(rng.integers(10, min(21, horizon_days)))
                day_idx = int(rng.integers(0, max(1, (eff_end - eff_start).days - duration + 1)))
                start_date = eff_start + timedelta(days=day_idx)
                end_date = min(eff_end, start_date + timedelta(days=duration - 1))

                notice_days = int(rng.integers(30, 61))
                sub_dt = datetime.combine(start_date - timedelta(days=notice_days), time(10, 0))
                submitted_at = sub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                dec_dt = sub_dt + timedelta(days=int(rng.integers(2, 6)))
                decided_at = dec_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            else: # BEREAVEMENT
                duration = int(rng.integers(2, 4))
                day_idx = int(rng.integers(0, max(1, (eff_end - eff_start).days - duration + 1)))
                start_date = eff_start + timedelta(days=day_idx)
                end_date = min(eff_end, start_date + timedelta(days=duration - 1))

                sub_dt = datetime.combine(start_date - timedelta(days=1), time(18, 0))
                submitted_at = sub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                dec_dt = sub_dt + timedelta(hours=int(rng.integers(2, 6)))
                decided_at = dec_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Clamp submitted_at >= hire_date
            hire_midnight = datetime.combine(emp.hire_date, time(0, 0))
            if sub_dt < hire_midnight:
                sub_dt = hire_midnight
            if dec_dt <= sub_dt:
                dec_dt = sub_dt + timedelta(hours=int(rng.integers(1, 4)), minutes=int(rng.integers(5, 55)))

            submitted_at = sub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            decided_at = dec_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Compute requested hours with Statutory Holiday Exemption rule
        daily_hours = round(emp.weekly_contract_hours / 5.0, 1)
        cur_d = start_date
        total_hours = 0.0

        while cur_d <= end_date:
            is_statutory = cur_d in statutory_dates
            is_weekend = cur_d.weekday() in (5, 6)

            if l_type == "ANNUAL":
                # Statutory holidays do not deduct annual leave hours
                if not is_statutory and not is_weekend:
                    total_hours += daily_hours
            else:
                if not is_weekend:
                    total_hours += daily_hours

            cur_d += timedelta(days=1)

        if total_hours <= 0.0:
            total_hours = daily_hours

        candidate_requests.append({
            "seq": seq,
            "employee_id": emp.employee_id,
            "team_id": emp.team_id,
            "is_manager": (emp.role == "MANAGER"),
            "leave_type": l_type,
            "start_date": start_date,
            "end_date": end_date,
            "requested_hours": round(total_hours, 1),
            "submitted_at": submitted_at,
            "decided_at": decided_at,
            "submitted_dt": sub_dt,
            "decided_dt": dec_dt,
        })

    # 8. Sort Candidate Requests Chronologically by Submission Time
    candidate_requests.sort(key=lambda r: (r["submitted_dt"], r["seq"]))

    # 9. Lifecycle Selection:
    # A few requests left SUBMITTED (e.g. 2 in starter, ~1-2% at scale)
    # A few requests cancelled BEFORE decision (e.g. 2 in starter, ~1-2% at scale)
    num_submitted_only = max(2, int(round(target_count * 0.02)))
    num_cancelled_before_dec = max(2, int(round(target_count * 0.02)))

    # Choose from the tail of submissions for submitted_only (recent submissions)
    submitted_only_indices = set(range(target_count - num_submitted_only, target_count))

    # Choose candidate indices for cancelled before decision (non-overlapping with submitted_only)
    available_for_pre_cancel = [i for i in range(target_count) if i not in submitted_only_indices]
    pre_cancel_indices = set(rng.choice(available_for_pre_cancel, size=num_cancelled_before_dec, replace=False))

    # Track approved leave periods: employee_id -> list of (start_date, end_date)
    approved_leave_ranges_by_emp: Dict[str, List[Tuple[date, date]]] = defaultdict(list)
    # Track approved on leave per team and date: (team_id, date) -> set of employee_ids
    approved_leave_by_team_date: Dict[Tuple[str, date], Set[str]] = defaultdict(set)

    leave_requests: List[LeaveRequest] = []
    leave_events: List[LeaveRequestEvent] = []
    evt_seq = 0

    for idx, cand in enumerate(candidate_requests, start=1):
        cand_idx = idx - 1
        req_id = format_leave_request_id(year, idx)
        cand_emp_id = cand["employee_id"]
        t_id = cand["team_id"]
        s_date = cand["start_date"]
        e_date = cand["end_date"]
        is_mgr = cand["is_manager"]

        # Determine decision actor:
        # If requester is MANAGER: decided by SYSTEM or a manager of another team
        # If requester is non-manager: decided by their team's MANAGER or SYSTEM
        # In all cases: actor_id != cand_emp_id
        if is_mgr:
            if rng.random() < 0.50:
                dec_actor_type = "SYSTEM"
                dec_actor_id = None
            else:
                dec_actor_type = "EMPLOYEE"
                other_mgrs = [m for m in all_managers if m.employee_id != cand_emp_id]
                dec_actor_id = other_mgrs[int(rng.integers(0, len(other_mgrs)))].employee_id if other_mgrs else None
                if dec_actor_id is None:
                    dec_actor_type = "SYSTEM"
        else:
            if rng.random() < 0.15:
                dec_actor_type = "SYSTEM"
                dec_actor_id = None
            else:
                dec_actor_type = "EMPLOYEE"
                dec_actor_id = team_managers.get(t_id)

        # CASE A: Left SUBMITTED (pending review)
        if cand_idx in submitted_only_indices:
            evt_seq += 1
            sub_evt = LeaveRequestEvent(
                leave_event_id=format_leave_event_id(year, evt_seq),
                leave_request_id=req_id,
                from_status=None,
                to_status="SUBMITTED",
                event_timestamp=cand["submitted_at"],
                actor_type="EMPLOYEE",
                actor_id=cand_emp_id,
                reason="Initial leave request submission",
            )
            leave_events.append(sub_evt)

            req_obj = LeaveRequest(
                leave_request_id=req_id,
                employee_id=cand_emp_id,
                leave_type=cand["leave_type"],
                start_date=s_date,
                end_date=e_date,
                requested_hours=cand["requested_hours"],
                current_status="SUBMITTED",
                submitted_at=cand["submitted_at"],
                decided_at=None,
                decision_reason=None,
            )
            leave_requests.append(req_obj)
            continue

        # CASE B: Cancelled BEFORE decision
        if cand_idx in pre_cancel_indices:
            # Event 1: SUBMITTED
            evt_seq += 1
            sub_evt = LeaveRequestEvent(
                leave_event_id=format_leave_event_id(year, evt_seq),
                leave_request_id=req_id,
                from_status=None,
                to_status="SUBMITTED",
                event_timestamp=cand["submitted_at"],
                actor_type="EMPLOYEE",
                actor_id=cand_emp_id,
                reason="Initial leave request submission",
            )
            leave_events.append(sub_evt)

            # Event 2: CANCELLED (before decided_at, and before end_date)
            sub_dt = cand["submitted_dt"]
            # Cancel 2 to 12 hours after submission
            cancel_dt = sub_dt + timedelta(hours=int(rng.integers(2, 13)))
            cancel_ts = cancel_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            evt_seq += 1
            cancel_evt = LeaveRequestEvent(
                leave_event_id=format_leave_event_id(year, evt_seq),
                leave_request_id=req_id,
                from_status="SUBMITTED",
                to_status="CANCELLED",
                event_timestamp=cancel_ts,
                actor_type="EMPLOYEE",
                actor_id=cand_emp_id,
                reason="Cancelled by employee prior to manager decision",
            )
            leave_events.append(cancel_evt)

            req_obj = LeaveRequest(
                leave_request_id=req_id,
                employee_id=cand_emp_id,
                leave_type=cand["leave_type"],
                start_date=s_date,
                end_date=e_date,
                requested_hours=cand["requested_hours"],
                current_status="CANCELLED",
                submitted_at=cand["submitted_at"],
                decided_at=None,
                decision_reason=None,
            )
            leave_requests.append(req_obj)
            continue

        # CASE C: Standard Evaluation (Approve or Reject)
        # Check 1: Overlapping approved leave for the same employee
        has_self_overlap = any(
            not (e_date < ex_start or s_date > ex_end)
            for ex_start, ex_end in approved_leave_ranges_by_emp[cand_emp_id]
        )

        can_approve = True
        rejection_reason: Optional[str] = None

        if has_self_overlap:
            can_approve = False
            rejection_reason = "Overlapping approved leave request already exists for this employee"

        # Check 2: SICK leave exemption rule:
        # Statutory sick leave is unplanned illness and exempt from coverage threshold constraints
        elif cand["leave_type"] == "SICK":
            can_approve = True
            dec_reason = "Approved: Statutory sick leave is exempt from operational coverage constraints"

        # Check 3: Managers are not scheduled for operational coverage
        elif is_mgr:
            can_approve = True
            dec_reason = "Approved: Manager administrative leave approved by supervisory authority"

        # Check 4: Planned leave coverage threshold check
        else:
            cur_check_date = s_date
            emp_holds = emp_skills[cand_emp_id]

            while cur_check_date <= e_date:
                team_date_demands = demand_by_team_date.get((t_id, cur_check_date), {})
                current_on_leave = approved_leave_by_team_date[(t_id, cur_check_date)]

                for sk_id, min_cnt in team_date_demands.items():
                    if min_cnt <= 0 or sk_id not in emp_holds:
                        continue

                    required_threshold = math.ceil(min_cnt * buffer_multiplier)
                    holders = team_skill_holders.get((t_id, sk_id), [])

                    qualified_active_non_leave = sum(
                        1 for staff in holders
                        if staff.employee_id not in current_on_leave
                        and staff.hire_date <= cur_check_date
                        and (staff.termination_date is None or staff.termination_date >= cur_check_date)
                    )

                    remaining_if_approved = qualified_active_non_leave - 1

                    if remaining_if_approved < required_threshold:
                        can_approve = False
                        rejection_reason = (
                            f"Insufficient coverage for skill {sk_id} on {cur_check_date}: "
                            f"remaining qualified staff ({remaining_if_approved}) < required threshold ({required_threshold})"
                        )
                        break

                if not can_approve:
                    break

                cur_check_date += timedelta(days=1)

            if can_approve:
                dec_reason = "Approved: Daily staffing buffer satisfied across all requested dates and skills"

        # Record Decision
        if can_approve:
            final_status = "APPROVED"
            # Update approved calendar
            approved_leave_ranges_by_emp[cand_emp_id].append((s_date, e_date))
            cur_mark = s_date
            while cur_mark <= e_date:
                approved_leave_by_team_date[(t_id, cur_mark)].add(cand_emp_id)
                cur_mark += timedelta(days=1)
        else:
            final_status = "REJECTED"
            dec_reason = rejection_reason

        # Event 1: SUBMISSION
        evt_seq += 1
        sub_evt = LeaveRequestEvent(
            leave_event_id=format_leave_event_id(year, evt_seq),
            leave_request_id=req_id,
            from_status=None,
            to_status="SUBMITTED",
            event_timestamp=cand["submitted_at"],
            actor_type="EMPLOYEE",
            actor_id=cand_emp_id,
            reason="Initial leave request submission",
        )
        leave_events.append(sub_evt)

        # Event 2: DECISION
        evt_seq += 1
        dec_evt = LeaveRequestEvent(
            leave_event_id=format_leave_event_id(year, evt_seq),
            leave_request_id=req_id,
            from_status="SUBMITTED",
            to_status=final_status,
            event_timestamp=cand["decided_at"],
            actor_type=dec_actor_type,
            actor_id=dec_actor_id,
            reason=dec_reason,
        )
        leave_events.append(dec_evt)

        req_obj = LeaveRequest(
            leave_request_id=req_id,
            employee_id=cand_emp_id,
            leave_type=cand["leave_type"],
            start_date=s_date,
            end_date=e_date,
            requested_hours=cand["requested_hours"],
            current_status=final_status,
            submitted_at=cand["submitted_at"],
            decided_at=cand["decided_at"],
            decision_reason=dec_reason,
        )
        leave_requests.append(req_obj)

    # 10. Cancellation AFTER Approval
    # Select roughly 5% of APPROVED requests to be cancelled before end_date by index
    approved_indices = [i for i, r in enumerate(leave_requests) if r.current_status == "APPROVED"]
    num_post_cancel = max(1, int(round(len(approved_indices) * 0.05))) if approved_indices else 0

    if num_post_cancel > 0:
        chosen_cancel_indices = list(rng.choice(approved_indices, size=num_post_cancel, replace=False))
        for c_idx in chosen_cancel_indices:
            req = leave_requests[c_idx]
            # Naive UTC datetime parsing: remove Z
            dec_dt = datetime.fromisoformat(req.decided_at.rstrip("Z"))
            # Cancellation must happen strictly before end_date
            end_dt = datetime.combine(req.end_date, time(18, 0))

            time_gap = (end_dt - dec_dt).total_seconds()
            if time_gap > 3600:
                cancel_offset = int(rng.integers(1800, max(1801, int(time_gap * 0.8))))
                cancel_dt = dec_dt + timedelta(seconds=cancel_offset)
            else:
                cancel_dt = dec_dt + timedelta(minutes=15)

            cancel_ts = cancel_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            evt_seq += 1
            cancel_evt = LeaveRequestEvent(
                leave_event_id=format_leave_event_id(year, evt_seq),
                leave_request_id=req.leave_request_id,
                from_status="APPROVED",
                to_status="CANCELLED",
                event_timestamp=cancel_ts,
                actor_type="EMPLOYEE",
                actor_id=req.employee_id,
                reason="Cancelled by employee prior to leave conclusion",
            )
            leave_events.append(cancel_evt)

            # Update status so current_status == latest event to_status
            req.current_status = "CANCELLED"
            # Release from approved calendar
            if (req.start_date, req.end_date) in approved_leave_ranges_by_emp[req.employee_id]:
                approved_leave_ranges_by_emp[req.employee_id].remove((req.start_date, req.end_date))

    return leave_requests, leave_events

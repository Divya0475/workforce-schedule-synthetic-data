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

    # 2. Team managers map: team_id -> manager_employee_id
    team_managers: Dict[str, str] = {}
    schedulable_employees: List[Employee] = []
    emp_by_id: Dict[str, Employee] = {}

    for e in employees:
        emp_by_id[e.employee_id] = e
        if e.role == "MANAGER":
            team_managers[e.team_id] = e.employee_id
        else:
            schedulable_employees.append(e)

    # 3. Employee skill mappings: emp_id -> set of skill_ids
    emp_skills: Dict[str, Set[str]] = defaultdict(set)
    for es in employee_skills:
        emp_skills[es.employee_id].add(es.skill_id)

    # 4. Coverage demand lookup: (team_id, date, skill_id) -> sum of min_headcount
    daily_min_sum: Dict[Tuple[str, date, str], int] = defaultdict(int)
    for cov in coverage_requirements:
        daily_min_sum[(cov.team_id, cov.effective_date, cov.skill_id)] += cov.min_headcount

    # 5. Precompute active qualified holders by team and skill: (team_id, skill_id) -> list of Employees
    team_skill_holders: Dict[Tuple[str, str], List[Employee]] = defaultdict(list)
    for staff in schedulable_employees:
        for sk_id in emp_skills[staff.employee_id]:
            team_skill_holders[(staff.team_id, sk_id)].append(staff)

    # 6. Holiday proximity weighting for Annual Leave
    # Calculate weights for each day in horizon: days within 3 days of statutory holidays get higher weight
    day_weights = np.ones(horizon_days, dtype=float)
    date_list = [horizon_start + timedelta(days=i) for i in range(horizon_days)]
    for i, d in enumerate(date_list):
        # Distance to closest statutory holiday
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
        # Pick an active schedulable employee
        emp = schedulable_employees[int(rng.integers(0, len(schedulable_employees)))]

        # Determine employee active boundaries
        eff_start = max(horizon_start, emp.hire_date)
        eff_end = min(horizon_end, emp.termination_date) if emp.termination_date else horizon_end

        if eff_start >= eff_end:
            # If not active during horizon, pick another employee who is active
            active_pool = [e for e in schedulable_employees if e.hire_date <= horizon_start and (e.termination_date is None or e.termination_date >= horizon_end)]
            emp = active_pool[int(rng.integers(0, len(active_pool)))]
            eff_start = horizon_start
            eff_end = horizon_end

        l_type = str(rng.choice(leave_type_codes, p=leave_type_weights))

        # Determine duration and start date based on leave type
        if l_type == "SICK":
            duration = int(rng.choice([1, 2, 3], p=[0.70, 0.25, 0.05]))
            # Sick leave is unplanned, uniformly distributed across active days
            day_idx = int(rng.integers(0, (eff_end - eff_start).days + 1))
            start_date = eff_start + timedelta(days=day_idx)
            end_date = min(eff_end, start_date + timedelta(days=duration - 1))

            # Unplanned: submitted same day or evening before
            sub_hour = int(rng.integers(5, 9))
            sub_min = int(rng.integers(0, 60))
            sub_dt = datetime.combine(start_date, time(sub_hour, sub_min)) - timedelta(hours=int(rng.choice([0, 12, 18])))
            submitted_at = sub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Decided within 1-3 hours of submission
            dec_dt = sub_dt + timedelta(hours=int(rng.integers(1, 4)), minutes=int(rng.integers(5, 55)))
            decided_at = dec_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        elif l_type == "ANNUAL":
            duration = int(rng.integers(2, 8))
            # Pick start date biased towards holidays
            # Filter day_probs to employee active range
            active_slice_start = (eff_start - horizon_start).days
            active_slice_end = (eff_end - horizon_start).days + 1
            slice_probs = day_probs[active_slice_start:active_slice_end].copy()
            slice_probs /= slice_probs.sum()
            chosen_offset = int(rng.choice(len(slice_probs), p=slice_probs))
            start_date = eff_start + timedelta(days=chosen_offset)
            end_date = min(eff_end, start_date + timedelta(days=duration - 1))

            # Planned: submitted 7 to 45 days in advance
            notice_days = int(rng.integers(7, 46))
            sub_dt = datetime.combine(start_date - timedelta(days=notice_days), time(int(rng.integers(8, 17)), int(rng.integers(0, 60))))
            submitted_at = sub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Decided 1 to 3 days after submission
            dec_dt = sub_dt + timedelta(days=int(rng.integers(1, 4)), hours=int(rng.integers(1, 8)))
            decided_at = dec_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        elif l_type == "UNPAID":
            duration = int(rng.integers(1, 5))
            day_idx = int(rng.integers(0, max(1, (eff_end - eff_start).days - duration + 1)))
            start_date = eff_start + timedelta(days=day_idx)
            end_date = min(eff_end, start_date + timedelta(days=duration - 1))

            notice_days = int(rng.integers(3, 15))
            sub_dt = datetime.combine(start_date - timedelta(days=notice_days), time(9, 30))
            submitted_at = sub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            dec_dt = sub_dt + timedelta(days=int(rng.integers(1, 3)))
            decided_at = dec_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        elif l_type == "PARENTAL":
            duration = int(rng.integers(10, 21))
            day_idx = int(rng.integers(0, max(1, (eff_end - eff_start).days - duration + 1)))
            start_date = eff_start + timedelta(days=day_idx)
            end_date = min(eff_end, start_date + timedelta(days=duration - 1))

            notice_days = int(rng.integers(30, 61))
            sub_dt = datetime.combine(start_date - timedelta(days=notice_days), time(10, 0))
            submitted_at = sub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            dec_dt = sub_dt + timedelta(days=int(rng.integers(2, 6)))
            decided_at = dec_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        else: # BEREAVEMENT
            duration = int(rng.integers(2, 5))
            day_idx = int(rng.integers(0, max(1, (eff_end - eff_start).days - duration + 1)))
            start_date = eff_start + timedelta(days=day_idx)
            end_date = min(eff_end, start_date + timedelta(days=duration - 1))

            sub_dt = datetime.combine(start_date, time(8, 0)) - timedelta(hours=int(rng.integers(2, 16)))
            submitted_at = sub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            dec_dt = sub_dt + timedelta(hours=int(rng.integers(1, 5)))
            decided_at = dec_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Compute requested hours with Statutory Holiday Exemption rule:
        # Statutory holidays do not deduct annual leave hours
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
                # Other leaves (Sick, Unpaid, Bereavement, Parental)
                if not is_weekend:
                    total_hours += daily_hours

            cur_d += timedelta(days=1)

        if total_hours <= 0.0:
            total_hours = daily_hours # Guaranteed positive minimum if single-day window on non-workday

        candidate_requests.append({
            "seq": seq,
            "employee_id": emp.employee_id,
            "team_id": emp.team_id,
            "leave_type": l_type,
            "start_date": start_date,
            "end_date": end_date,
            "requested_hours": round(total_hours, 1),
            "submitted_at": submitted_at,
            "decided_at": decided_at,
            "submitted_dt": sub_dt,
            "decided_dt": dec_dt,
        })

    # 8. Sort Candidate Requests in Exact Chronological Submission-Time Order
    candidate_requests.sort(key=lambda r: (r["submitted_dt"], r["seq"]))

    # 9. Evaluate Decisions Chronologically
    # Track approved employees on leave: (team_id, date) -> set of employee_ids
    approved_leave_by_team_date: Dict[Tuple[str, date], Set[str]] = defaultdict(set)

    leave_requests: List[LeaveRequest] = []
    leave_events: List[LeaveRequestEvent] = []
    evt_seq = 0

    for idx, cand in enumerate(candidate_requests, start=1):
        req_id = format_leave_request_id(year, idx)
        cand_emp_id = cand["employee_id"]
        t_id = cand["team_id"]
        s_date = cand["start_date"]
        e_date = cand["end_date"]
        team_mgr_id = team_managers.get(t_id)

        # Counterfactual simulation: check if approving this request violates daily coverage
        can_approve = True
        rejection_reason: Optional[str] = None

        cur_check_date = s_date
        while cur_check_date <= e_date:
            # Check every skill demanded for team t_id on this date
            # Find demanded skills on cur_check_date
            demanded_skills = [
                sk for (tm, dt, sk), min_cnt in daily_min_sum.items()
                if tm == t_id and dt == cur_check_date and min_cnt > 0
            ]

            emp_holds = emp_skills[cand_emp_id]

            for sk_id in demanded_skills:
                # Only skills held by the candidate employee can be affected by their leave
                if sk_id not in emp_holds:
                    continue

                min_cnt = daily_min_sum[(t_id, cur_check_date, sk_id)]
                required_threshold = math.ceil(min_cnt * buffer_multiplier)

                # Count qualified non-manager active employees in team who are NOT on approved leave
                current_on_leave = approved_leave_by_team_date[(t_id, cur_check_date)]
                holders = team_skill_holders.get((t_id, sk_id), [])

                qualified_active_non_leave = sum(
                    1 for staff in holders
                    if staff.employee_id not in current_on_leave
                    and staff.hire_date <= cur_check_date
                    and (staff.termination_date is None or staff.termination_date >= cur_check_date)
                )

                # If candidate takes leave, remaining qualified non-leave staff:
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

        # Decision Outcome
        if can_approve:
            final_status = "APPROVED"
            dec_reason = "Approved: Daily staffing buffer satisfied across all requested dates and skills"
            # Mark employee on leave for requested dates
            cur_mark_date = s_date
            while cur_mark_date <= e_date:
                approved_leave_by_team_date[(t_id, cur_mark_date)].add(cand_emp_id)
                cur_mark_date += timedelta(days=1)
        else:
            final_status = "REJECTED"
            dec_reason = rejection_reason

        # 10. Generate Lifecycle Events
        # Event 1: Initial Submission
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

        # Event 2: Decision (Approved or Rejected)
        evt_seq += 1
        # Actor is team's MANAGER (85%) or SYSTEM (15%)
        is_system_actor = bool(rng.random() < 0.15)
        actor_type = "SYSTEM" if is_system_actor else "EMPLOYEE"
        actor_id = None if is_system_actor else team_mgr_id

        dec_evt = LeaveRequestEvent(
            leave_event_id=format_leave_event_id(year, evt_seq),
            leave_request_id=req_id,
            from_status="SUBMITTED",
            to_status=final_status,
            event_timestamp=cand["decided_at"],
            actor_type=actor_type,
            actor_id=actor_id,
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

    # 11. Cancellation Lifecycle: Some approved leaves are later CANCELLED
    # Select roughly 5% of approved leaves for cancellation before start_date
    approved_indices = [i for i, r in enumerate(leave_requests) if r.current_status == "APPROVED"]
    num_to_cancel = max(1, int(round(len(approved_indices) * 0.05))) if approved_indices else 0

    if num_to_cancel > 0:
        cancel_indices = list(rng.choice(approved_indices, size=num_to_cancel, replace=False))
        for c_idx in cancel_indices:
            req = leave_requests[c_idx]
            # Cancellation timestamp: between decided_at and start_date
            dec_dt = datetime.fromisoformat(req.decided_at.replace("Z", "+00:00"))
            start_dt = datetime.combine(req.start_date, time(8, 0)).astimezone(dec_dt.tzinfo)

            # Ensure cancel timestamp is strictly between decided_at and start_date
            time_gap = (start_dt - dec_dt).total_seconds()
            if time_gap > 3600:
                cancel_offset_sec = int(rng.integers(1800, int(time_gap * 0.85)))
                cancel_dt = dec_dt + timedelta(seconds=cancel_offset_sec)
            else:
                cancel_dt = dec_dt + timedelta(minutes=30)

            cancel_ts = cancel_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Event 3: Cancellation
            evt_seq += 1
            cancel_evt = LeaveRequestEvent(
                leave_event_id=format_leave_event_id(year, evt_seq),
                leave_request_id=req.leave_request_id,
                from_status="APPROVED",
                to_status="CANCELLED",
                event_timestamp=cancel_ts,
                actor_type="EMPLOYEE",
                actor_id=req.employee_id,
                reason="Cancelled by employee prior to leave commencement",
            )
            leave_events.append(cancel_evt)

            # Update request status so current_status == latest event to_status
            req.current_status = "CANCELLED"

    return leave_requests, leave_events

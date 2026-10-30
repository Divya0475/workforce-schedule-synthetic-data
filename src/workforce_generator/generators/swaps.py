"""Generate eligible peer shift swaps with append-only assignment history."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from typing import List, Tuple

import numpy as np

from workforce_generator.config import GeneratorConfig
from workforce_generator.models.entities import (
    Employee,
    EmployeeSkill,
    LeaveRequest,
    ShiftAssignment,
    SwapRequest,
    SwapRequestEvent,
)
from workforce_generator.models.identifiers import (
    format_assignment_id,
    format_swap_event_id,
    format_swap_request_id,
    format_shift_instance_id,
)
from workforce_generator.rules import check_swap_eligibility, parse_iso_utc


def _format_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_swap_requests_and_events(
    cfg: GeneratorConfig,
    rng: np.random.Generator,
    employees: List[Employee],
    employee_skills: List[EmployeeSkill],
    assignments: List[ShiftAssignment],
    leave_requests: List[LeaveRequest],
) -> Tuple[List[SwapRequest], List[SwapRequestEvent], List[ShiftAssignment]]:
    """Generate requested, rejected, and applied exchanges after baseline scheduling."""
    employee_map = {employee.employee_id: employee for employee in employees}
    managers_by_team = defaultdict(list)
    certifications_by_employee_skill = defaultdict(list)
    for employee in employees:
        if employee.role == "MANAGER":
            managers_by_team[employee.team_id].append(employee)
    for skill in employee_skills:
        certifications_by_employee_skill[(skill.employee_id, skill.skill_id)].append(
            (skill.certified_date, skill.expiry_date)
        )

    approved_leave_dates_by_employee = defaultdict(set)
    for leave in leave_requests:
        if leave.current_status != "APPROVED":
            continue
        current_date = leave.start_date
        while current_date <= leave.end_date:
            approved_leave_dates_by_employee[leave.employee_id].add(current_date)
            current_date += timedelta(days=1)

    active_by_team_week = defaultdict(list)
    active_by_employee = defaultdict(list)
    for assignment in assignments:
        if assignment.assignment_status != cfg.swap_rules.swapped_out_status:
            if assignment.assignment_status == cfg.swap_rules.active_status:
                active_by_employee[assignment.employee_id].append(assignment)
                iso_year, iso_week, _ = assignment.shift_date.isocalendar()
                active_by_team_week[(assignment.team_id, iso_year, iso_week)].append(assignment)

    request_rate = cfg.swap_rules.request_rate_per_assignment
    requested_ids = set()
    swaps: List[SwapRequest] = []
    events: List[SwapRequestEvent] = []
    next_assignment_seq = len(assignments)
    request_seq = 0
    event_seq = 0
    assignment_index = {assignment.assignment_id: index for index, assignment in enumerate(assignments)}

    for group_key in sorted(active_by_team_week):
        group_assignments = active_by_team_week[group_key]
        if len(group_assignments) < 2:
            continue
        order = rng.permutation(len(group_assignments))
        for source_index in order:
            source = group_assignments[int(source_index)]
            if source.assignment_id in requested_ids or rng.random() >= request_rate:
                continue
            possible_targets = [
                candidate
                for candidate in group_assignments
                if candidate.assignment_id not in requested_ids
                and candidate.employee_id != source.employee_id
            ]
            if not possible_targets:
                continue
            target_order = rng.permutation(len(possible_targets))
            selected = None
            for target_index in target_order[: min(20, len(target_order))]:
                candidate = possible_targets[int(target_index)]
                requester = employee_map[source.employee_id]
                target_employee = employee_map[candidate.employee_id]
                eligibility_errors = check_swap_eligibility(
                    source,
                    candidate,
                    requester,
                    target_employee,
                    assignments,
                    employee_skills,
                    leave_requests,
                    min_rest_hours=cfg.labor_constraints.min_rest_hours_between_shifts,
                    max_consecutive_days=cfg.labor_constraints.max_consecutive_working_days,
                    max_weekly_hours=cfg.labor_constraints.overtime_cap_weekly_hours,
                    paid_hours_per_shift=cfg.labor_constraints.paid_hours_per_shift,
                    assignments_by_employee=active_by_employee,
                    certifications_by_employee_skill=certifications_by_employee_skill,
                    approved_leave_dates_by_employee=approved_leave_dates_by_employee,
                )
                if not eligibility_errors:
                    selected = candidate
                    break
            if selected is None:
                continue

            target = selected
            requested_ids.update((source.assignment_id, target.assignment_id))
            request_seq += 1
            year = min(source.shift_date, target.shift_date).year
            request_id = format_swap_request_id(year, request_seq)
            requester = employee_map[source.employee_id]
            target_employee = employee_map[target.employee_id]
            earliest_shift = min(
                parse_iso_utc(source.scheduled_start),
                parse_iso_utc(target.scheduled_start),
            )
            requested_time = earliest_shift - timedelta(
                hours=int(rng.integers(24, 97)),
                minutes=int(rng.integers(0, 60)),
            )
            request = SwapRequest(
                swap_request_id=request_id,
                requestor_employee_id=requester.employee_id,
                requestor_assignment_id=source.assignment_id,
                target_employee_id=target_employee.employee_id,
                target_assignment_id=target.assignment_id,
                current_status="REQUESTED",
                manager_employee_id=None,
                requested_at=_format_timestamp(requested_time),
            )
            swaps.append(request)

            event_seq += 1
            events.append(
                SwapRequestEvent(
                    swap_event_id=format_swap_event_id(year, event_seq),
                    swap_request_id=request_id,
                    from_status=None,
                    to_status="REQUESTED",
                    event_timestamp=request.requested_at,
                    actor_type="EMPLOYEE",
                    actor_id=requester.employee_id,
                    notes="Shift exchange requested by employee",
                )
            )

            transition = float(rng.random())
            if request_seq == 1 or (request_seq > 3 and transition < 0.15):
                continue

            managers = managers_by_team.get(requester.team_id, [])
            manager = managers[int(rng.integers(0, len(managers)))] if managers else None
            actor_type = "EMPLOYEE" if manager else "SYSTEM"
            actor_id = manager.employee_id if manager else None
            decision_time = min(
                requested_time + timedelta(hours=int(rng.integers(1, 7))),
                earliest_shift - timedelta(minutes=2),
            )
            request.decided_at = _format_timestamp(decision_time)
            request.manager_employee_id = actor_id
            event_seq += 1
            if request_seq == 2 or (request_seq > 3 and transition < 0.30):
                request.current_status = "REJECTED"
                request.rejection_reason = "Operational coverage could not accommodate the exchange"
                events.append(
                    SwapRequestEvent(
                        swap_event_id=format_swap_event_id(year, event_seq),
                        swap_request_id=request_id,
                        from_status="REQUESTED",
                        to_status="REJECTED",
                        event_timestamp=request.decided_at,
                        actor_type=actor_type,
                        actor_id=actor_id,
                        notes=request.rejection_reason,
                    )
                )
                continue

            request.current_status = "APPROVED"
            events.append(
                SwapRequestEvent(
                    swap_event_id=format_swap_event_id(year, event_seq),
                    swap_request_id=request_id,
                    from_status="REQUESTED",
                    to_status="APPROVED",
                    event_timestamp=request.decided_at,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    notes="Approved by authorized manager or SYSTEM",
                )
            )
            applied_time = decision_time + timedelta(minutes=int(rng.integers(1, 3)))
            request.applied_at = _format_timestamp(applied_time)
            request.current_status = "APPLIED"
            event_seq += 1
            events.append(
                SwapRequestEvent(
                    swap_event_id=format_swap_event_id(year, event_seq),
                    swap_request_id=request_id,
                    from_status="APPROVED",
                    to_status="APPLIED",
                    event_timestamp=request.applied_at,
                    actor_type="SYSTEM",
                    actor_id=None,
                    notes="Approved exchange applied non-destructively",
                )
            )

            for old_assignment, receiver in (
                (source, target_employee),
                (target, requester),
            ):
                index = assignment_index[old_assignment.assignment_id]
                assignments[index] = replace(
                    old_assignment,
                    assignment_status=cfg.swap_rules.swapped_out_status,
                    effective_to=request.applied_at,
                )
                next_assignment_seq += 1
                new_assignment = replace(
                    old_assignment,
                    assignment_id=format_assignment_id(old_assignment.shift_date.year, next_assignment_seq),
                    shift_instance_id=format_shift_instance_id(
                        old_assignment.shift_date,
                        old_assignment.team_id,
                        old_assignment.shift_template_id[-1],
                    ),
                    employee_id=receiver.employee_id,
                    assigned_role=receiver.role,
                    assignment_status=cfg.swap_rules.active_status,
                    effective_from=request.applied_at,
                    effective_to=None,
                    swap_request_id=request_id,
                    replaces_assignment_id=old_assignment.assignment_id,
                )
                assignment_index[new_assignment.assignment_id] = len(assignments)
                assignments.append(new_assignment)
                active_by_employee[old_assignment.employee_id] = [
                    active
                    for active in active_by_employee[old_assignment.employee_id]
                    if active.assignment_id != old_assignment.assignment_id
                ]
                active_by_employee[receiver.employee_id].append(new_assignment)

    return swaps, events, assignments

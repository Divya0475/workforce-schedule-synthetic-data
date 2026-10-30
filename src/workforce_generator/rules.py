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
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from workforce_generator.models.entities import (
    Employee,
    EmployeeSkill,
    LeaveRequest,
    SwapRequest,
    SwapRequestEvent,
    ShiftAssignment,
    ShiftTemplate,
)
from workforce_generator.models.identifiers import format_attendance_event_id


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


def check_swap_eligibility(
    requestor_assignment: ShiftAssignment,
    target_assignment: ShiftAssignment,
    requestor_employee: Employee,
    target_employee: Employee,
    assignments: List[ShiftAssignment],
    employee_skills: List[EmployeeSkill],
    leave_requests: List[LeaveRequest],
    min_rest_hours: float = 11.0,
    max_consecutive_days: int = 6,
    max_weekly_hours: float = 48.0,
    paid_hours_per_shift: float = 7.5,
) -> List[str]:
    """Validate a proposed reciprocal exchange against each employee's full schedule."""
    errors = []
    if requestor_employee.employee_id == target_employee.employee_id:
        errors.append("SWAP_SAME_EMPLOYEE: request participants must differ")
    if requestor_employee.team_id != target_employee.team_id:
        errors.append("SWAP_DIFFERENT_TEAM: participants must belong to the same team")
    if requestor_employee.role == "MANAGER" or target_employee.role == "MANAGER":
        errors.append("SWAP_MANAGER_PARTICIPANT: managers cannot participate")
    if requestor_assignment.assignment_status != "ACTIVE" or target_assignment.assignment_status != "ACTIVE":
        errors.append("SWAP_INACTIVE_ASSIGNMENT: both offered assignments must be ACTIVE")
    if requestor_assignment.team_id != target_assignment.team_id:
        errors.append("SWAP_DIFFERENT_TEAM_ASSIGNMENTS: assignments must belong to the same team")
    if requestor_assignment.employee_id != requestor_employee.employee_id:
        errors.append("SWAP_REQUESTOR_ASSIGNMENT_MISMATCH")
    if target_assignment.employee_id != target_employee.employee_id:
        errors.append("SWAP_TARGET_ASSIGNMENT_MISMATCH")
    if requestor_assignment.shift_date.isocalendar()[:2] != target_assignment.shift_date.isocalendar()[:2]:
        errors.append("SWAP_DIFFERENT_ISO_WEEK: assignments must be in the same ISO week")
    if errors:
        return errors

    cert_map = defaultdict(list)
    for employee_skill in employee_skills:
        cert_map[(employee_skill.employee_id, employee_skill.skill_id)].append(
            (employee_skill.certified_date, employee_skill.expiry_date)
        )
    approved_leave_dates = defaultdict(set)
    for leave in leave_requests:
        if leave.current_status == "APPROVED":
            current_date = leave.start_date
            while current_date <= leave.end_date:
                approved_leave_dates[leave.employee_id].add(current_date)
                current_date += timedelta(days=1)

    incoming = (
        (target_employee, requestor_assignment),
        (requestor_employee, target_assignment),
    )
    incoming_assignments = []
    for receiver, assignment in incoming:
        if assignment.fulfills_skill_id:
            is_qualified = any(
                certified_date <= assignment.shift_date
                and (expiry_date is None or expiry_date >= assignment.shift_date)
                for certified_date, expiry_date in cert_map.get(
                    (receiver.employee_id, assignment.fulfills_skill_id), []
                )
            )
            if not is_qualified:
                errors.append(
                    f"SWAP_UNQUALIFIED_RECEIVER: {receiver.employee_id} lacks "
                    f"{assignment.fulfills_skill_id} for {assignment.assignment_id}"
                )
        if assignment.shift_date in approved_leave_dates[receiver.employee_id]:
            errors.append(
                f"SWAP_APPROVED_LEAVE: {receiver.employee_id} has approved leave "
                f"on {assignment.shift_date}"
            )
        if assignment.shift_date < receiver.hire_date or (
            receiver.termination_date and assignment.shift_date > receiver.termination_date
        ):
            errors.append(
                f"SWAP_EMPLOYEE_BOUNDS: {receiver.employee_id} is inactive on {assignment.shift_date}"
            )
        incoming_assignments.append(
            replace(
                assignment,
                employee_id=receiver.employee_id,
                assigned_role=receiver.role,
            )
        )
    if errors:
        return errors

    exchanged_ids = {
        requestor_assignment.assignment_id,
        target_assignment.assignment_id,
    }
    remaining = [
        assignment
        for assignment in assignments
        if assignment.assignment_status == "ACTIVE"
        and assignment.assignment_id not in exchanged_ids
    ]
    for employee in (requestor_employee, target_employee):
        employee_schedule = [
            assignment
            for assignment in remaining
            if assignment.employee_id == employee.employee_id
        ]
        employee_schedule.extend(
            assignment
            for assignment in incoming_assignments
            if assignment.employee_id == employee.employee_id
        )
        intervals = [
            (
                parse_iso_utc(assignment.scheduled_start),
                parse_iso_utc(assignment.scheduled_end),
                assignment,
            )
            for assignment in employee_schedule
        ]
        intervals.sort(key=lambda interval: (interval[0], interval[1]))
        for previous, current in zip(intervals, intervals[1:]):
            rest_hours = (current[0] - previous[1]).total_seconds() / 3600.0
            if rest_hours < min_rest_hours - 1e-5:
                errors.append(
                    f"SWAP_REST_VIOLATION: {employee.employee_id} would have "
                    f"{rest_hours:.1f}h rest between {previous[2].assignment_id} "
                    f"and {current[2].assignment_id}"
                )
        worked_dates = {assignment.shift_date for assignment in employee_schedule}
        if any(
            sum(
                1
                for offset in range(max_consecutive_days + 1)
                if window_start + timedelta(days=offset) in worked_dates
            )
            > max_consecutive_days
            for window_start in worked_dates
        ):
            errors.append(
                f"SWAP_CONSECUTIVE_DAYS: {employee.employee_id} would exceed "
                f"{max_consecutive_days} consecutive working days"
            )
        hours_by_week = defaultdict(float)
        for assignment in employee_schedule:
            iso_year, iso_week, _ = assignment.shift_date.isocalendar()
            hours_by_week[(iso_year, iso_week)] += paid_hours_per_shift
        if any(hours > max_weekly_hours + 1e-5 for hours in hours_by_week.values()):
            errors.append(
                f"SWAP_OVERTIME_CAP: {employee.employee_id} would exceed "
                f"{max_weekly_hours:g} paid hours in an ISO week"
            )
    return errors


def check_swap_invariants(
    swap_requests: List[SwapRequest],
    swap_events: List[SwapRequestEvent],
    assignments: List[ShiftAssignment],
    employees: List[Employee],
    employee_skills: List[EmployeeSkill],
) -> List[str]:
    """Check swap lifecycle, authorization, skill, and non-destructive history invariants."""
    errors = []
    employee_map = {employee.employee_id: employee for employee in employees}
    assignment_map = {assignment.assignment_id: assignment for assignment in assignments}
    events_by_request = defaultdict(list)
    for event_index, event in enumerate(swap_events):
        events_by_request[event.swap_request_id].append((event_index, event))
    replacements_by_request = defaultdict(list)
    for assignment in assignments:
        if assignment.swap_request_id:
            replacements_by_request[assignment.swap_request_id].append(assignment)

    for request in swap_requests:
        events = sorted(
            events_by_request.get(request.swap_request_id, []),
            key=lambda item: (parse_iso_utc(item[1].event_timestamp), item[0]),
        )
        if not events:
            errors.append(f"SWAP_MISSING_EVENTS: {request.swap_request_id}")
            continue
        if events[0][1].from_status is not None or events[0][1].to_status != "REQUESTED":
            errors.append(f"SWAP_INVALID_INITIAL_EVENT: {request.swap_request_id}")
        previous_time = None
        previous_status = None
        for _, event in events:
            event_time = parse_iso_utc(event.event_timestamp)
            if previous_time is not None and event_time < previous_time:
                errors.append(f"SWAP_TIMESTAMP_ORDER: {request.swap_request_id}")
            if previous_status is not None and event.from_status != previous_status:
                errors.append(f"SWAP_EVENT_CHAIN: {request.swap_request_id}")
            if previous_status is not None and (
                (previous_status == "REQUESTED" and event.to_status not in {"APPROVED", "REJECTED"})
                or (previous_status == "APPROVED" and event.to_status != "APPLIED")
                or previous_status in {"REJECTED", "APPLIED"}
            ):
                errors.append(f"SWAP_INVALID_TRANSITION: {request.swap_request_id}")
            is_initial_request = event.to_status == "REQUESTED" and event.from_status is None
            if is_initial_request:
                if (
                    event.actor_type != "EMPLOYEE"
                    or event.actor_id != request.requestor_employee_id
                ):
                    errors.append(f"SWAP_INVALID_REQUESTOR_ACTOR: {request.swap_request_id}")
            elif event.actor_type == "SYSTEM":
                if event.actor_id is not None:
                    errors.append(f"SWAP_SYSTEM_ACTOR_HAS_ID: {request.swap_request_id}")
            elif event.actor_type == "EMPLOYEE":
                actor = employee_map.get(event.actor_id)
                if actor is None or actor.role != "MANAGER":
                    errors.append(f"SWAP_INVALID_MANAGER_ACTOR: {request.swap_request_id}")
                elif actor.employee_id in {
                    request.requestor_employee_id,
                    request.target_employee_id,
                }:
                    errors.append(f"SWAP_PARTICIPANT_APPROVER: {request.swap_request_id}")
                elif actor.team_id != employee_map.get(
                    request.requestor_employee_id, actor
                ).team_id:
                    errors.append(f"SWAP_WRONG_TEAM_APPROVER: {request.swap_request_id}")
            else:
                errors.append(f"SWAP_INVALID_ACTOR_TYPE: {request.swap_request_id}")
            if event.to_status in {"APPROVED", "REJECTED"}:
                if event.actor_type not in {"EMPLOYEE", "SYSTEM"}:
                    errors.append(f"SWAP_UNAUTHORIZED_DECISION: {request.swap_request_id}")
                if request.manager_employee_id != event.actor_id:
                    errors.append(f"SWAP_DECISION_ACTOR_MISMATCH: {request.swap_request_id}")
            previous_time = event_time
            previous_status = event.to_status

        latest_event = events[-1][1]
        if request.current_status != latest_event.to_status:
            errors.append(f"SWAP_STATUS_MISMATCH: {request.swap_request_id}")
        if request.current_status == "REJECTED" and not request.rejection_reason:
            errors.append(f"SWAP_REJECTION_WITHOUT_REASON: {request.swap_request_id}")

        requestor = employee_map.get(request.requestor_employee_id)
        target = employee_map.get(request.target_employee_id)
        requestor_assignment = assignment_map.get(request.requestor_assignment_id)
        target_assignment = assignment_map.get(request.target_assignment_id)
        if any(
            item is None
            for item in (requestor, target, requestor_assignment, target_assignment)
        ):
            errors.append(f"SWAP_MISSING_REFERENCE: {request.swap_request_id}")
            continue
        if requestor.team_id != target.team_id:
            errors.append(f"SWAP_DIFFERENT_TEAM: {request.swap_request_id}")
        if requestor_assignment.shift_date.isocalendar()[:2] != target_assignment.shift_date.isocalendar()[:2]:
            errors.append(f"SWAP_DIFFERENT_ISO_WEEK: {request.swap_request_id}")
        earliest_start = min(
            parse_iso_utc(requestor_assignment.scheduled_start),
            parse_iso_utc(target_assignment.scheduled_start),
        )
        if parse_iso_utc(request.requested_at) >= earliest_start:
            errors.append(f"SWAP_REQUEST_NOT_BEFORE_SHIFTS: {request.swap_request_id}")

        applied_events = [event for _, event in events if event.to_status == "APPLIED"]
        replacements = replacements_by_request.get(request.swap_request_id, [])
        if request.current_status == "APPLIED":
            if not any(event.to_status == "APPROVED" for _, event in events):
                errors.append(f"SWAP_APPLIED_WITHOUT_APPROVAL: {request.swap_request_id}")
            if len(applied_events) != 1 or len(replacements) != 2:
                errors.append(f"SWAP_APPLIED_HISTORY_INCOMPLETE: {request.swap_request_id}")
            if len(applied_events) == 1:
                applied_at = applied_events[0].event_timestamp
                if request.applied_at != applied_at:
                    errors.append(f"SWAP_APPLIED_TIMESTAMP_MISMATCH: {request.swap_request_id}")
                for old_assignment, expected_employee_id in (
                    (requestor_assignment, request.requestor_employee_id),
                    (target_assignment, request.target_employee_id),
                ):
                    if old_assignment.employee_id != expected_employee_id:
                        errors.append(f"SWAP_IN_PLACE_OVERWRITE: {request.swap_request_id}")
                    if (
                        old_assignment.assignment_status != "SWAPPED_OUT"
                        or old_assignment.effective_to != applied_at
                    ):
                        errors.append(f"SWAP_OLD_HISTORY_NOT_CLOSED: {request.swap_request_id}")
                expected_receivers = {
                    request.requestor_assignment_id: request.target_employee_id,
                    request.target_assignment_id: request.requestor_employee_id,
                }
                found_replacements = {
                    replacement.replaces_assignment_id: replacement
                    for replacement in replacements
                }
                if set(found_replacements) != set(expected_receivers):
                    errors.append(f"SWAP_REPLACEMENT_LINEAGE: {request.swap_request_id}")
                for old_id, receiver_id in expected_receivers.items():
                    replacement = found_replacements.get(old_id)
                    if replacement is None:
                        continue
                    if (
                        replacement.employee_id != receiver_id
                        or replacement.assignment_status != "ACTIVE"
                        or replacement.effective_from != applied_at
                        or replacement.effective_to is not None
                    ):
                        errors.append(f"SWAP_REPLACEMENT_INVALID: {request.swap_request_id}")
                    skill_id = replacement.fulfills_skill_id
                    if skill_id and not any(
                        skill.employee_id == receiver_id
                        and skill.skill_id == skill_id
                        and skill.certified_date <= replacement.shift_date
                        and (skill.expiry_date is None or skill.expiry_date >= replacement.shift_date)
                        for skill in employee_skills
                    ):
                        errors.append(f"SWAP_UNQUALIFIED_RECEIVER: {request.swap_request_id}")
        elif replacements:
            errors.append(f"SWAP_UNAPPLIED_HAS_REPLACEMENTS: {request.swap_request_id}")
        if request.current_status in {"APPROVED", "REJECTED"}:
            decision_events = [
                event for _, event in events if event.to_status in {"APPROVED", "REJECTED"}
            ]
            if decision_events and request.decided_at != decision_events[-1].event_timestamp:
                errors.append(f"SWAP_DECISION_TIMESTAMP_MISMATCH: {request.swap_request_id}")

    return errors


def check_attendance_invariants(
    attendance_events,
    assignments: List[ShiftAssignment],
    leave_requests: List[LeaveRequest],
    late_threshold_minutes: int = 10,
    early_leave_threshold_minutes: int = 15,
    overtime_threshold_minutes: int = 15,
    horizon_end: date | None = None,
) -> List[str]:
    """Independently verify attendance references and event-derived fields."""
    errors = []
    assignment_map = {assignment.assignment_id: assignment for assignment in assignments}
    approved_leave_dates = defaultdict(set)
    for leave in leave_requests:
        if leave.current_status == "APPROVED":
            current_date = leave.start_date
            while current_date <= leave.end_date:
                approved_leave_dates[leave.employee_id].add(current_date)
                current_date += timedelta(days=1)

    expected_assignment_ids = {
        assignment.assignment_id
        for assignment in assignments
        if assignment.assignment_status == "ACTIVE"
        and (horizon_end is None or assignment.shift_date <= horizon_end)
        and assignment.shift_date not in approved_leave_dates[assignment.employee_id]
    }
    events_by_assignment = defaultdict(list)
    identifiers = set()
    for event in attendance_events:
        if event.attendance_event_id in identifiers:
            errors.append(f"ATTENDANCE_DUPLICATE_ID: {event.attendance_event_id}")
        identifiers.add(event.attendance_event_id)
        assignment = assignment_map.get(event.assignment_id)
        if assignment is None:
            errors.append(f"ATTENDANCE_UNKNOWN_ASSIGNMENT: {event.assignment_id}")
            continue
        if assignment.assignment_status != "ACTIVE":
            errors.append(f"ATTENDANCE_ON_SWAPPED_OUT: {event.assignment_id}")
        if event.employee_id != assignment.employee_id:
            errors.append(f"ATTENDANCE_EMPLOYEE_MISMATCH: {event.assignment_id}")
        if assignment.shift_date in approved_leave_dates[event.employee_id]:
            errors.append(f"ATTENDANCE_DURING_APPROVED_LEAVE: {event.assignment_id}")
        if event.event_type not in {"CLOCK_IN", "CLOCK_OUT", "NO_SHOW"}:
            errors.append(f"ATTENDANCE_INVALID_EVENT_TYPE: {event.event_type}")
        if format_attendance_event_id(
            event.employee_id, parse_iso_utc(event.event_timestamp)
        ) != event.attendance_event_id:
            errors.append(f"ATTENDANCE_ID_TIMESTAMP_MISMATCH: {event.attendance_event_id}")
        events_by_assignment[event.assignment_id].append(event)

    missing_punches = expected_assignment_ids - set(events_by_assignment)
    for assignment_id in sorted(missing_punches):
        errors.append(f"ATTENDANCE_MISSING_EVENTS: {assignment_id}")

    for assignment_id, events in events_by_assignment.items():
        assignment = assignment_map.get(assignment_id)
        if assignment is None:
            continue
        no_shows = [event for event in events if event.event_type == "NO_SHOW"]
        clock_ins = [event for event in events if event.event_type == "CLOCK_IN"]
        clock_outs = [event for event in events if event.event_type == "CLOCK_OUT"]
        if no_shows:
            if len(no_shows) != 1 or clock_ins or clock_outs:
                errors.append(f"ATTENDANCE_NO_SHOW_PUNCH_CONFLICT: {assignment_id}")
            event = no_shows[0]
            if (
                event.variance_minutes is not None
                or event.worked_minutes != 0
                or not event.is_exception
                or event.exception_type != "NO_SHOW"
                or parse_iso_utc(event.event_timestamp)
                != parse_iso_utc(assignment.scheduled_start)
            ):
                errors.append(f"ATTENDANCE_NO_SHOW_DERIVATION: {assignment_id}")
            continue
        if len(clock_ins) != 1 or len(clock_outs) != 1:
            errors.append(f"ATTENDANCE_PUNCH_PAIR: {assignment_id}")
            continue
        clock_in, clock_out = clock_ins[0], clock_outs[0]
        scheduled_start = parse_iso_utc(assignment.scheduled_start)
        scheduled_end = parse_iso_utc(assignment.scheduled_end)
        clock_in_time = parse_iso_utc(clock_in.event_timestamp)
        clock_out_time = parse_iso_utc(clock_out.event_timestamp)
        expected_in_variance = int(round((clock_in_time - scheduled_start).total_seconds() / 60))
        expected_out_variance = int(round((clock_out_time - scheduled_end).total_seconds() / 60))
        expected_worked = int(round((clock_out_time - clock_in_time).total_seconds() / 60))
        if clock_in.variance_minutes != expected_in_variance or clock_in.worked_minutes is not None:
            errors.append(f"ATTENDANCE_CLOCK_IN_DERIVATION: {assignment_id}")
        if clock_out.variance_minutes != expected_out_variance or clock_out.worked_minutes != expected_worked:
            errors.append(f"ATTENDANCE_CLOCK_OUT_DERIVATION: {assignment_id}")
        expected_in_exception = expected_in_variance > late_threshold_minutes
        if (
            clock_in.is_exception != expected_in_exception
            or clock_in.exception_type != ("LATE" if expected_in_exception else None)
        ):
            errors.append(f"ATTENDANCE_LATE_DERIVATION: {assignment_id}")
        if expected_out_variance < -early_leave_threshold_minutes:
            expected_out_exception = True
            expected_out_type = "EARLY_LEAVE"
        elif expected_out_variance > overtime_threshold_minutes:
            expected_out_exception = True
            expected_out_type = "OVERTIME"
        else:
            expected_out_exception = False
            expected_out_type = None
        if (
            clock_out.is_exception != expected_out_exception
            or clock_out.exception_type != expected_out_type
        ):
            errors.append(f"ATTENDANCE_CLOCK_OUT_EXCEPTION: {assignment_id}")
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

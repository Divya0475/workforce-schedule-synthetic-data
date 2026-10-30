"""Deterministic, targeted dirty-dataset anomaly injection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Any, Mapping, Sequence

import numpy as np

from workforce_generator.config import GeneratorConfig
from workforce_generator.generators.analytics import (
    compute_coverage_gaps,
    compute_weekly_hours_summaries,
)
from workforce_generator.generators.attendance import generate_attendance_events
from workforce_generator.models.entities import (
    AttendanceEvent,
    Employee,
    EmployeeSkill,
    LeaveRequest,
    ShiftAssignment,
    SwapRequest,
    SwapRequestEvent,
)
from workforce_generator.models.identifiers import (
    format_anomaly_id,
    format_assignment_id,
    format_shift_instance_id,
    format_swap_event_id,
    format_swap_request_id,
)
from workforce_generator.rules import (
    check_swap_eligibility,
    get_shift_datetimes,
    parse_iso_utc,
)

ANOMALY_TYPES = (
    "DOUBLE_BOOKED",
    "LEAVE_OVERLAP",
    "SKILL_ABSENT",
    "OVERTIME_CAP_EXCEEDED",
    "SWAP_WITHOUT_APPROVAL",
)


def _iso_timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_certification(
    employee_id: str,
    skill_id: str | None,
    on_date: date,
    certifications: Mapping[tuple[str, str], Sequence[tuple[date, date | None]]],
) -> bool:
    if skill_id is None:
        return True
    return any(
        certified_date <= on_date and (expiry_date is None or expiry_date >= on_date)
        for certified_date, expiry_date in certifications.get((employee_id, skill_id), ())
    )


def _approved_leave_index(
    leave_requests: Sequence[LeaveRequest],
) -> dict[str, set[date]]:
    dates: dict[str, set[date]] = defaultdict(set)
    for leave in leave_requests:
        if leave.current_status != "APPROVED":
            continue
        current_date = leave.start_date
        while current_date <= leave.end_date:
            dates[leave.employee_id].add(current_date)
            current_date += timedelta(days=1)
    return dates


def _make_assignment(
    source: ShiftAssignment,
    employee: Employee,
    shift_date: date,
    cfg: GeneratorConfig,
    assignment_id: str,
    fulfills_skill_id: str | None = None,
    start_offset_minutes: int = 0,
) -> ShiftAssignment:
    shift_template = next(
        item for item in cfg.shifts if item.id == source.shift_template_id
    )
    start, end = get_shift_datetimes(shift_date, shift_template)
    start += timedelta(minutes=start_offset_minutes)
    end += timedelta(minutes=start_offset_minutes)
    return replace(
        source,
        assignment_id=assignment_id,
        shift_instance_id=format_shift_instance_id(
            shift_date, source.team_id, shift_template.code
        ),
        employee_id=employee.employee_id,
        shift_date=shift_date,
        scheduled_start=_iso_timestamp(start),
        scheduled_end=_iso_timestamp(end),
        assigned_role=employee.role,
        assignment_status=cfg.swap_rules.active_status,
        effective_from=_iso_timestamp(datetime.combine(shift_date, time.min)),
        effective_to=None,
        swap_request_id=None,
        replaces_assignment_id=None,
        fulfills_skill_id=fulfills_skill_id,
    )


def _ground_truth(
    anomaly_index: int,
    issue_type: str,
    entity: str,
    record_id: str,
    related_record_ids: Sequence[str],
    employee_id: str,
    description: str,
    also_triggers: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "anomaly_id": format_anomaly_id(anomaly_index),
        "issue_type": issue_type,
        "entity": entity,
        "record_id": record_id,
        "related_record_ids": list(related_record_ids),
        "employee_id": employee_id,
        "description": description,
        "also_triggers": list(also_triggers),
    }


def _next_sequence(values: Sequence[Any], attribute: str) -> int:
    highest = 0
    for value in values:
        identifier = getattr(value, attribute)
        try:
            highest = max(highest, int(identifier.rsplit("-", 1)[1]))
        except (ValueError, IndexError):
            continue
    return highest + 1


def _active_index(
    assignments: Sequence[ShiftAssignment],
) -> tuple[dict[str, list[ShiftAssignment]], dict[tuple[str, int, int], list[ShiftAssignment]]]:
    by_employee: dict[str, list[ShiftAssignment]] = defaultdict(list)
    by_team_week: dict[tuple[str, int, int], list[ShiftAssignment]] = defaultdict(list)
    for assignment in assignments:
        if assignment.assignment_status != "ACTIVE":
            continue
        by_employee[assignment.employee_id].append(assignment)
        iso_year, iso_week, _ = assignment.shift_date.isocalendar()
        by_team_week[(assignment.team_id, iso_year, iso_week)].append(assignment)
    return by_employee, by_team_week


def _make_anomaly_swap(
    cfg: GeneratorConfig,
    rng: np.random.Generator,
    anomaly_index: int,
    assignments: list[ShiftAssignment],
    requests: list[SwapRequest],
    events: list[SwapRequestEvent],
    employees_by_id: Mapping[str, Employee],
    employee_skills: Sequence[EmployeeSkill],
    leave_requests: Sequence[LeaveRequest],
    certifications: Mapping[tuple[str, str], Sequence[tuple[date, date | None]]],
    leave_dates: Mapping[str, set[date]],
    touched_employee_days: set[tuple[str, date]],
    next_assignment_seq: int,
    next_request_seq: int,
    next_event_seq: int,
) -> tuple[dict[str, Any], int, int, int]:
    active_by_employee, active_by_team_week = _active_index(assignments)
    candidates: list[tuple[ShiftAssignment, ShiftAssignment]] = []
    replacements_by_request: dict[str, list[ShiftAssignment]] = defaultdict(list)
    for row in assignments:
        if row.swap_request_id and row.assignment_status == "ACTIVE":
            replacements_by_request[row.swap_request_id].append(row)
    for request in sorted(requests, key=lambda item: item.swap_request_id):
        if request.current_status != "APPLIED":
            continue
        replacement_rows = replacements_by_request.get(request.swap_request_id, [])
        if len(replacement_rows) == 2:
            candidates.append((replacement_rows[0], replacement_rows[1]))
    seen_candidate_pairs = {
        frozenset((first.assignment_id, second.assignment_id))
        for first, second in candidates
    }
    for group_key in sorted(active_by_team_week):
        group = active_by_team_week[group_key]
        if len(group) < 2:
            continue
        order = rng.permutation(len(group))
        for position, source_index in enumerate(order):
            source = group[int(source_index)]
            for offset in range(1, min(21, len(group))):
                target = group[int(order[(position + offset) % len(group)])]
                if (
                    source.employee_id == target.employee_id
                    or source.shift_date == target.shift_date
                ):
                    continue
                pair_key = frozenset((source.assignment_id, target.assignment_id))
                if pair_key in seen_candidate_pairs:
                    continue
                seen_candidate_pairs.add(pair_key)
                candidates.append((source, target))
    selected_pair = None
    rejected_findings: list[str] = []
    for candidate_index in rng.permutation(len(candidates)):
        first, second = candidates[int(candidate_index)]
        if (
            (first.employee_id, first.shift_date) in touched_employee_days
            or (second.employee_id, second.shift_date) in touched_employee_days
        ):
            continue
        requester = employees_by_id[first.employee_id]
        target = employees_by_id[second.employee_id]
        findings = check_swap_eligibility(
            first,
            second,
            requester,
            target,
            assignments,
            list(employee_skills),
            list(leave_requests),
            min_rest_hours=cfg.labor_constraints.min_rest_hours_between_shifts,
            max_consecutive_days=cfg.labor_constraints.max_consecutive_working_days,
            max_weekly_hours=cfg.labor_constraints.overtime_cap_weekly_hours,
            paid_hours_per_shift=cfg.labor_constraints.paid_hours_per_shift,
            assignments_by_employee=active_by_employee,
            certifications_by_employee_skill=certifications,
            approved_leave_dates_by_employee=leave_dates,
        )
        if not findings:
            selected_pair = first, second
            break
        rejected_findings.extend(findings)
    if selected_pair is None:
        raise ValueError(
            "Not enough valid, non-overlapping swap targets for anomalies: "
            f"{rejected_findings[:10]}"
        )

    first, second = selected_pair
    requester = employees_by_id[first.employee_id]
    target = employees_by_id[second.employee_id]
    year = first.shift_date.year
    request_id = format_swap_request_id(year, next_request_seq)
    earliest_start = min(
        parse_iso_utc(first.scheduled_start),
        parse_iso_utc(second.scheduled_start),
    )
    requested_at = earliest_start - timedelta(hours=3)
    applied_at = earliest_start - timedelta(hours=2)
    request = SwapRequest(
        swap_request_id=request_id,
        requestor_employee_id=requester.employee_id,
        requestor_assignment_id=first.assignment_id,
        target_employee_id=target.employee_id,
        target_assignment_id=second.assignment_id,
        current_status="APPLIED",
        manager_employee_id=None,
        requested_at=_iso_timestamp(requested_at),
        decided_at=_iso_timestamp(applied_at),
        applied_at=_iso_timestamp(applied_at),
    )
    requests.append(request)
    events.extend(
        (
            SwapRequestEvent(
                swap_event_id=format_swap_event_id(year, next_event_seq),
                swap_request_id=request_id,
                from_status=None,
                to_status="REQUESTED",
                event_timestamp=request.requested_at,
                actor_type="EMPLOYEE",
                actor_id=requester.employee_id,
                notes="Injected lifecycle anomaly",
            ),
            SwapRequestEvent(
                swap_event_id=format_swap_event_id(year, next_event_seq + 1),
                swap_request_id=request_id,
                from_status="REQUESTED",
                to_status="APPLIED",
                event_timestamp=request.applied_at,
                actor_type="SYSTEM",
                actor_id=None,
                notes="Applied without an approval transition",
            ),
        )
    )

    assignment_positions = {
        assignment.assignment_id: index for index, assignment in enumerate(assignments)
    }
    new_rows = []
    for original, receiver in ((first, target), (second, requester)):
        assignments[assignment_positions[original.assignment_id]] = replace(
            original,
            assignment_status=cfg.swap_rules.swapped_out_status,
            effective_to=request.applied_at,
        )
        replacement = replace(
            original,
            assignment_id=format_assignment_id(original.shift_date.year, next_assignment_seq),
            employee_id=receiver.employee_id,
            assigned_role=receiver.role,
            assignment_status=cfg.swap_rules.active_status,
            effective_from=request.applied_at,
            effective_to=None,
            swap_request_id=request_id,
            replaces_assignment_id=original.assignment_id,
        )
        assignments.append(replacement)
        new_rows.append(replacement)
        touched_employee_days.add((receiver.employee_id, original.shift_date))
        next_assignment_seq += 1
    truth = _ground_truth(
        anomaly_index,
        "SWAP_WITHOUT_APPROVAL",
        "SwapRequest",
        request_id,
        [
            first.assignment_id,
            second.assignment_id,
            *(event.swap_event_id for event in events[-2:]),
            *(row.assignment_id for row in new_rows),
        ],
        requester.employee_id,
        "Swap lifecycle transitions directly from REQUESTED to APPLIED without approval.",
    )
    return truth, next_assignment_seq, next_request_seq + 1, next_event_seq + 2


def generate_dirty_dataset(
    cfg: GeneratorConfig,
    clean_data: Mapping[str, Any],
    injection_rng: np.random.Generator,
    attendance_rng: np.random.Generator,
    counts_per_type: int,
) -> tuple[dict[str, list[Any]], list[dict[str, Any]]]:
    """Return a dirty copy with exactly the configured number of each anomaly."""
    if counts_per_type < 1:
        raise ValueError("counts_per_type must be at least one")
    dirty = {
        name: list(rows)
        for name, rows in clean_data.items()
        if isinstance(rows, list)
    }
    employees: list[Employee] = dirty["employees"]
    employees_by_id = {employee.employee_id: employee for employee in employees}
    employee_skills: list[EmployeeSkill] = dirty["employee_skills"]
    leave_requests: list[LeaveRequest] = dirty["leave_requests"]
    assignments: list[ShiftAssignment] = dirty["shift_assignments"]
    requests: list[SwapRequest] = dirty["swap_requests"]
    events: list[SwapRequestEvent] = dirty["swap_events"]
    certifications: dict[
        tuple[str, str], list[tuple[date, date | None]]
    ] = defaultdict(list)
    for skill in employee_skills:
        certifications[(skill.employee_id, skill.skill_id)].append(
            (skill.certified_date, skill.expiry_date)
        )
    leave_dates = _approved_leave_index(leave_requests)
    touched_employee_days: set[tuple[str, date]] = set()
    anomaly_rows: list[dict[str, Any]] = []
    next_assignment_seq = _next_sequence(assignments, "assignment_id")
    next_request_seq = _next_sequence(requests, "swap_request_id")
    next_event_seq = _next_sequence(events, "swap_event_id")
    next_anomaly_index = 1

    # DOUBLE_BOOKED: add a shifted copy that overlaps the source assignment.
    active_rows = sorted(
        (row for row in assignments if row.assignment_status == "ACTIVE"),
        key=lambda row: row.assignment_id,
    )
    double_booked_candidates = [
        row
        for row in active_rows
        if (row.employee_id, row.shift_date) not in touched_employee_days
    ]
    for _ in range(counts_per_type):
        if not double_booked_candidates:
            raise ValueError("Not enough employee-day targets for DOUBLE_BOOKED anomalies")
        selected_index = int(injection_rng.integers(len(double_booked_candidates)))
        source = double_booked_candidates.pop(selected_index)
        employee = employees_by_id[source.employee_id]
        extra = _make_assignment(
            source,
            employee,
            source.shift_date,
            cfg,
            format_assignment_id(source.shift_date.year, next_assignment_seq),
            source.fulfills_skill_id,
            start_offset_minutes=30,
        )
        next_assignment_seq += 1
        assignments.append(extra)
        touched_employee_days.add((extra.employee_id, extra.shift_date))
        anomaly_rows.append(
            _ground_truth(
                next_anomaly_index,
                "DOUBLE_BOOKED",
                "ShiftAssignment",
                extra.assignment_id,
                [source.assignment_id],
                extra.employee_id,
                "Injected active assignment overlaps another active assignment for the employee.",
            )
        )
        next_anomaly_index += 1

    # LEAVE_OVERLAP: create a shift on a real approved-leave date for that employee.
    team_sources: dict[str, list[ShiftAssignment]] = defaultdict(list)
    active_days_by_employee: dict[str, set[date]] = defaultdict(set)
    for row in assignments:
        if row.assignment_status == "ACTIVE":
            team_sources[row.team_id].append(row)
            active_days_by_employee[row.employee_id].add(row.shift_date)
    leave_candidates = [
        leave
        for leave in leave_requests
        if leave.current_status == "APPROVED"
        and employees_by_id[leave.employee_id].role != "MANAGER"
    ]
    leave_rng_order = injection_rng.permutation(len(leave_candidates))
    leave_candidates = [leave_candidates[int(i)] for i in leave_rng_order]
    selected_leave_targets = []
    for leave in leave_candidates:
        employee = employees_by_id[leave.employee_id]
        current_date = leave.start_date
        while current_date <= leave.end_date:
            if (
                cfg.horizon.start_date <= current_date <= cfg.horizon.end_date
                and employee.hire_date <= current_date
                and (
                    employee.termination_date is None
                    or employee.termination_date >= current_date
                )
                and current_date not in active_days_by_employee[employee.employee_id]
                and (employee.employee_id, current_date) not in touched_employee_days
            ):
                source_rows = team_sources.get(employee.team_id, [])
                if source_rows:
                    source = source_rows[0]
                    skill_id = source.fulfills_skill_id
                    if not _valid_certification(
                        employee.employee_id, skill_id, current_date, certifications
                    ):
                        skill_id = None
                    selected_leave_targets.append((leave, current_date, source, skill_id))
                    break
            current_date += timedelta(days=1)
        if len(selected_leave_targets) == counts_per_type:
            break
    if len(selected_leave_targets) < counts_per_type:
        raise ValueError("Not enough approved leave employee-days for LEAVE_OVERLAP anomalies")
    for leave, shift_date, source, skill_id in selected_leave_targets:
        employee = employees_by_id[leave.employee_id]
        extra = _make_assignment(
            source,
            employee,
            shift_date,
            cfg,
            format_assignment_id(shift_date.year, next_assignment_seq),
            skill_id,
        )
        next_assignment_seq += 1
        assignments.append(extra)
        touched_employee_days.add((employee.employee_id, shift_date))
        active_days_by_employee[employee.employee_id].add(shift_date)
        anomaly_rows.append(
            _ground_truth(
                next_anomaly_index,
                "LEAVE_OVERLAP",
                "ShiftAssignment",
                extra.assignment_id,
                [leave.leave_request_id],
                employee.employee_id,
                "Injected ACTIVE assignment falls within an APPROVED leave request.",
            )
        )
        next_anomaly_index += 1

    # SKILL_ABSENT: transfer an active skill-bearing slot to an uncertified teammate.
    active_rows = sorted(
        (
            row
            for row in assignments
            if row.assignment_status == "ACTIVE" and row.fulfills_skill_id
        ),
        key=lambda row: row.assignment_id,
    )
    skill_candidates = []
    team_staff: dict[str, list[Employee]] = defaultdict(list)
    for employee in employees:
        if employee.role != "MANAGER":
            team_staff[employee.team_id].append(employee)
    for source in active_rows:
        for employee in team_staff[source.team_id]:
            if (
                employee.team_id == source.team_id
                and employee.role != "MANAGER"
                and employee.employee_id != source.employee_id
                and source.shift_date not in active_days_by_employee[employee.employee_id]
                and (employee.employee_id, source.shift_date) not in touched_employee_days
                and not _valid_certification(
                    employee.employee_id,
                    source.fulfills_skill_id,
                    source.shift_date,
                    certifications,
                )
                and source.shift_date not in leave_dates[employee.employee_id]
                and employee.hire_date <= source.shift_date
                and (
                    employee.termination_date is None
                    or employee.termination_date >= source.shift_date
                )
            ):
                skill_candidates.append((source, employee))
    for _ in range(counts_per_type):
        if not skill_candidates:
            raise ValueError("Not enough uncertified employee-day targets for SKILL_ABSENT")
        selected_index = int(injection_rng.integers(len(skill_candidates)))
        source, employee = skill_candidates.pop(selected_index)
        extra = _make_assignment(
            source,
            employee,
            source.shift_date,
            cfg,
            format_assignment_id(source.shift_date.year, next_assignment_seq),
            source.fulfills_skill_id,
        )
        next_assignment_seq += 1
        assignments.append(extra)
        touched_employee_days.add((employee.employee_id, source.shift_date))
        active_days_by_employee[employee.employee_id].add(source.shift_date)
        anomaly_rows.append(
            _ground_truth(
                next_anomaly_index,
                "SKILL_ABSENT",
                "ShiftAssignment",
                extra.assignment_id,
                [source.assignment_id, str(source.fulfills_skill_id)],
                employee.employee_id,
                f"Injected assignment fulfills {source.fulfills_skill_id} without a valid employee certification on the shift date.",
            )
        )
        next_anomaly_index += 1

    # OVERTIME_CAP_EXCEEDED: ensure one employee has exactly seven shifts in a full week.
    complete_weeks = []
    current_date = cfg.horizon.start_date
    while current_date <= cfg.horizon.end_date:
        iso_year, iso_week, _ = current_date.isocalendar()
        monday = date.fromisocalendar(iso_year, iso_week, 1)
        sunday = monday + timedelta(days=6)
        if monday >= cfg.horizon.start_date and sunday <= cfg.horizon.end_date:
            if (iso_year, iso_week) not in complete_weeks:
                complete_weeks.append((iso_year, iso_week))
        current_date += timedelta(days=1)
    if not complete_weeks:
        raise ValueError("The configured horizon has no complete ISO week for overtime anomalies")

    overtime_targets = []
    active_by_employee, _ = _active_index(assignments)
    for iso_year, iso_week in complete_weeks:
        week_dates = [date.fromisocalendar(iso_year, iso_week, day) for day in range(1, 8)]
        for employee in employees:
            if employee.role == "MANAGER":
                continue
            existing_by_day: dict[date, list[ShiftAssignment]] = defaultdict(list)
            for row in active_by_employee.get(employee.employee_id, ()):
                if row.shift_date in week_dates:
                    existing_by_day[row.shift_date].append(row)
            if any(len(rows) > 1 for rows in existing_by_day.values()):
                continue
            existing_days = set(existing_by_day)
            available_days = [
                day
                for day in week_dates
                if day not in existing_days
                and (employee.employee_id, day) not in touched_employee_days
                and employee.hire_date <= day
                and (employee.termination_date is None or employee.termination_date >= day)
                and day not in leave_dates[employee.employee_id]
            ]
            if len(existing_days) < 7 and len(available_days) >= 7 - len(existing_days):
                overtime_targets.append(
                    (employee, week_dates, existing_by_day, available_days)
                )
    if len(overtime_targets) < counts_per_type:
        raise ValueError("Not enough full-week employee targets for OVERTIME_CAP_EXCEEDED")
    overtime_order = injection_rng.permutation(len(overtime_targets))
    selected_overtime_targets = []
    used_overtime_employees: set[str] = set()
    for index in overtime_order:
        target = overtime_targets[int(index)]
        if target[0].employee_id in used_overtime_employees:
            continue
        selected_overtime_targets.append(target)
        used_overtime_employees.add(target[0].employee_id)
        if len(selected_overtime_targets) == counts_per_type:
            break
    if len(selected_overtime_targets) < counts_per_type:
        raise ValueError("Not enough distinct employees for overtime anomaly targets")
    for employee, week_dates, existing_by_day, available_days in selected_overtime_targets:
        selected_days = set(existing_by_day)
        needed = 7 - len(selected_days)
        day_order = injection_rng.permutation(len(available_days))
        selected_days.update(
            available_days[int(index)] for index in day_order[:needed]
        )
        week_assignment_ids = [
            row.assignment_id
            for day in sorted(selected_days)
            for row in existing_by_day.get(day, ())
        ]
        for shift_date in sorted(selected_days.difference(existing_by_day)):
            team_rows = team_sources.get(employee.team_id, [])
            if not team_rows:
                raise ValueError(f"No source assignments available for {employee.team_id}")
            source = team_rows[0]
            extra = _make_assignment(
                source,
                employee,
                shift_date,
                cfg,
                format_assignment_id(shift_date.year, next_assignment_seq),
                None,
            )
            next_assignment_seq += 1
            assignments.append(extra)
            touched_employee_days.add((employee.employee_id, shift_date))
            week_assignment_ids.append(extra.assignment_id)
        week_assignment_ids.sort()
        anomaly_rows.append(
            _ground_truth(
                next_anomaly_index,
                "OVERTIME_CAP_EXCEEDED",
                "ShiftAssignment",
                next(
                    identifier
                    for identifier in week_assignment_ids
                    if identifier not in {
                        row.assignment_id
                        for rows in existing_by_day.values()
                        for row in rows
                    }
                ),
                week_assignment_ids,
                employee.employee_id,
                "Employee has seven ACTIVE shifts in one full ISO week (52.5 paid hours).",
                ["MAX_CONSECUTIVE_DAYS"],
            )
        )
        next_anomaly_index += 1

    for _ in range(counts_per_type):
        truth, next_assignment_seq, next_request_seq, next_event_seq = _make_anomaly_swap(
            cfg,
            injection_rng,
            next_anomaly_index,
            assignments,
            requests,
            events,
            employees_by_id,
            employee_skills,
            leave_requests,
            certifications,
            leave_dates,
            touched_employee_days,
            next_assignment_seq,
            next_request_seq,
            next_event_seq,
        )
        anomaly_rows.append(truth)
        next_anomaly_index += 1

    counts = defaultdict(int)
    for record in anomaly_rows:
        counts[record["issue_type"]] += 1
    expected_counts = {issue_type: counts_per_type for issue_type in ANOMALY_TYPES}
    if dict(counts) != expected_counts:
        raise AssertionError(f"Unexpected injected anomaly counts: {dict(counts)}")

    dirty["attendance_events"] = generate_attendance_events(
        cfg, attendance_rng, assignments, leave_requests
    )
    if len(
        {event.attendance_event_id for event in dirty["attendance_events"]}
    ) != len(dirty["attendance_events"]):
        raise ValueError("Attendance event ID collision after anomaly recomputation")
    dirty["coverage_gaps"] = compute_coverage_gaps(
        cfg,
        dirty["coverage_requirements"],
        assignments,
        employees,
        employee_skills,
        leave_requests,
    )
    dirty["weekly_hours_summaries"] = compute_weekly_hours_summaries(
        cfg, employees, assignments
    )
    anomaly_rows.sort(key=lambda row: row["anomaly_id"])
    return dirty, anomaly_rows

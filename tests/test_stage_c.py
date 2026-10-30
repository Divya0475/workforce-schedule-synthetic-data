"""Stage C swap, attendance, and post-swap derivation invariants."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import os
import subprocess
import sys

import pytest

from workforce_generator.models.entities import ShiftAssignment
from workforce_generator.rules import (
    check_attendance_invariants,
    check_swap_eligibility,
    check_swap_invariants,
    validate_all_schedule_rules,
)


def _parse_utc(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.rstrip("Z"))


def _approved_leave_dates(leave_requests) -> dict[str, set]:
    result = defaultdict(set)
    for leave in leave_requests:
        if leave.current_status != "APPROVED":
            continue
        current_date = leave.start_date
        while current_date <= leave.end_date:
            result[leave.employee_id].add(current_date)
            current_date += timedelta(days=1)
    return result


def test_swap_invariants_reconcile_raw_tables(starter_pipeline_data):
    data = starter_pipeline_data
    employees = {employee.employee_id: employee for employee in data["employees"]}
    assignments = {assignment.assignment_id: assignment for assignment in data["shift_assignments"]}
    events_by_request = defaultdict(list)
    for event in data["swap_events"]:
        events_by_request[event.swap_request_id].append(event)
    replacements_by_request = defaultdict(list)
    for assignment in data["shift_assignments"]:
        if assignment.swap_request_id:
            replacements_by_request[assignment.swap_request_id].append(assignment)

    assert data["swap_requests"]
    assert {request.current_status for request in data["swap_requests"]} == {
        "REQUESTED",
        "REJECTED",
        "APPLIED",
    }

    for request in data["swap_requests"]:
        requestor = employees[request.requestor_employee_id]
        target = employees[request.target_employee_id]
        requestor_assignment = assignments[request.requestor_assignment_id]
        target_assignment = assignments[request.target_assignment_id]
        request_events = sorted(
            events_by_request[request.swap_request_id],
            key=lambda event: _parse_utc(event.event_timestamp),
        )
        assert requestor.team_id == target.team_id
        assert requestor.role != "MANAGER"
        assert target.role != "MANAGER"
        assert requestor_assignment.shift_date.isocalendar()[:2] == target_assignment.shift_date.isocalendar()[:2]
        assert _parse_utc(request.requested_at) < min(
            _parse_utc(requestor_assignment.scheduled_start),
            _parse_utc(target_assignment.scheduled_start),
        )
        assert request.current_status == request_events[-1].to_status
        event_times = [_parse_utc(event.event_timestamp) for event in request_events]
        assert event_times == sorted(event_times)
        assert request_events[0].to_status == "REQUESTED"
        assert request_events[0].actor_id == request.requestor_employee_id

        for event in request_events:
            if event.to_status not in {"APPROVED", "REJECTED"}:
                continue
            if event.actor_type == "SYSTEM":
                assert event.actor_id is None
                assert request.manager_employee_id is None
            else:
                manager = employees[event.actor_id]
                assert manager.role == "MANAGER"
                assert manager.team_id == requestor.team_id
                assert manager.employee_id not in {
                    request.requestor_employee_id,
                    request.target_employee_id,
                }
                assert request.manager_employee_id == manager.employee_id

        replacements = replacements_by_request[request.swap_request_id]
        if request.current_status != "APPLIED":
            assert replacements == []
            if request.current_status == "REJECTED":
                assert request.rejection_reason
            continue

        assert any(event.to_status == "APPROVED" for event in request_events)
        assert len(replacements) == 2
        assert request.applied_at == request_events[-1].event_timestamp
        expected_receivers = {
            request.requestor_assignment_id: request.target_employee_id,
            request.target_assignment_id: request.requestor_employee_id,
        }
        for original_id, receiver_id in expected_receivers.items():
            original = assignments[original_id]
            assert original.employee_id == (
                request.requestor_employee_id
                if original_id == request.requestor_assignment_id
                else request.target_employee_id
            )
            assert original.assignment_status == "SWAPPED_OUT"
            assert original.effective_to == request.applied_at
            replacement = next(
                row for row in replacements if row.replaces_assignment_id == original_id
            )
            assert replacement.assignment_id != original.assignment_id
            assert replacement.employee_id == receiver_id
            assert replacement.assignment_status == "ACTIVE"
            assert replacement.effective_from == request.applied_at
            assert replacement.effective_to is None
            if replacement.fulfills_skill_id:
                assert any(
                    skill.employee_id == receiver_id
                    and skill.skill_id == replacement.fulfills_skill_id
                    and skill.certified_date <= replacement.shift_date
                    and (
                        skill.expiry_date is None
                        or skill.expiry_date >= replacement.shift_date
                    )
                    for skill in data["employee_skills"]
                )

    violations = check_swap_invariants(
        data["swap_requests"],
        data["swap_events"],
        data["shift_assignments"],
        data["employees"],
        data["employee_skills"],
    )
    assert violations == []


def test_final_and_reconstructed_baseline_schedules_have_no_hard_violations(
    starter_pipeline_data,
):
    data = starter_pipeline_data
    cfg = data["config"]
    baseline_assignments = [
        replace(assignment, assignment_status="ACTIVE", effective_to=None)
        for assignment in data["shift_assignments"]
        if assignment.swap_request_id is None
    ]
    for assignments in (baseline_assignments, data["shift_assignments"]):
        assert validate_all_schedule_rules(
            assignments,
            data["employees"],
            data["employee_skills"],
            data["leave_requests"],
            cfg.horizon.start_date,
            cfg.horizon.end_date,
        ) == []


def test_coverage_and_derived_tables_rebuild_from_final_active_assignments(
    starter_pipeline_data,
):
    data = starter_pipeline_data
    baseline = [
        assignment
        for assignment in data["shift_assignments"]
        if assignment.swap_request_id is None
    ]
    active_after = [
        assignment
        for assignment in data["shift_assignments"]
        if assignment.assignment_status == "ACTIVE"
    ]

    def counts(assignments):
        result = Counter()
        for assignment in assignments:
            if assignment.fulfills_skill_id:
                result[
                    (
                        assignment.shift_date,
                        assignment.team_id,
                        assignment.shift_template_id,
                        assignment.fulfills_skill_id,
                    )
                ] += 1
        return result

    baseline_counts = counts(baseline)
    final_counts = counts(active_after)
    requirements = {
        (
            requirement.effective_date,
            requirement.team_id,
            requirement.shift_template_id,
            requirement.skill_id,
        ): requirement
        for requirement in data["coverage_requirements"]
    }
    gaps = {gap.coverage_id: gap for gap in data["coverage_gaps"]}
    assert len(gaps) == len(requirements)
    assert all(
        baseline_counts[key] == final_counts[key]
        for key in requirements
    ), "Skill-preserving swaps must not change coverage counts"
    for key, requirement in requirements.items():
        gap = gaps[requirement.coverage_id]
        scheduled = final_counts[key]
        assert gap.scheduled_headcount == scheduled
        assert gap.gap_headcount == max(0, requirement.min_headcount - scheduled)

    hours_by_employee_week = Counter()
    for assignment in active_after:
        iso_year, iso_week, _ = assignment.shift_date.isocalendar()
        hours_by_employee_week[(assignment.employee_id, iso_year, iso_week)] += (
            data["config"].labor_constraints.paid_hours_per_shift
        )
    summary_map = {
        (summary.employee_id, summary.iso_year, summary.iso_week): summary
        for summary in data["weekly_hours_summaries"]
    }
    assert len(summary_map) == len(data["weekly_hours_summaries"])
    for key, summary in summary_map.items():
        assert summary.scheduled_hours == pytest.approx(hours_by_employee_week[key])


def test_applied_swap_without_approval_is_rejected_by_checker(starter_pipeline_data):
    data = starter_pipeline_data
    applied_request = next(
        request for request in data["swap_requests"] if request.current_status == "APPLIED"
    )
    events = [
        replace(event, to_status="REJECTED")
        if event.swap_request_id == applied_request.swap_request_id
        and event.to_status == "APPROVED"
        else event
        for event in data["swap_events"]
    ]
    errors = check_swap_invariants(
        data["swap_requests"],
        events,
        data["shift_assignments"],
        data["employees"],
        data["employee_skills"],
    )
    assert any("SWAP_APPLIED_WITHOUT_APPROVAL" in error for error in errors)


def test_unqualified_applied_swap_is_rejected_by_checker(starter_pipeline_data):
    data = starter_pipeline_data
    applied_request = next(
        request for request in data["swap_requests"] if request.current_status == "APPLIED"
    )
    replacements = [
        replace(assignment, fulfills_skill_id="SKL-UNQUALIFIED")
        if assignment.swap_request_id == applied_request.swap_request_id
        else assignment
        for assignment in data["shift_assignments"]
    ]
    errors = check_swap_invariants(
        data["swap_requests"],
        data["swap_events"],
        replacements,
        data["employees"],
        data["employee_skills"],
    )
    assert any("SWAP_UNQUALIFIED_RECEIVER" in error for error in errors)


def test_rejected_swap_reason_matches_bruteforce_eligibility_finding(starter_pipeline_data):
    data = starter_pipeline_data
    employees = {employee.employee_id: employee for employee in data["employees"]}
    assignments = {assignment.assignment_id: assignment for assignment in data["shift_assignments"]}
    replacements_by_request = defaultdict(list)
    for assignment in data["shift_assignments"]:
        if assignment.swap_request_id:
            replacements_by_request[assignment.swap_request_id].append(assignment)

    rejected = [
        request for request in data["swap_requests"] if request.current_status == "REJECTED"
    ]
    assert rejected
    for request in rejected:
        request_sequence = int(request.swap_request_id.rsplit("-", 1)[1])
        earlier_applied_ids = {
            other.swap_request_id
            for other in data["swap_requests"]
            if other.current_status == "APPLIED"
            and int(other.swap_request_id.rsplit("-", 1)[1]) < request_sequence
        }
        replaced_before_request = {
            assignment.replaces_assignment_id
            for swap_request_id in earlier_applied_ids
            for assignment in replacements_by_request[swap_request_id]
        }
        schedule_at_request = [
            replace(
                assignment,
                assignment_status=(
                    "SWAPPED_OUT"
                    if assignment.assignment_id in replaced_before_request
                    else "ACTIVE"
                ),
                effective_to=None,
            )
            for assignment in data["shift_assignments"]
            if assignment.swap_request_id is None
        ]
        schedule_at_request.extend(
            assignment
            for swap_request_id in earlier_applied_ids
            for assignment in replacements_by_request[swap_request_id]
        )
        requestor = employees[request.requestor_employee_id]
        target = employees[request.target_employee_id]
        findings = check_swap_eligibility(
            assignments[request.requestor_assignment_id],
            assignments[request.target_assignment_id],
            requestor,
            target,
            schedule_at_request,
            data["employee_skills"],
            data["leave_requests"],
            min_rest_hours=data["config"].labor_constraints.min_rest_hours_between_shifts,
            max_consecutive_days=(
                data["config"].labor_constraints.max_consecutive_working_days
            ),
            max_weekly_hours=data["config"].labor_constraints.overtime_cap_weekly_hours,
            paid_hours_per_shift=data["config"].labor_constraints.paid_hours_per_shift,
        )
        assert request.rejection_reason in findings


def test_attendance_independently_reconciles_to_final_schedule(starter_pipeline_data):
    data = starter_pipeline_data
    assignments = {assignment.assignment_id: assignment for assignment in data["shift_assignments"]}
    leave_dates = _approved_leave_dates(data["leave_requests"])
    expected_assignment_ids = {
        assignment.assignment_id
        for assignment in assignments.values()
        if assignment.assignment_status == "ACTIVE"
        and assignment.shift_date <= data["config"].horizon.end_date
        and assignment.shift_date not in leave_dates[assignment.employee_id]
    }
    attendance_by_assignment = defaultdict(list)
    for event in data["attendance_events"]:
        attendance_by_assignment[event.assignment_id].append(event)
        assignment = assignments[event.assignment_id]
        assert assignment.assignment_status == "ACTIVE"
        assert event.employee_id == assignment.employee_id
        assert assignment.shift_date not in leave_dates[event.employee_id]

    assert set(attendance_by_assignment) == expected_assignment_ids
    late_threshold = data["config"].attendance.exception_thresholds["late_minutes"]
    early_threshold = data["config"].attendance.exception_thresholds["early_leave_minutes"]
    overtime_threshold = data["config"].attendance.exception_thresholds["overtime_minutes"]
    for assignment_id, events in attendance_by_assignment.items():
        assignment = assignments[assignment_id]
        no_shows = [event for event in events if event.event_type == "NO_SHOW"]
        if no_shows:
            assert len(events) == 1
            assert no_shows[0].variance_minutes is None
            assert no_shows[0].worked_minutes == 0
            assert no_shows[0].is_exception
            assert no_shows[0].exception_type == "NO_SHOW"
            continue

        clock_in = next(event for event in events if event.event_type == "CLOCK_IN")
        clock_out = next(event for event in events if event.event_type == "CLOCK_OUT")
        in_variance = int(
            round(
                (
                    _parse_utc(clock_in.event_timestamp)
                    - _parse_utc(assignment.scheduled_start)
                ).total_seconds()
                / 60
            )
        )
        out_variance = int(
            round(
                (
                    _parse_utc(clock_out.event_timestamp)
                    - _parse_utc(assignment.scheduled_end)
                ).total_seconds()
                / 60
            )
        )
        worked_minutes = int(
            round(
                (
                    _parse_utc(clock_out.event_timestamp)
                    - _parse_utc(clock_in.event_timestamp)
                ).total_seconds()
                / 60
            )
        )
        assert clock_in.variance_minutes == in_variance
        assert clock_in.worked_minutes is None
        assert clock_in.is_exception == (in_variance > late_threshold)
        assert clock_in.exception_type == ("LATE" if in_variance > late_threshold else None)
        assert clock_out.variance_minutes == out_variance
        assert clock_out.worked_minutes == worked_minutes
        if out_variance < -early_threshold:
            assert clock_out.is_exception
            assert clock_out.exception_type == "EARLY_LEAVE"
        elif out_variance > overtime_threshold:
            assert clock_out.is_exception
            assert clock_out.exception_type == "OVERTIME"
        else:
            assert not clock_out.is_exception
            assert clock_out.exception_type is None

    errors = check_attendance_invariants(
        data["attendance_events"],
        data["shift_assignments"],
        data["leave_requests"],
        late_threshold_minutes=late_threshold,
        early_leave_threshold_minutes=early_threshold,
        overtime_threshold_minutes=overtime_threshold,
        horizon_end=data["config"].horizon.end_date,
    )
    assert errors == []


def test_attendance_on_swapped_out_assignment_is_rejected(starter_pipeline_data):
    data = starter_pipeline_data
    applied_request = next(
        request for request in data["swap_requests"] if request.current_status == "APPLIED"
    )
    old_assignment_id = applied_request.requestor_assignment_id
    replacement_event = next(
        event
        for event in data["attendance_events"]
        if event.assignment_id
        in {
            assignment.assignment_id
            for assignment in data["shift_assignments"]
            if assignment.swap_request_id == applied_request.swap_request_id
        }
    )
    original_assignment = next(
        assignment
        for assignment in data["shift_assignments"]
        if assignment.assignment_id == old_assignment_id
    )
    invalid_event = replace(
        replacement_event,
        employee_id=original_assignment.employee_id,
        assignment_id=old_assignment_id,
    )
    errors = check_attendance_invariants(
        [invalid_event], data["shift_assignments"], data["leave_requests"]
    )
    assert any("ATTENDANCE_ON_SWAPPED_OUT" in error for error in errors)


def test_swap_and_attendance_output_is_deterministic_across_timezones():
    script = (
        "import sys, json, hashlib; "
        "sys.path.insert(0, 'src'); "
        "from workforce_generator.pipeline import run_pipeline; "
        "overrides={'seed':20261022,'workforce':{'num_teams':5,'employees_per_team':10},"
        "'horizon':{'start_date':'2026-01-01','end_date':'2026-01-14'},"
        "'leave_rules':{'target_total_requests':100,'leave_coverage_buffer':1.25}}; "
        "d=run_pipeline(overrides=overrides); "
        "keys=('shift_assignments','swap_requests','swap_events','attendance_events',"
        "'coverage_gaps','weekly_hours_summaries'); "
        "payload=json.dumps({k:[x.to_dict() for x in d[k]] for k in keys},sort_keys=True); "
        "print(hashlib.sha256(payload.encode('utf-8')).hexdigest())"
    )
    env_ist = os.environ.copy()
    env_ist["TZ"] = "IST-5:30"
    env_utc = os.environ.copy()
    env_utc["TZ"] = "UTC0"
    hashes = [
        subprocess.check_output(
            [sys.executable, "-c", script], env=environment, text=True
        ).strip()
        for environment in (env_ist, env_utc)
    ]
    assert hashes[0] == hashes[1]

"""Simulate deterministic timeclock events from the finalized active schedule."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import List

import numpy as np

from workforce_generator.config import GeneratorConfig
from workforce_generator.models.entities import (
    AttendanceEvent,
    LeaveRequest,
    ShiftAssignment,
)
from workforce_generator.models.identifiers import format_attendance_event_id
from workforce_generator.rules import parse_iso_utc

_ATTENDANCE_SOURCES = (
    "BIOMETRIC",
    "BADGE_READER",
    "MOBILE_APP",
    "MANUAL_OVERRIDE",
)


def _event(
    employee_id: str,
    assignment_id: str,
    event_type: str,
    timestamp,
    source: str,
    variance_minutes: int | None,
    worked_minutes: int | None,
    exception_type: str | None,
) -> AttendanceEvent:
    return AttendanceEvent(
        attendance_event_id=format_attendance_event_id(employee_id, timestamp),
        employee_id=employee_id,
        assignment_id=assignment_id,
        event_type=event_type,
        event_timestamp=timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        source=source,
        variance_minutes=variance_minutes,
        worked_minutes=worked_minutes,
        is_exception=exception_type is not None,
        exception_type=exception_type,
    )


def _sample_clock_in_variance(rng: np.random.Generator, probabilities: dict) -> int | None:
    categories = list(probabilities)
    chosen = str(rng.choice(categories, p=[probabilities[key] for key in categories]))
    if chosen == "no_show":
        return None
    bounds = {
        "on_time": (-5, 3),
        "early": (-30, -6),
        "late_grace": (4, 10),
        "late_unexcused": (11, 60),
    }
    if chosen not in bounds:
        raise ValueError(f"Unknown punctuality category: {chosen}")
    low, high = bounds[chosen]
    return int(rng.integers(low, high + 1))


def _sample_clock_out_variance(rng: np.random.Generator, probabilities: dict) -> int:
    categories = list(probabilities)
    chosen = str(rng.choice(categories, p=[probabilities[key] for key in categories]))
    bounds = {
        "on_time": (-15, 15),
        "early_departure": (-60, -16),
        "overtime_shift": (16, 120),
    }
    if chosen not in bounds:
        raise ValueError(f"Unknown clock-out category: {chosen}")
    low, high = bounds[chosen]
    return int(rng.integers(low, high + 1))


def generate_attendance_events(
    cfg: GeneratorConfig,
    rng: np.random.Generator,
    assignments: List[ShiftAssignment],
    leave_requests: List[LeaveRequest],
) -> List[AttendanceEvent]:
    """Create punches for each active scheduled shift within the horizon cutoff."""
    approved_leave_dates = defaultdict(set)
    for leave in leave_requests:
        if leave.current_status != "APPROVED":
            continue
        current_date = leave.start_date
        while current_date <= leave.end_date:
            approved_leave_dates[leave.employee_id].add(current_date)
            current_date += timedelta(days=1)

    events: List[AttendanceEvent] = []
    for assignment in sorted(
        assignments,
        key=lambda item: (item.shift_date, item.scheduled_start, item.assignment_id),
    ):
        if (
            assignment.assignment_status != cfg.swap_rules.active_status
            or assignment.shift_date > cfg.horizon.end_date
            or assignment.shift_date in approved_leave_dates[assignment.employee_id]
        ):
            continue

        scheduled_start = parse_iso_utc(assignment.scheduled_start)
        scheduled_end = parse_iso_utc(assignment.scheduled_end)
        source = str(rng.choice(_ATTENDANCE_SOURCES))
        clock_in_variance = _sample_clock_in_variance(
            rng, cfg.attendance.punctuality_probabilities
        )
        if clock_in_variance is None:
            events.append(
                _event(
                    assignment.employee_id,
                    assignment.assignment_id,
                    "NO_SHOW",
                    scheduled_start,
                    source,
                    None,
                    0,
                    "NO_SHOW",
                )
            )
            continue

        clock_in = scheduled_start + timedelta(minutes=clock_in_variance)
        late_threshold = cfg.attendance.exception_thresholds["late_minutes"]
        clock_in_exception = (
            "LATE" if clock_in_variance > late_threshold else None
        )
        events.append(
            _event(
                assignment.employee_id,
                assignment.assignment_id,
                "CLOCK_IN",
                clock_in,
                source,
                clock_in_variance,
                None,
                clock_in_exception,
            )
        )

        clock_out_variance = _sample_clock_out_variance(
            rng, cfg.attendance.clock_out_variance
        )
        clock_out = scheduled_end + timedelta(minutes=clock_out_variance)
        if clock_out_variance < -cfg.attendance.exception_thresholds["early_leave_minutes"]:
            clock_out_exception = "EARLY_LEAVE"
        elif clock_out_variance > cfg.attendance.exception_thresholds["overtime_minutes"]:
            clock_out_exception = "OVERTIME"
        else:
            clock_out_exception = None
        worked_minutes = int(round((clock_out - clock_in).total_seconds() / 60))
        events.append(
            _event(
                assignment.employee_id,
                assignment.assignment_id,
                "CLOCK_OUT",
                clock_out,
                source,
                clock_out_variance,
                worked_minutes,
                clock_out_exception,
            )
        )

    return events

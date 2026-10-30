"""Tests for identifier formats and regex validation."""

from datetime import date, datetime, timezone
import pytest

from workforce_generator.models.identifiers import (
    RE_EMPLOYEE_ID,
    RE_TEAM_ID,
    RE_SKILL_ID,
    RE_SHIFT_TEMPLATE_ID,
    RE_SHIFT_INSTANCE_ID,
    RE_ASSIGNMENT_ID,
    RE_LEAVE_REQUEST_ID,
    RE_LEAVE_EVENT_ID,
    RE_SWAP_REQUEST_ID,
    RE_SWAP_EVENT_ID,
    RE_ATTENDANCE_EVENT_ID,
    RE_COVERAGE_ID,
    RE_HOLIDAY_ID,
    RE_EMPLOYEE_SKILL_ID,
    RE_COVERAGE_GAP_ID,
    RE_WEEKLY_HOURS_SUMMARY_ID,
    RE_ANOMALY_ID,
    format_employee_id,
    format_team_id,
    format_skill_id,
    format_shift_template_id,
    format_shift_instance_id,
    format_assignment_id,
    format_leave_request_id,
    format_leave_event_id,
    format_swap_request_id,
    format_swap_event_id,
    format_attendance_event_id,
    format_coverage_id,
    format_holiday_id,
    format_employee_skill_id,
    format_coverage_gap_id,
    format_weekly_hours_summary_id,
    format_anomaly_id,
)


def test_employee_id_format():
    emp_id = format_employee_id(1)
    assert emp_id == "EMP-000001"
    assert RE_EMPLOYEE_ID.match(emp_id)


def test_team_id_format():
    team_id = format_team_id(5)
    assert team_id == "TEAM-005"
    assert RE_TEAM_ID.match(team_id)


def test_skill_id_format():
    skill_id = format_skill_id("triage")
    assert skill_id == "SKL-TRIAGE"
    assert RE_SKILL_ID.match(skill_id)


def test_shift_template_id_format():
    sft_id = format_shift_template_id("a")
    assert sft_id == "SFT-A"
    assert RE_SHIFT_TEMPLATE_ID.match(sft_id)


def test_shift_instance_id_uniqueness_across_teams():
    d = date(2026, 1, 1)
    id1 = format_shift_instance_id(d, "TEAM-001", "A")
    id2 = format_shift_instance_id(d, "TEAM-002", "A")
    assert id1 == "SHIFT-2026-01-01-TEAM001-A"
    assert id2 == "SHIFT-2026-01-01-TEAM002-A"
    assert id1 != id2
    assert RE_SHIFT_INSTANCE_ID.match(id1)
    assert RE_SHIFT_INSTANCE_ID.match(id2)


def test_event_and_request_ids():
    lve_id = format_leave_request_id(2026, 42)
    assert lve_id == "LVE-2026-000042"
    assert RE_LEAVE_REQUEST_ID.match(lve_id)

    lve_evt = format_leave_event_id(2026, 42)
    assert lve_evt == "LVE-EVT-2026-000042"
    assert RE_LEAVE_EVENT_ID.match(lve_evt)

    swp_id = format_swap_request_id(2026, 1)
    assert swp_id == "SWP-2026-000001"
    assert RE_SWAP_REQUEST_ID.match(swp_id)

    swp_evt = format_swap_event_id(2026, 1)
    assert swp_evt == "SWP-EVT-2026-000001"
    assert RE_SWAP_EVENT_ID.match(swp_evt)


def test_attendance_event_id():
    ts = datetime(2026, 1, 1, 5, 58, 0, tzinfo=timezone.utc)
    att_id = format_attendance_event_id("EMP-000001", ts)
    assert att_id == "ATT-EMP-000001-20260101T055800Z"
    assert RE_ATTENDANCE_EVENT_ID.match(att_id)


def test_derived_ids():
    gap_id = format_coverage_gap_id(2026, 10)
    assert gap_id == "GAP-2026-000010"
    assert RE_COVERAGE_GAP_ID.match(gap_id)

    whs_id = format_weekly_hours_summary_id("EMP-000001", 2026, 1)
    assert whs_id == "WHS-EMP-000001-2026W01"
    assert RE_WEEKLY_HOURS_SUMMARY_ID.match(whs_id)

    anm_id = format_anomaly_id(5)
    assert anm_id == "ANM-0005"
    assert RE_ANOMALY_ID.match(anm_id)


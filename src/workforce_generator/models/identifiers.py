"""ID formatting helpers and validators ensuring zero-padded, immutable conventions."""

from __future__ import annotations

import re
from datetime import date, datetime

RE_EMPLOYEE_ID = re.compile(r"^EMP-\d{6}$")
RE_TEAM_ID = re.compile(r"^TEAM-\d{3}$")
RE_SKILL_ID = re.compile(r"^SKL-[A-Z_]+$")
RE_SHIFT_TEMPLATE_ID = re.compile(r"^SFT-[A-Z]$")
RE_SHIFT_INSTANCE_ID = re.compile(r"^SHIFT-\d{4}-\d{2}-\d{2}-TEAM\d{3}-[A-Z]$")
RE_ASSIGNMENT_ID = re.compile(r"^ASN-\d{4}-\d{6}$")
RE_LEAVE_REQUEST_ID = re.compile(r"^LVE-\d{4}-\d{6}$")
RE_LEAVE_EVENT_ID = re.compile(r"^LVE-EVT-\d{4}-\d{6}$")
RE_SWAP_REQUEST_ID = re.compile(r"^SWP-\d{4}-\d{6}$")
RE_SWAP_EVENT_ID = re.compile(r"^SWP-EVT-\d{4}-\d{6}$")
RE_ATTENDANCE_EVENT_ID = re.compile(r"^ATT-EMP-\d{6}-\d{8}T\d{6}Z$")
RE_COVERAGE_ID = re.compile(r"^COV-\d{4}-\d{6}$")
RE_HOLIDAY_ID = re.compile(r"^HOL-\d{4}-\d{3}$")
RE_EMPLOYEE_SKILL_ID = re.compile(r"^ESK-\d{6}$")
RE_COVERAGE_GAP_ID = re.compile(r"^GAP-\d{4}-\d{6}$")
RE_WEEKLY_HOURS_SUMMARY_ID = re.compile(r"^WHS-EMP-\d{6}-\d{4}W\d{2}$")
RE_ANOMALY_ID = re.compile(r"^ANM-\d{4}$")


def format_employee_id(seq: int) -> str:
    return f"EMP-{seq:06d}"


def format_team_id(seq: int) -> str:
    return f"TEAM-{seq:03d}"


def format_skill_id(code: str) -> str:
    return f"SKL-{code.upper()}"


def format_shift_template_id(code: str) -> str:
    return f"SFT-{code.upper()}"


def format_shift_instance_id(shift_date: date | str, team_id: str | int, template_code: str) -> str:
    if isinstance(shift_date, date):
        d_str = shift_date.isoformat()
    else:
        d_str = str(shift_date)
    if isinstance(team_id, int):
        team_str = f"TEAM{team_id:03d}"
    else:
        # e.g. TEAM-001 -> TEAM001
        team_str = team_id.replace("-", "")
    return f"SHIFT-{d_str}-{team_str}-{template_code.upper()}"


def format_assignment_id(year: int, seq: int) -> str:
    return f"ASN-{year:04d}-{seq:06d}"


def format_leave_request_id(year: int, seq: int) -> str:
    return f"LVE-{year:04d}-{seq:06d}"


def format_leave_event_id(year: int, seq: int) -> str:
    return f"LVE-EVT-{year:04d}-{seq:06d}"


def format_swap_request_id(year: int, seq: int) -> str:
    return f"SWP-{year:04d}-{seq:06d}"


def format_swap_event_id(year: int, seq: int) -> str:
    return f"SWP-EVT-{year:04d}-{seq:06d}"


def format_attendance_event_id(employee_id: str, ts: datetime) -> str:
    compact_ts = ts.strftime("%Y%m%dT%H%M%SZ")
    return f"ATT-{employee_id}-{compact_ts}"


def format_coverage_id(year: int, seq: int) -> str:
    return f"COV-{year:04d}-{seq:06d}"


def format_holiday_id(year: int, seq: int) -> str:
    return f"HOL-{year:04d}-{seq:03d}"


def format_employee_skill_id(seq: int) -> str:
    return f"ESK-{seq:06d}"


def format_coverage_gap_id(year: int, seq: int) -> str:
    return f"GAP-{year:04d}-{seq:06d}"


def format_weekly_hours_summary_id(employee_id: str, iso_year: int, iso_week: int) -> str:
    return f"WHS-{employee_id}-{iso_year:04d}W{iso_week:02d}"


def format_anomaly_id(seq: int) -> str:
    return f"ANM-{seq:04d}"


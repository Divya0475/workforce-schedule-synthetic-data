"""Dataclass definitions for all workforce scheduling entities."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional


@dataclass
class Team:
    team_id: str
    name: str
    department: str
    timezone: str = "UTC"
    is_active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Skill:
    skill_id: str
    code: str
    name: str
    category: str
    description: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Employee:
    employee_id: str
    team_id: str
    first_name: str
    last_name: str
    email: str
    role: str
    weekly_contract_hours: float
    fte: float
    hire_date: date
    termination_date: Optional[date] = None
    is_active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["hire_date"] = self.hire_date.isoformat()
        d["termination_date"] = self.termination_date.isoformat() if self.termination_date else None
        return d


@dataclass
class EmployeeSkill:
    employee_skill_id: str
    employee_id: str
    skill_id: str
    proficiency_level: int
    certified_date: date
    expiry_date: Optional[date] = None
    is_primary: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["certified_date"] = self.certified_date.isoformat()
        d["expiry_date"] = self.expiry_date.isoformat() if self.expiry_date else None
        return d


@dataclass
class ShiftTemplate:
    shift_template_id: str
    template_code: str
    name: str
    start_time: str
    end_time: str
    duration_hours: float
    unpaid_break_minutes: int = 30
    is_overnight: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HolidayCalendar:
    holiday_id: str
    calendar_date: date
    name: str
    jurisdiction: str = "FEDERAL"
    is_statutory: bool = True

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["calendar_date"] = self.calendar_date.isoformat()
        return d


@dataclass
class CoverageRequirement:
    coverage_id: str
    effective_date: date
    team_id: str
    shift_template_id: str
    skill_id: str
    min_headcount: int
    target_headcount: int

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["effective_date"] = self.effective_date.isoformat()
        return d


@dataclass
class ShiftAssignment:
    assignment_id: str
    shift_instance_id: str
    employee_id: str
    team_id: str
    shift_template_id: str
    shift_date: date
    scheduled_start: str
    scheduled_end: str
    duration_hours: float
    assigned_role: str
    assignment_status: str  # ACTIVE or SWAPPED_OUT
    effective_from: str     # ISO timestamp
    effective_to: Optional[str] = None
    swap_request_id: Optional[str] = None
    replaces_assignment_id: Optional[str] = None
    fulfills_skill_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["shift_date"] = self.shift_date.isoformat()
        return d


@dataclass
class LeaveRequest:
    leave_request_id: str
    employee_id: str
    leave_type: str
    start_date: date
    end_date: date
    requested_hours: float
    current_status: str
    submitted_at: str
    decided_at: Optional[str] = None
    decision_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["start_date"] = self.start_date.isoformat()
        d["end_date"] = self.end_date.isoformat()
        return d


@dataclass
class LeaveRequestEvent:
    leave_event_id: str
    leave_request_id: str
    from_status: Optional[str]
    to_status: str
    event_timestamp: str
    actor_type: str  # EMPLOYEE or SYSTEM
    actor_id: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)




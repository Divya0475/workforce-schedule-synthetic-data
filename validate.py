"""Validate exported workforce CSV tables without importing generator code."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
import time

import pandas as pd
import numpy as np


TABLE_KEYS = {
    "teams": "team_id",
    "skills": "skill_id",
    "employees": "employee_id",
    "employee_skills": "employee_skill_id",
    "shift_templates": "shift_template_id",
    "holidays": "holiday_id",
    "coverage_requirements": "coverage_id",
    "leave_requests": "leave_request_id",
    "leave_events": "leave_event_id",
    "shift_assignments": "assignment_id",
    "swap_requests": "swap_request_id",
    "swap_events": "swap_event_id",
    "attendance_events": "attendance_event_id",
    "coverage_gaps": "gap_id",
    "weekly_hours_summaries": "summary_id",
}

FOREIGN_KEYS = {
    "employees": {"team_id": "teams"},
    "employee_skills": {"employee_id": "employees", "skill_id": "skills"},
    "coverage_requirements": {
        "team_id": "teams",
        "shift_template_id": "shift_templates",
        "skill_id": "skills",
    },
    "leave_requests": {"employee_id": "employees"},
    "leave_events": {
        "leave_request_id": "leave_requests",
        "actor_id": "employees",
    },
    "shift_assignments": {
        "employee_id": "employees",
        "team_id": "teams",
        "shift_template_id": "shift_templates",
        "swap_request_id": "swap_requests",
        "replaces_assignment_id": "shift_assignments",
        "fulfills_skill_id": "skills",
    },
    "swap_requests": {
        "requestor_employee_id": "employees",
        "requestor_assignment_id": "shift_assignments",
        "target_employee_id": "employees",
        "target_assignment_id": "shift_assignments",
        "manager_employee_id": "employees",
    },
    "swap_events": {
        "swap_request_id": "swap_requests",
        "actor_id": "employees",
    },
    "attendance_events": {
        "employee_id": "employees",
        "assignment_id": "shift_assignments",
    },
    "coverage_gaps": {
        "coverage_id": "coverage_requirements",
        "team_id": "teams",
        "shift_template_id": "shift_templates",
        "skill_id": "skills",
    },
    "weekly_hours_summaries": {"employee_id": "employees"},
}

ENUMS = {
    ("employees", "role"): {"STAFF", "SENIOR", "LEAD", "MANAGER"},
    ("shift_assignments", "assignment_status"): {"ACTIVE", "SWAPPED_OUT"},
    ("leave_requests", "leave_type"): {
        "ANNUAL",
        "SICK",
        "UNPAID",
        "PARENTAL",
        "BEREAVEMENT",
    },
    ("leave_requests", "current_status"): {
        "SUBMITTED",
        "APPROVED",
        "REJECTED",
        "CANCELLED",
    },
    ("leave_events", "to_status"): {
        "SUBMITTED",
        "APPROVED",
        "REJECTED",
        "CANCELLED",
    },
    ("leave_events", "actor_type"): {"EMPLOYEE", "SYSTEM"},
    ("swap_requests", "current_status"): {
        "REQUESTED",
        "APPROVED",
        "REJECTED",
        "APPLIED",
    },
    ("swap_events", "to_status"): {
        "REQUESTED",
        "APPROVED",
        "REJECTED",
        "APPLIED",
    },
    ("swap_events", "actor_type"): {"EMPLOYEE", "SYSTEM"},
    ("attendance_events", "event_type"): {"CLOCK_IN", "CLOCK_OUT", "NO_SHOW"},
    ("attendance_events", "source"): {
        "BIOMETRIC",
        "BADGE_READER",
        "MOBILE_APP",
        "MANUAL_OVERRIDE",
    },
    ("attendance_events", "exception_type"): {
        "",
        "LATE",
        "EARLY_LEAVE",
        "NO_SHOW",
        "OVERTIME",
    },
}

VIOLATION_COLUMNS = (
    "rule",
    "entity",
    "record_id",
    "employee_id",
    "severity",
    "details",
)


def _read_tables(data_dir: Path, violations: list[dict[str, str]]) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    for table_name in TABLE_KEYS:
        path = data_dir / f"{table_name}.csv"
        if not path.is_file():
            violations.append(
                _violation(
                    "MISSING_TABLE",
                    table_name,
                    "",
                    "",
                    f"Required CSV file not found: {path.name}",
                )
            )
            tables[table_name] = pd.DataFrame()
            continue
        tables[table_name] = pd.read_csv(
            path, dtype=str, keep_default_na=False, encoding="utf-8"
        )
    return tables


def _violation(
    rule: str,
    entity: str,
    record_id: str,
    employee_id: str,
    details: str,
    severity: str = "ERROR",
) -> dict[str, str]:
    return {
        "rule": rule,
        "entity": entity,
        "record_id": str(record_id or ""),
        "employee_id": str(employee_id or ""),
        "severity": severity,
        "details": details,
    }


def _has_column(frame: pd.DataFrame, column: str) -> bool:
    return column in frame.columns


def _date_values(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, format="mixed", errors="coerce").dt.date


def _timestamps(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, errors="coerce", utc=True)


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _active_assignments(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    assignments = tables["shift_assignments"]
    if assignments.empty or "assignment_status" not in assignments:
        return assignments.iloc[0:0].copy()
    return assignments.loc[assignments["assignment_status"] == "ACTIVE"].copy()


def _emit_row_violations(
    violations: list[dict[str, str]],
    mask: pd.Series,
    frame: pd.DataFrame,
    rule: str,
    entity: str,
    details: str,
    employee_column: str = "employee_id",
) -> None:
    for index in frame.index[mask.fillna(False)]:
        row = frame.loc[index]
        violations.append(
            _violation(
                rule,
                entity,
                row.get(TABLE_KEYS.get(entity, ""), ""),
                row.get(employee_column, ""),
                details,
            )
        )


def _check_primary_keys_and_foreign_keys(
    tables: dict[str, pd.DataFrame], violations: list[dict[str, str]]
) -> None:
    for table_name, primary_key in TABLE_KEYS.items():
        frame = tables[table_name]
        if primary_key not in frame.columns:
            violations.append(
                _violation(
                    "PRIMARY_KEY_COLUMN_MISSING",
                    table_name,
                    "",
                    "",
                    f"Missing required primary-key column {primary_key}",
                )
            )
            continue
        duplicate_mask = frame[primary_key].duplicated(keep=False)
        _emit_row_violations(
            violations,
            duplicate_mask,
            frame,
            "PRIMARY_KEY_DUPLICATE",
            table_name,
            f"Primary key {primary_key} is duplicated",
        )

    for child_name, field_map in FOREIGN_KEYS.items():
        child = tables[child_name]
        for field, parent_name in field_map.items():
            if field not in child.columns:
                violations.append(
                    _violation(
                        "FOREIGN_KEY_COLUMN_MISSING",
                        child_name,
                        "",
                        "",
                        f"Missing FK column {field}",
                    )
                )
                continue
            parent_key = TABLE_KEYS[parent_name]
            parent = tables[parent_name]
            if parent_key not in parent.columns:
                continue
            allowed = set(parent[parent_key])
            null_allowed = field in {
                "actor_id",
                "manager_employee_id",
                "swap_request_id",
                "replaces_assignment_id",
                "fulfills_skill_id",
            }
            vals = child[field]
            if null_allowed:
                invalid_mask = vals.notna() & vals.ne("") & (~vals.isin(allowed))
            else:
                invalid_mask = vals.isna() | vals.eq("") | (~vals.isin(allowed))
            if invalid_mask.any():
                pk_col = TABLE_KEYS[child_name]
                for _, row in child.loc[invalid_mask].iterrows():
                    val = row[field]
                    violations.append(
                        _violation(
                            f"FK_{child_name.upper()}_{field.upper()}",
                            child_name,
                            str(row.get(pk_col, "")),
                            str(row.get("employee_id", "")),
                            f"{field} references missing {parent_name}.{parent_key}: {val}",
                        )
                    )


def _check_enums(tables: dict[str, pd.DataFrame], violations: list[dict[str, str]]) -> None:
    for (table_name, field), allowed_values in ENUMS.items():
        frame = tables[table_name]
        if field not in frame.columns:
            continue
        mask = ~frame[field].isin(allowed_values)
        for index in frame.index[mask]:
            row = frame.loc[index]
            violations.append(
                _violation(
                    "ENUM_INVALID",
                    table_name,
                    row.get(TABLE_KEYS[table_name], ""),
                    row.get("employee_id", ""),
                    f"{field} has unsupported value {row[field]!r}",
                )
            )


def _check_dates_and_timestamps(
    tables: dict[str, pd.DataFrame], violations: list[dict[str, str]]
) -> tuple[date | None, date | None]:
    dates = {
        ("employees", "hire_date"),
        ("employees", "termination_date"),
        ("employee_skills", "certified_date"),
        ("employee_skills", "expiry_date"),
        ("holidays", "calendar_date"),
        ("coverage_requirements", "effective_date"),
        ("shift_assignments", "shift_date"),
        ("leave_requests", "start_date"),
        ("leave_requests", "end_date"),
    }
    for table_name, column in dates:
        frame = tables[table_name]
        if column not in frame.columns:
            continue
        value = frame[column]
        parsed = _date_values(value)
        mask = value.ne("") & parsed.isna()
        _emit_row_violations(
            violations,
            mask,
            frame,
            "DATE_INVALID",
            table_name,
            f"{column} is not a valid ISO date",
        )

    timestamp_fields = {
        "shift_assignments": ("scheduled_start", "scheduled_end", "effective_from", "effective_to"),
        "leave_requests": ("submitted_at", "decided_at"),
        "leave_events": ("event_timestamp",),
        "swap_requests": ("requested_at", "decided_at", "applied_at"),
        "swap_events": ("event_timestamp",),
        "attendance_events": ("event_timestamp",),
        "coverage_gaps": ("computed_at",),
        "weekly_hours_summaries": ("computed_at",),
    }
    parsed_timestamps: dict[tuple[str, str], pd.Series] = {}
    for table_name, columns in timestamp_fields.items():
        frame = tables[table_name]
        for column in columns:
            if column not in frame.columns:
                continue
            parsed = _timestamps(frame[column])
            parsed_timestamps[(table_name, column)] = parsed
            mask = frame[column].ne("") & parsed.isna()
            _emit_row_violations(
                violations,
                mask,
                frame,
                "TIMESTAMP_INVALID",
                table_name,
                f"{column} is not a valid ISO timestamp",
            )

    employees = tables["employees"]
    if {"hire_date", "termination_date"} <= set(employees.columns):
        hire, termination = _date_values(employees["hire_date"]), _date_values(
            employees["termination_date"]
        )
        mask = termination.notna() & hire.notna() & (termination < hire)
        _emit_row_violations(
            violations,
            mask,
            employees,
            "EMPLOYEE_DATE_ORDER",
            "employees",
            "termination_date precedes hire_date",
        )
    skills = tables["employee_skills"]
    if {"certified_date", "expiry_date"} <= set(skills.columns):
        certified, expiry = _date_values(skills["certified_date"]), _date_values(
            skills["expiry_date"]
        )
        mask = expiry.notna() & certified.notna() & (expiry < certified)
        _emit_row_violations(
            violations,
            mask,
            skills,
            "CERTIFICATION_DATE_ORDER",
            "employee_skills",
            "expiry_date precedes certified_date",
        )
    requirements = tables["coverage_requirements"]
    if "effective_date" in requirements.columns:
        valid = _date_values(requirements["effective_date"]).dropna()
        horizon_start = valid.min() if not valid.empty else None
        horizon_end = valid.max() if not valid.empty else None
    else:
        horizon_start = horizon_end = None

    assignments = tables["shift_assignments"]
    start = parsed_timestamps.get(("shift_assignments", "scheduled_start"))
    end = parsed_timestamps.get(("shift_assignments", "scheduled_end"))
    if start is not None and end is not None:
        mask = start.notna() & end.notna() & (end <= start)
        _emit_row_violations(
            violations,
            mask,
            assignments,
            "ASSIGNMENT_TIME_ORDER",
            "shift_assignments",
            "scheduled_end must be after scheduled_start",
        )
    effective_from = parsed_timestamps.get(("shift_assignments", "effective_from"))
    effective_to = parsed_timestamps.get(("shift_assignments", "effective_to"))
    if effective_from is not None and effective_to is not None:
        # Only check ACTIVE rows; SWAPPED_OUT rows legitimately have
        # effective_to = applied_at which can predate effective_from (horizon anchor).
        active_mask = assignments["assignment_status"] == "ACTIVE"
        mask = active_mask & effective_from.notna() & effective_to.notna() & (effective_to < effective_from)
        _emit_row_violations(
            violations,
            mask,
            assignments,
            "ASSIGNMENT_EFFECTIVE_ORDER",
            "shift_assignments",
            "effective_to precedes effective_from",
        )
    leaves = tables["leave_requests"]
    if {"start_date", "end_date"} <= set(leaves.columns):
        leave_start, leave_end = _date_values(leaves["start_date"]), _date_values(
            leaves["end_date"]
        )
        mask = leave_start.notna() & leave_end.notna() & (leave_end < leave_start)
        _emit_row_violations(
            violations,
            mask,
            leaves,
            "LEAVE_DATE_ORDER",
            "leave_requests",
            "end_date precedes start_date",
        )
    swaps = tables["swap_requests"]
    requested = parsed_timestamps.get(("swap_requests", "requested_at"))
    decided = parsed_timestamps.get(("swap_requests", "decided_at"))
    applied = parsed_timestamps.get(("swap_requests", "applied_at"))
    if requested is not None and decided is not None:
        mask = requested.notna() & decided.notna() & (decided < requested)
        _emit_row_violations(
            violations,
            mask,
            swaps,
            "SWAP_TIME_ORDER",
            "swap_requests",
            "decided_at precedes requested_at",
        )
    if requested is not None and applied is not None:
        mask = requested.notna() & applied.notna() & (applied < requested)
        _emit_row_violations(
            violations,
            mask,
            swaps,
            "SWAP_TIME_ORDER",
            "swap_requests",
            "applied_at precedes requested_at",
        )
    return horizon_start, horizon_end


def _approved_leave_days(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    leaves = tables["leave_requests"]
    if leaves.empty:
        return pd.DataFrame(columns=["employee_id", "shift_date", "leave_request_id"])
    approved = leaves.loc[leaves["current_status"] == "APPROVED"].copy()
    if approved.empty:
        return pd.DataFrame(columns=["employee_id", "shift_date", "leave_request_id"])
    starts = pd.to_datetime(approved["start_date"], errors="coerce")
    ends = pd.to_datetime(approved["end_date"], errors="coerce")
    valid = starts.notna() & ends.notna()
    if not valid.any():
        return pd.DataFrame(columns=["employee_id", "shift_date", "leave_request_id"])
    approved = approved.loc[valid]
    starts = starts.loc[valid]
    ends = ends.loc[valid]
    num_days = ((ends - starts).dt.days + 1).clip(lower=0)
    if (num_days == 0).all():
        return pd.DataFrame(columns=["employee_id", "shift_date", "leave_request_id"])
    repeated = approved.loc[approved.index.repeat(num_days)].copy()
    day_offsets = [offset for days in num_days for offset in range(days)]
    base_dates = starts.repeat(num_days)
    all_dates = base_dates + pd.to_timedelta(day_offsets, unit="D")
    repeated["shift_date"] = all_dates.dt.date.astype(str)
    return repeated[["employee_id", "shift_date", "leave_request_id"]].reset_index(drop=True)


def _check_schedule_rules(
    tables: dict[str, pd.DataFrame],
    violations: list[dict[str, str]],
    horizon_start: date | None,
    horizon_end: date | None,
) -> None:
    assignments = tables["shift_assignments"]
    active = _active_assignments(tables)
    employees = tables["employees"]
    skills = tables["employee_skills"]
    employee_lookup = employees.set_index("employee_id", drop=False)

    duplicate_mask = active.duplicated(
        ["employee_id", "shift_instance_id"], keep=False
    )
    _emit_row_violations(
        violations,
        duplicate_mask,
        active,
        "DUPLICATE_ACTIVE_ASSIGNMENT",
        "shift_assignments",
        "Employee has multiple ACTIVE rows for the same shift instance",
    )

    if not active.empty:
        joined = active.merge(
            employees[["employee_id", "role", "hire_date", "termination_date"]],
            on="employee_id",
            how="left",
            suffixes=("", "_employee"),
        )
        manager_mask = joined["role"] == "MANAGER"
        for _, row in joined.loc[manager_mask].iterrows():
            violations.append(
                _violation(
                    "MANAGER_SCHEDULED",
                    "shift_assignments",
                    row["assignment_id"],
                    row["employee_id"],
                    "A manager has an ACTIVE operational assignment",
                )
            )

        shift_dates = _date_values(joined["shift_date"])
        hire_dates = _date_values(joined["hire_date"])
        term_dates = _date_values(joined["termination_date"])
        before_hire = shift_dates.notna() & hire_dates.notna() & (shift_dates < hire_dates)
        after_term = (
            shift_dates.notna()
            & term_dates.notna()
            & (shift_dates > term_dates)
        )
        for mask, rule, detail in (
            (before_hire, "BEFORE_HIRE", "Assignment date precedes employee hire date"),
            (after_term, "AFTER_TERMINATION", "Assignment date follows employee termination"),
        ):
            for _, row in joined.loc[mask].iterrows():
                violations.append(
                    _violation(
                        rule,
                        "shift_assignments",
                        row["assignment_id"],
                        row["employee_id"],
                        detail,
                    )
                )

    qualified_rows = active.loc[
        active["fulfills_skill_id"].ne("")
    ].merge(
        skills,
        left_on=["employee_id", "fulfills_skill_id"],
        right_on=["employee_id", "skill_id"],
        how="left",
        suffixes=("", "_cert"),
    )
    if not qualified_rows.empty:
        shift_day = _date_values(qualified_rows["shift_date"])
        cert_day = _date_values(qualified_rows["certified_date"])
        expiry_day = _date_values(qualified_rows["expiry_date"])
        valid = (
            cert_day.notna()
            & shift_day.notna()
            & (cert_day <= shift_day)
            & (expiry_day.isna() | (expiry_day >= shift_day))
        )
        valid_keys = set(
            zip(
                qualified_rows.loc[valid, "assignment_id"],
                qualified_rows.loc[valid, "fulfills_skill_id"],
            )
        )
        for _, row in active.loc[active["fulfills_skill_id"].ne("")].iterrows():
            if (row["assignment_id"], row["fulfills_skill_id"]) not in valid_keys:
                violations.append(
                    _violation(
                        "SKILL_ABSENT",
                        "shift_assignments",
                        row["assignment_id"],
                        row["employee_id"],
                        f"No certification valid on {row['shift_date']} for {row['fulfills_skill_id']}",
                    )
                )

    leave_days = _approved_leave_days(tables)
    if not active.empty and not leave_days.empty:
        overlap = active.merge(
            leave_days,
            on=["employee_id", "shift_date"],
            how="inner",
        )
        for _, row in overlap.iterrows():
            violations.append(
                _violation(
                    "LEAVE_OVERLAP",
                    "shift_assignments",
                    row["assignment_id"],
                    row["employee_id"],
                    f"ACTIVE assignment overlaps approved leave {row['leave_request_id']}",
                )
            )

    starts = _timestamps(active["scheduled_start"]) if "scheduled_start" in active else pd.Series(dtype="datetime64[ns, UTC]")
    ends = _timestamps(active["scheduled_end"]) if "scheduled_end" in active else pd.Series(dtype="datetime64[ns, UTC]")
    if not active.empty and starts.notna().all() and ends.notna().all():
        scan = active.assign(_start=starts, _end=ends).sort_values(
            ["employee_id", "_start", "assignment_id"]
        )
        scan["_prev_end"] = scan.groupby("employee_id")["_end"].shift(1)
        scan["_prev_id"] = scan.groupby("employee_id")["assignment_id"].shift(1)

        # DOUBLE_BOOKED
        overlap_mask = scan["_prev_end"].notna() & (scan["_prev_end"] > scan["_start"])
        for _, current in scan.loc[overlap_mask].iterrows():
            violations.append(
                _violation(
                    "DOUBLE_BOOKED",
                    "shift_assignments",
                    current["assignment_id"],
                    current["employee_id"],
                    f"Overlaps ACTIVE assignment {current['_prev_id']}",
                )
            )

        # REST_VIOLATION
        rest_diff = (scan["_start"] - scan["_prev_end"]).dt.total_seconds() / 3600.0
        rest_mask = scan["_prev_end"].notna() & (scan["_start"] >= scan["_prev_end"]) & (rest_diff < 11.0)
        for _, current in scan.loc[rest_mask].iterrows():
            rest_hours = (current["_start"] - current["_prev_end"]).total_seconds() / 3600.0
            violations.append(
                _violation(
                    "REST_VIOLATION",
                    "shift_assignments",
                    current["assignment_id"],
                    current["employee_id"],
                    f"Only {rest_hours:.2f}h rest after {current['_prev_id']}",
                )
            )

    if not active.empty:
        worked_days = active.assign(
            _day=_date_values(active["shift_date"])
        ).drop_duplicates(["employee_id", "_day"]).sort_values(["employee_id", "_day"])
        worked_days["_day_dt"] = pd.to_datetime(worked_days["_day"])
        worked_days["_shift_6"] = worked_days.groupby("employee_id")["_day_dt"].shift(6)
        consec_mask = worked_days["_shift_6"].notna() & ((worked_days["_day_dt"] - worked_days["_shift_6"]).dt.days == 6)
        for _, row in worked_days.loc[consec_mask].iterrows():
            violations.append(
                _violation(
                    "MAX_CONSECUTIVE_DAYS",
                    "shift_assignments",
                    row["assignment_id"],
                    row["employee_id"],
                    f"Worked all 7 consecutive days ending {row['_day']}",
                )
            )

    if horizon_start is not None and horizon_end is not None and not active.empty:
        week_rows = active.copy()
        dates = pd.to_datetime(week_rows["shift_date"], errors="coerce")
        week_rows["_iso_year"] = dates.dt.isocalendar().year.astype(int)
        week_rows["_iso_week"] = dates.dt.isocalendar().week.astype(int)
        week_rows["_paid_hours"] = 7.5
        hours = week_rows.groupby(
            ["employee_id", "_iso_year", "_iso_week"], as_index=False
        ).agg(_hours=("_paid_hours", "sum"))
        overtime_hours = hours.loc[hours["_hours"] > 48]
        for _, row in overtime_hours.iterrows():
            monday = date.fromisocalendar(int(row["_iso_year"]), int(row["_iso_week"]), 1)
            sunday = monday + timedelta(days=6)
            if monday >= horizon_start and sunday <= horizon_end:
                same_week = week_rows.loc[
                    (week_rows["employee_id"] == row["employee_id"])
                    & (week_rows["_iso_year"] == row["_iso_year"])
                    & (week_rows["_iso_week"] == row["_iso_week"])
                ].sort_values(["shift_date", "assignment_id"])
                related_ids = sorted(same_week["assignment_id"].tolist())
                violations.append(
                    _violation(
                        "OVERTIME_CAP_EXCEEDED",
                        "shift_assignments",
                        related_ids[-1],
                        row["employee_id"],
                        f"{row['_hours']:.1f} paid hours in ISO week "
                        f"{int(row['_iso_year'])}-W{int(row['_iso_week']):02d}; "
                        f"assignments={json.dumps(related_ids)}",
                    )
                )


def _check_swap_history(
    tables: dict[str, pd.DataFrame], violations: list[dict[str, str]]
) -> None:
    requests = tables["swap_requests"].copy()
    if requests.empty:
        return
    requests["_requested_dt"] = pd.to_datetime(requests["requested_at"], utc=True)
    events = tables["swap_events"].copy()
    if not events.empty:
        events["_event_dt"] = pd.to_datetime(events["event_timestamp"], utc=True)
    assignments = tables["shift_assignments"].copy()
    if not assignments.empty:
        assignments["_sched_start_dt"] = pd.to_datetime(assignments["scheduled_start"], utc=True)
    employees = tables["employees"]
    employee_map = employees.set_index("employee_id", drop=False).to_dict("index")
    assignment_map = assignments.set_index("assignment_id", drop=False).to_dict("index")
    events_by_request = {
        key: group.sort_values(["event_timestamp", "swap_event_id"])
        for key, group in events.groupby("swap_request_id", sort=False)
    } if not events.empty else {}
    replacements = {
        key: group
        for key, group in assignments.loc[
            assignments["swap_request_id"].ne("")
        ].groupby("swap_request_id", sort=False)
    }
    for _, request in requests.iterrows():
        request_id = request["swap_request_id"]
        event_rows = events_by_request.get(request_id)
        if event_rows is None or event_rows.empty:
            violations.append(
                _violation(
                    "SWAP_MISSING_EVENTS",
                    "swap_requests",
                    request_id,
                    request["requestor_employee_id"],
                    "Swap request has no lifecycle events",
                )
            )
            continue
        event_records = event_rows.to_dict("records")
        event_statuses = [event["to_status"] for event in event_records]
        if event_records[0]["to_status"] != "REQUESTED" or event_records[0]["from_status"]:
            violations.append(
                _violation(
                    "SWAP_INVALID_INITIAL_EVENT",
                    "swap_requests",
                    request_id,
                    request["requestor_employee_id"],
                    "First event must transition from null to REQUESTED",
                )
            )
        prior_status = None
        prior_time = None
        has_approval = False
        has_rejection = False
        for event in event_records:
            event_time = event.get("_event_dt")
            if event_time is None:
                event_time = _timestamps(pd.Series([event["event_timestamp"]])).iloc[0]
            if prior_time is not None and event_time < prior_time:
                violations.append(
                    _violation(
                        "SWAP_EVENT_TIMESTAMP_ORDER",
                        "swap_events",
                        event["swap_event_id"],
                        request["requestor_employee_id"],
                        f"Event timestamp regresses for {request_id}",
                    )
                )
            if prior_status is not None and event["from_status"] != prior_status:
                violations.append(
                    _violation(
                        "SWAP_EVENT_CHAIN",
                        "swap_events",
                        event["swap_event_id"],
                        request["requestor_employee_id"],
                        f"from_status does not follow {prior_status} for {request_id}",
                    )
                )
            status = event["to_status"]
            if status == "APPROVED":
                has_approval = True
            if status == "REJECTED":
                has_rejection = True
            valid_transition = (
                prior_status is None and status == "REQUESTED"
            ) or (
                prior_status == "REQUESTED" and status in {"APPROVED", "REJECTED"}
            ) or (prior_status == "APPROVED" and status == "APPLIED")
            if not valid_transition and status == "APPLIED":
                # Direct REQUESTED -> APPLIED and REJECTED -> APPLIED are one rule.
                pass
            elif not valid_transition:
                violations.append(
                    _violation(
                        "SWAP_INVALID_TRANSITION",
                        "swap_events",
                        event["swap_event_id"],
                        request["requestor_employee_id"],
                        f"Invalid {prior_status!r} -> {status!r} for {request_id}",
                    )
                )
            actor_type = event["actor_type"]
            actor_id = event["actor_id"]
            if actor_type == "SYSTEM" and actor_id:
                violations.append(
                    _violation(
                        "SWAP_SYSTEM_ACTOR_HAS_ID",
                        "swap_events",
                        event["swap_event_id"],
                        request["requestor_employee_id"],
                        "SYSTEM actor_id must be empty",
                    )
                )
            if actor_type == "EMPLOYEE" and status != "REQUESTED":
                actor = employee_map.get(actor_id)
                participants = {
                    request["requestor_employee_id"],
                    request["target_employee_id"],
                }
                if (
                    actor is None
                    or actor["role"] != "MANAGER"
                    or actor_id in participants
                    or actor["team_id"]
                    != employee_map.get(request["requestor_employee_id"], {}).get(
                        "team_id"
                    )
                ):
                    violations.append(
                        _violation(
                            "SWAP_INVALID_MANAGER_ACTOR",
                            "swap_events",
                            event["swap_event_id"],
                            request["requestor_employee_id"],
                            "Decision actor must be a non-participant manager on the requestor's team",
                        )
                    )
            prior_status = status
            prior_time = event_time

        latest_status = event_records[-1]["to_status"]
        if request["current_status"] != latest_status:
            violations.append(
                _violation(
                    "SWAP_STATUS_MISMATCH",
                    "swap_requests",
                    request_id,
                    request["requestor_employee_id"],
                    f"current_status={request['current_status']}, latest event={latest_status}",
                )
            )
        if request["current_status"] == "REJECTED" and not request["rejection_reason"]:
            violations.append(
                _violation(
                    "SWAP_REJECTION_WITHOUT_REASON",
                    "swap_requests",
                    request_id,
                    request["requestor_employee_id"],
                    "Rejected request must contain rejection_reason",
                )
            )
        if request["current_status"] == "APPROVED":
            violations.append(
                _violation(
                    "SWAP_STUCK_APPROVED",
                    "swap_requests",
                    request_id,
                    request["requestor_employee_id"],
                    "Approved swaps must be applied before pipeline completion",
                )
            )

        requester_assignment = assignment_map.get(request["requestor_assignment_id"])
        target_assignment = assignment_map.get(request["target_assignment_id"])
        requester = employee_map.get(request["requestor_employee_id"])
        target = employee_map.get(request["target_employee_id"])
        if not all((requester_assignment, target_assignment, requester, target)):
            continue
        if requester["team_id"] != target["team_id"]:
            violations.append(
                _violation(
                    "SWAP_DIFFERENT_TEAM",
                    "swap_requests",
                    request_id,
                    request["requestor_employee_id"],
                    "Swap participants belong to different teams",
                )
            )
        first_date = pd.to_datetime(requester_assignment["shift_date"], errors="coerce")
        second_date = pd.to_datetime(target_assignment["shift_date"], errors="coerce")
        if not pd.isna(first_date) and not pd.isna(second_date):
            if first_date.isocalendar()[:2] != second_date.isocalendar()[:2]:
                violations.append(
                    _violation(
                        "SWAP_DIFFERENT_ISO_WEEK",
                        "swap_requests",
                        request_id,
                        request["requestor_employee_id"],
                        "Assignments are not in the same ISO week",
                    )
                )
            starts = _timestamps(
                pd.Series(
                    [
                        requester_assignment["scheduled_start"],
                        target_assignment["scheduled_start"],
                    ]
                )
            )
            requested_at = request.get("_requested_dt")
            if requested_at is None:
                requested_at = _timestamps(pd.Series([request["requested_at"]])).iloc[0]
            if starts.notna().all() and requested_at >= starts.min():
                violations.append(
                    _violation(
                        "SWAP_REQUEST_NOT_BEFORE_SHIFTS",
                        "swap_requests",
                        request_id,
                        request["requestor_employee_id"],
                        "requested_at must precede both requested shifts",
                    )
                )
        if request["current_status"] == "APPLIED":
            if not has_approval or has_rejection:
                violations.append(
                    _violation(
                        "SWAP_WITHOUT_APPROVAL",
                        "swap_requests",
                        request_id,
                        request["requestor_employee_id"],
                        "APPLIED swap lacks an earlier APPROVED event or has a REJECTED event",
                    )
                )
            applied_events = [
                event for event in event_records if event["to_status"] == "APPLIED"
            ]
            applied_at = request["applied_at"]
            if len(applied_events) != 1 or not applied_at:
                violations.append(
                    _violation(
                        "SWAP_HISTORY_INCOMPLETE",
                        "swap_requests",
                        request_id,
                        request["requestor_employee_id"],
                        "Applied request must have one APPLIED event and applied_at",
                    )
                )
            elif applied_events[0]["event_timestamp"] != applied_at:
                violations.append(
                    _violation(
                        "SWAP_APPLIED_TIMESTAMP_MISMATCH",
                        "swap_requests",
                        request_id,
                        request["requestor_employee_id"],
                        "applied_at differs from APPLIED event timestamp",
                    )
                )
            expected_old = {
                request["requestor_assignment_id"]: request["requestor_employee_id"],
                request["target_assignment_id"]: request["target_employee_id"],
            }
            for old_id, employee_id in expected_old.items():
                old = assignment_map.get(old_id)
                if old is None:
                    continue
                if old["employee_id"] != employee_id:
                    violations.append(
                        _violation(
                            "SWAP_IN_PLACE_OVERWRITE",
                            "shift_assignments",
                            old_id,
                            employee_id,
                            "Original assignment employee_id was overwritten",
                        )
                    )
                if (
                    old["assignment_status"] != "SWAPPED_OUT"
                    or old["effective_to"] != applied_at
                ):
                    violations.append(
                        _violation(
                            "SWAP_OLD_HISTORY_NOT_CLOSED",
                            "shift_assignments",
                            old_id,
                            employee_id,
                            "Original assignment must be SWAPPED_OUT and closed at applied_at",
                        )
                    )
            replacement_rows = replacements.get(request_id, pd.DataFrame())
            if len(replacement_rows) != 2:
                violations.append(
                    _violation(
                        "SWAP_REPLACEMENT_COUNT",
                        "swap_requests",
                        request_id,
                        request["requestor_employee_id"],
                        "Applied swap must have exactly two replacement assignments",
                    )
                )
            expected_replacements = {
                request["requestor_assignment_id"]: request["target_employee_id"],
                request["target_assignment_id"]: request["requestor_employee_id"],
            }
            found_replacements = (
                {
                    row["replaces_assignment_id"]: row
                    for _, row in replacement_rows.iterrows()
                }
                if not replacement_rows.empty
                else {}
            )
            if set(found_replacements) != set(expected_replacements):
                violations.append(
                    _violation(
                        "SWAP_REPLACEMENT_LINEAGE",
                        "swap_requests",
                        request_id,
                        request["requestor_employee_id"],
                        "Replacement rows do not link one-to-one to the original assignments",
                    )
                )
            for old_id, expected_employee in expected_replacements.items():
                replacement = found_replacements.get(old_id)
                if replacement is None:
                    continue
                rep_status = replacement["assignment_status"]
                # A replacement that was itself subsequently swapped out may have
                # effective_to set; only require effective_to to be null for ACTIVE rows.
                effective_to_bad = (
                    replacement["effective_to"]
                    and rep_status == "ACTIVE"
                )
                if (
                    replacement["employee_id"] != expected_employee
                    or replacement["effective_from"] != applied_at
                    or effective_to_bad
                    or rep_status not in {"ACTIVE", "SWAPPED_OUT"}
                ):
                    violations.append(
                        _violation(
                            "SWAP_REPLACEMENT_INVALID",
                            "shift_assignments",
                            replacement["assignment_id"],
                            replacement["employee_id"],
                            "Replacement must preserve receiver, applied timestamp, status, and open/current history",
                        )
                    )
        elif request_id in replacements:
            violations.append(
                _violation(
                    "SWAP_UNAPPLIED_HAS_REPLACEMENTS",
                    "swap_requests",
                    request_id,
                    request["requestor_employee_id"],
                    "Non-applied request has replacement assignments",
                )
            )


def _check_lifecycle(
    tables: dict[str, pd.DataFrame], violations: list[dict[str, str]]
) -> None:
    requests = tables["leave_requests"]
    events = tables["leave_events"].copy()
    if not events.empty:
        events["_event_dt"] = pd.to_datetime(events["event_timestamp"], utc=True)
    employees = tables["employees"]
    emp_index = employees.set_index("employee_id") if not employees.empty else pd.DataFrame()
    if not requests.empty:
        groups = {
            key: group.sort_values(["event_timestamp", "leave_event_id"])
            for key, group in events.groupby("leave_request_id", sort=False)
        } if not events.empty else {}
        allowed = {
            "SUBMITTED": {"APPROVED", "REJECTED", "CANCELLED"},
            "APPROVED": {"CANCELLED"},
            "REJECTED": set(),
            "CANCELLED": set(),
        }
        for _, request in requests.iterrows():
            request_id = request["leave_request_id"]
            group = groups.get(request_id)
            if group is None or group.empty:
                violations.append(
                    _violation(
                        "LEAVE_MISSING_EVENTS",
                        "leave_requests",
                        request_id,
                        request["employee_id"],
                        "Leave request has no lifecycle events",
                    )
                )
                continue
            event_records = group.to_dict("records")
            if request["current_status"] != event_records[-1]["to_status"]:
                violations.append(
                    _violation(
                        "LEAVE_STATUS_MISMATCH",
                        "leave_requests",
                        request_id,
                        request["employee_id"],
                        f"current_status={request['current_status']}, latest event={event_records[-1]['to_status']}",
                    )
                )
            prior_status = None
            prior_timestamp = None
            for event in event_records:
                timestamp = event.get("_event_dt")
                if timestamp is None:
                    timestamp = _timestamps(pd.Series([event["event_timestamp"]])).iloc[0]
                if prior_timestamp is not None and timestamp < prior_timestamp:
                    violations.append(
                        _violation(
                            "LEAVE_EVENT_TIMESTAMP_ORDER",
                            "leave_events",
                            event["leave_event_id"],
                            request["employee_id"],
                            f"Event timestamp regresses for {request_id}",
                        )
                    )
                if prior_status is None:
                    valid = event["to_status"] == "SUBMITTED" and not event["from_status"]
                    if (
                        event["actor_type"] != "EMPLOYEE"
                        or event["actor_id"] != request["employee_id"]
                    ):
                        violations.append(
                            _violation(
                                "LEAVE_REQUESTOR_ACTOR",
                                "leave_events",
                                event["leave_event_id"],
                                request["employee_id"],
                                "Initial event must be authored by requestor",
                            )
                        )
                else:
                    valid = (
                        event["from_status"] == prior_status
                        and event["to_status"] in allowed.get(prior_status, set())
                    )
                    if event["actor_type"] == "SYSTEM":
                        if event["actor_id"]:
                            violations.append(
                                _violation(
                                    "LEAVE_SYSTEM_ACTOR_HAS_ID",
                                    "leave_events",
                                    event["leave_event_id"],
                                    request["employee_id"],
                                    "SYSTEM actor_id must be empty",
                                )
                            )
                    else:
                        to_status = event["to_status"]
                        actor_id = event["actor_id"]
                        requester_id = request["employee_id"]
                        if to_status == "CANCELLED" and actor_id == requester_id:
                            pass  # self-cancellation is valid
                        else:
                            try:
                                actor_row = emp_index.loc[actor_id]
                                actor_is_manager = actor_row["role"] == "MANAGER"
                                actor_team = actor_row["team_id"]
                            except KeyError:
                                actor_is_manager = False
                                actor_team = None

                            try:
                                req_row = emp_index.loc[requester_id]
                                req_is_manager = req_row["role"] == "MANAGER"
                                req_team = req_row["team_id"]
                            except KeyError:
                                req_is_manager = False
                                req_team = None

                            if not actor_is_manager or actor_id == requester_id:
                                violations.append(
                                    _violation(
                                        "LEAVE_INVALID_MANAGER_ACTOR",
                                        "leave_events",
                                        event["leave_event_id"],
                                        request["employee_id"],
                                        "Decision actor must be a manager and not the requester",
                                    )
                                )
                            elif not req_is_manager and actor_team != req_team:
                                violations.append(
                                    _violation(
                                        "LEAVE_INVALID_MANAGER_ACTOR",
                                        "leave_events",
                                        event["leave_event_id"],
                                        request["employee_id"],
                                        "Non-manager leave must be decided by same-team manager",
                                    )
                                )
                if not valid:
                    violations.append(
                        _violation(
                            "LEAVE_INVALID_TRANSITION",
                            "leave_events",
                            event["leave_event_id"],
                            request["employee_id"],
                            f"Invalid lifecycle event for {request_id}",
                        )
                    )
                prior_status = event["to_status"]
                prior_timestamp = timestamp


def _check_derived_tables(
    tables: dict[str, pd.DataFrame],
    violations: list[dict[str, str]],
    horizon_start: date | None,
    horizon_end: date | None,
) -> None:
    active = _active_assignments(tables)
    requirements = tables["coverage_requirements"]
    gaps = tables["coverage_gaps"]
    if not requirements.empty and not gaps.empty:
        expected = requirements.merge(
            gaps,
            on="coverage_id",
            how="left",
            suffixes=("_requirement", "_gap"),
            indicator=True,
        )
        active_skill = active.loc[active["fulfills_skill_id"].ne("")]
        counts = (
            active_skill.groupby(
                ["shift_date", "team_id", "shift_template_id", "fulfills_skill_id"]
            )
            .size()
            .to_dict()
        )
        for _, row in expected.iterrows():
            coverage_id = row["coverage_id"]
            if row["_merge"] != "both":
                violations.append(
                    _violation(
                        "COVERAGE_GAP_MISSING",
                        "coverage_gaps",
                        coverage_id,
                        "",
                        "No CoverageGap row for CoverageRequirement",
                    )
                )
                continue
            key = (
                row["effective_date_requirement"],
                row["team_id_requirement"],
                row["shift_template_id_requirement"],
                row["skill_id_requirement"],
            )
            scheduled = int(counts.get(key, 0))
            minimum = _as_int(row["min_headcount"])
            target = _as_int(row["target_headcount"])
            missing = (
                _as_int(row["scheduled_headcount"]) != scheduled
                or _as_int(row["required_headcount"]) != minimum
                or _as_int(row["gap_headcount"]) != max(0, minimum - scheduled)
                or _as_int(row["target_shortfall"]) != max(0, target - scheduled)
                or _as_bool(row["is_critical"]) != (scheduled < minimum)
                or _as_bool(row["has_gap"]) != (scheduled < minimum)
            )
            if missing:
                violations.append(
                    _violation(
                        "COVERAGE_GAP_RECONCILIATION",
                        "coverage_gaps",
                        row["gap_id"],
                        "",
                        f"Stored headcounts disagree with ACTIVE assignment count {scheduled}",
                    )
                )

    employees = tables["employees"]
    summaries = tables["weekly_hours_summaries"]
    if not summaries.empty:
        active_weekly = active.copy()
        dates = _date_values(active_weekly["shift_date"])
        active_weekly["_iso_year"] = [
            d.isocalendar().year if pd.notna(d) else 0 for d in dates
        ]
        active_weekly["_iso_week"] = [
            d.isocalendar().week if pd.notna(d) else 0 for d in dates
        ]
        actual_hours = (
            active_weekly.groupby(
                ["employee_id", "_iso_year", "_iso_week"]
            )
            .size()
            .mul(7.5)
            .to_dict()
        )
        contract = employees.set_index("employee_id")["weekly_contract_hours"].to_dict()
        seen_summary_keys = set()
        for _, row in summaries.iterrows():
            key = (row["employee_id"], _as_int(row["iso_year"]), _as_int(row["iso_week"]))
            if key in seen_summary_keys:
                continue
            seen_summary_keys.add(key)
            hours = float(actual_hours.get(key, 0.0))
            expected_contract = _as_float(contract.get(row["employee_id"], 0))
            regular = min(hours, expected_contract)
            overtime = max(0.0, hours - expected_contract)
            partial = False
            if horizon_start is not None and horizon_end is not None:
                monday = date.fromisocalendar(key[1], key[2], 1)
                sunday = monday + timedelta(days=6)
                partial = monday < horizon_start or sunday > horizon_end
            if (
                _as_float(row["scheduled_hours"]) != hours
                or _as_float(row["contract_hours"]) != expected_contract
                or _as_float(row["regular_hours"]) != regular
                or _as_float(row["overtime_hours"]) != overtime
                or _as_bool(row["is_partial_week"]) != partial
                or _as_bool(row["exceeds_overtime_cap"])
                != (not partial and hours > 48)
            ):
                violations.append(
                    _violation(
                        "WEEKLY_HOURS_RECONCILIATION",
                        "weekly_hours_summaries",
                        row["summary_id"],
                        row["employee_id"],
                        f"Stored hours disagree with ACTIVE schedule ({hours:.1f}h)",
                    )
                )
        expected_week_count = 0
        if horizon_start is not None and horizon_end is not None:
            week_keys = set()
            current = horizon_start
            while current <= horizon_end:
                week_keys.add(current.isocalendar()[:2])
                current += timedelta(days=1)
            expected_week_count = len(employees) * len(week_keys)
        if len(summaries) != expected_week_count:
            violations.append(
                _violation(
                    "WEEKLY_HOURS_ROW_COUNT",
                    "weekly_hours_summaries",
                    "",
                    "",
                    f"Expected {expected_week_count} employee-week rows, found {len(summaries)}",
                )
            )


def _check_attendance(
    tables: dict[str, pd.DataFrame], violations: list[dict[str, str]], horizon_end: date | None
) -> None:
    assignments = tables["shift_assignments"]
    attendance = tables["attendance_events"]
    leaves = _approved_leave_days(tables)
    if assignments.empty:
        return

    # Check ATTENDANCE_ON_SWAPPED_OUT and ATTENDANCE_DURING_APPROVED_LEAVE vectorised
    leave_keys = set(zip(leaves["employee_id"], leaves["shift_date"])) if not leaves.empty else set()
    if not attendance.empty:
        att_merged = attendance.merge(
            assignments[["assignment_id", "assignment_status", "shift_date"]],
            on="assignment_id",
            how="inner",
        )
        swapped_out_mask = att_merged["assignment_status"] != "ACTIVE"
        for _, event in att_merged.loc[swapped_out_mask].iterrows():
            violations.append(
                _violation(
                    "ATTENDANCE_ON_SWAPPED_OUT",
                    "attendance_events",
                    event["attendance_event_id"],
                    event["employee_id"],
                    f"Attendance references {event['assignment_status']} assignment",
                )
            )
        if leave_keys:
            att_leave_mask = pd.Series(
                list(zip(att_merged["employee_id"], att_merged["shift_date"])),
                index=att_merged.index,
            ).isin(leave_keys)
            for _, event in att_merged.loc[att_leave_mask].iterrows():
                violations.append(
                    _violation(
                        "ATTENDANCE_DURING_APPROVED_LEAVE",
                        "attendance_events",
                        event["attendance_event_id"],
                        event["employee_id"],
                        "Attendance occurs on an approved-leave date",
                    )
                )

    shift_dates = pd.to_datetime(assignments["shift_date"], errors="coerce").dt.date
    active_mask = assignments["assignment_status"] == "ACTIVE"
    if horizon_end is not None:
        active_mask &= (shift_dates <= horizon_end)
    if leave_keys:
        is_on_leave = pd.Series(
            list(zip(assignments["employee_id"], assignments["shift_date"])),
            index=assignments.index,
        ).isin(leave_keys)
        active_mask &= (~is_on_leave)

    eligible_assignments = assignments.loc[active_mask].copy()
    if eligible_assignments.empty:
        return

    att_assignment_ids = set(attendance["assignment_id"].dropna().unique()) if not attendance.empty else set()
    missing_mask = ~eligible_assignments["assignment_id"].isin(att_assignment_ids)
    for _, row in eligible_assignments.loc[missing_mask].iterrows():
        violations.append(
            _violation(
                "ATTENDANCE_MISSING_EVENTS",
                "shift_assignments",
                row["assignment_id"],
                row["employee_id"],
                "Eligible ACTIVE assignment has no attendance record",
            )
        )

    present_eligible = eligible_assignments.loc[~missing_mask]
    if present_eligible.empty or attendance.empty:
        return

    # Attendance reconciliation per assignment
    counts = attendance.groupby(["assignment_id", "event_type"]).size().unstack(fill_value=0)
    for col in ["NO_SHOW", "CLOCK_IN", "CLOCK_OUT"]:
        if col not in counts.columns:
            counts[col] = 0

    no_shows = attendance[attendance["event_type"] == "NO_SHOW"]
    clock_ins = attendance[attendance["event_type"] == "CLOCK_IN"].rename(columns=lambda c: f"in_{c}")
    clock_outs = attendance[attendance["event_type"] == "CLOCK_OUT"].rename(columns=lambda c: f"out_{c}")

    # For NO_SHOW assignments
    ns_m = no_shows.merge(present_eligible, on="assignment_id", how="inner")
    for _, row in ns_m.iterrows():
        aid = row["assignment_id"]
        c = counts.loc[aid]
        if (
            c["NO_SHOW"] != 1
            or c["CLOCK_IN"] > 0
            or c["CLOCK_OUT"] > 0
            or row["variance_minutes"] != ""
            or _as_int(row["worked_minutes"], -1) != 0
            or not _as_bool(row["is_exception"])
            or row["exception_type"] != "NO_SHOW"
            or row["event_timestamp"] != row["scheduled_start"]
        ):
            violations.append(
                _violation(
                    "ATTENDANCE_RECONCILIATION",
                    "attendance_events",
                    row["attendance_event_id"],
                    row["employee_id_x"],
                    "NO_SHOW record does not reconcile to schedule",
                )
            )

    # For CLOCK_IN / CLOCK_OUT assignments
    eligible_counts = counts.reindex(present_eligible["assignment_id"]).fillna(0)
    non_ns_eligible = eligible_counts[eligible_counts["NO_SHOW"] == 0]
    bad_punch_counts = non_ns_eligible[(non_ns_eligible["CLOCK_IN"] != 1) | (non_ns_eligible["CLOCK_OUT"] != 1)]
    for aid in bad_punch_counts.index:
        emp_id = present_eligible.loc[present_eligible["assignment_id"] == aid, "employee_id"].iloc[0]
        violations.append(
            _violation(
                "ATTENDANCE_RECONCILIATION",
                "shift_assignments",
                aid,
                emp_id,
                "Expected exactly one CLOCK_IN and one CLOCK_OUT",
            )
        )

    valid_punch_aids = set(non_ns_eligible[(non_ns_eligible["CLOCK_IN"] == 1) & (non_ns_eligible["CLOCK_OUT"] == 1)].index)
    if not valid_punch_aids:
        return

    m = clock_ins[clock_ins["in_assignment_id"].isin(valid_punch_aids)].merge(
        clock_outs[clock_outs["out_assignment_id"].isin(valid_punch_aids)],
        left_on="in_assignment_id",
        right_on="out_assignment_id",
        how="inner",
    ).merge(
        present_eligible,
        left_on="in_assignment_id",
        right_on="assignment_id",
        how="inner",
    )

    sched_start = pd.to_datetime(m["scheduled_start"], errors="coerce", utc=True)
    sched_end = pd.to_datetime(m["scheduled_end"], errors="coerce", utc=True)
    act_in = pd.to_datetime(m["in_event_timestamp"], errors="coerce", utc=True)
    act_out = pd.to_datetime(m["out_event_timestamp"], errors="coerce", utc=True)

    in_var = ((act_in - sched_start).dt.total_seconds() / 60.0).round().astype(int)
    out_var = ((act_out - sched_end).dt.total_seconds() / 60.0).round().astype(int)
    worked_min = ((act_out - act_in).dt.total_seconds() / 60.0).round().astype(int)

    exp_in_exc = np.where(in_var > 10, "LATE", "")
    exp_out_exc = np.where(out_var < -15, "EARLY_LEAVE", np.where(out_var > 15, "OVERTIME", ""))

    mismatches = (
        (m["in_variance_minutes"].astype(str) != in_var.astype(str))
        | (m["out_variance_minutes"].astype(str) != out_var.astype(str))
        | (pd.to_numeric(m["out_worked_minutes"], errors="coerce").fillna(-1).astype(int) != worked_min)
        | (m["in_exception_type"].fillna("") != exp_in_exc)
        | (m["out_exception_type"].fillna("") != exp_out_exc)
        | (m["in_is_exception"].astype(str).str.lower().isin(["true", "1"]) != (exp_in_exc != ""))
        | (m["out_is_exception"].astype(str).str.lower().isin(["true", "1"]) != (exp_out_exc != ""))
        | (m["in_employee_id"] != m["employee_id"])
        | (m["out_employee_id"] != m["employee_id"])
    )

    exp_in_id = "ATT-" + m["in_employee_id"] + "-" + act_in.dt.strftime("%Y%m%dT%H%M%SZ")
    exp_out_id = "ATT-" + m["out_employee_id"] + "-" + act_out.dt.strftime("%Y%m%dT%H%M%SZ")
    mismatches |= (m["in_attendance_event_id"] != exp_in_id)
    mismatches |= (m["out_attendance_event_id"] != exp_out_id)

    for _, row in m.loc[mismatches].iterrows():
        violations.append(
            _violation(
                "ATTENDANCE_RECONCILIATION",
                "attendance_events",
                row["assignment_id"],
                row["employee_id"],
                "Punch timestamps, variance, worked minutes, or exceptions do not reconcile",
            )
        )


def validate_dataset(data_dir: str | Path) -> list[dict[str, str]]:
    """Validate all exported CSV tables and return normalized violation records."""
    violations: list[dict[str, str]] = []
    tables = _read_tables(Path(data_dir), violations)
    _check_primary_keys_and_foreign_keys(tables, violations)
    _check_enums(tables, violations)
    horizon_start, horizon_end = _check_dates_and_timestamps(tables, violations)
    _check_schedule_rules(tables, violations, horizon_start, horizon_end)
    _check_lifecycle(tables, violations)
    _check_swap_history(tables, violations)
    _check_derived_tables(tables, violations, horizon_start, horizon_end)
    _check_attendance(tables, violations, horizon_end)
    violations.sort(
        key=lambda item: (
            item["rule"],
            item["entity"],
            item["record_id"],
            item["employee_id"],
            item["details"],
        )
    )
    return violations


def _write_outputs(
    violations: list[dict[str, str]], out_dir: Path, elapsed_seconds: float
) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(violations, columns=VIOLATION_COLUMNS)
    frame.to_csv(
        out_dir / "validation_violations.csv",
        index=False,
        encoding="utf-8",
        lineterminator="\n",
    )
    counts = frame.groupby("rule").size().sort_index().to_dict() if not frame.empty else {}
    lines = [
        "# Validation Summary",
        "",
        f"- Total violations: {len(violations)}",
        f"- Runtime seconds: {elapsed_seconds:.4f}",
        "",
        "## Counts by rule",
        "",
        "| Rule | Count |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {rule} | {count} |" for rule, count in counts.items())
    if not counts:
        lines.append("| None | 0 |")
    (out_dir / "validation_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )
    return {str(rule): int(count) for rule, count in counts.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    violations = validate_dataset(args.data_dir)
    elapsed = time.perf_counter() - started
    counts = _write_outputs(violations, args.out, elapsed)
    print(f"Validated {args.data_dir}")
    print(f"Violations: {len(violations)}")
    print(f"Runtime seconds: {elapsed:.4f}")
    print(f"Counts by rule: {json.dumps(counts, sort_keys=True)}")
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())

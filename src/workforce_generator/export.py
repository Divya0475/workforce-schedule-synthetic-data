"""Stable UTF-8 CSV export and dataset manifest generation."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


TABLE_PRIMARY_KEYS = {
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

GROUND_TRUTH_COLUMNS = (
    "anomaly_id",
    "issue_type",
    "entity",
    "record_id",
    "related_record_ids",
    "employee_id",
    "description",
    "also_triggers",
)


def _serialize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _table_records(rows: Sequence[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        if hasattr(row, "to_dict"):
            record = row.to_dict()
        elif isinstance(row, Mapping):
            record = dict(row)
        else:
            raise TypeError(f"CSV rows must be mappings or expose to_dict(): {type(row)!r}")
        records.append(record)
    return records


def _write_csv(
    path: Path,
    rows: Sequence[Any],
    primary_key: str | None,
    columns: Sequence[str] | None = None,
) -> tuple[int, str]:
    records = _table_records(rows)
    if columns is None:
        if records:
            columns = tuple(records[0].keys())
            expected = set(columns)
            if any(set(record) != expected for record in records):
                raise ValueError(f"Rows in {path.name} do not have a stable schema")
        else:
            raise ValueError(f"Column names are required when exporting empty table {path.name}")
    if primary_key is not None:
        if any(primary_key not in record for record in records):
            raise ValueError(f"Missing primary key {primary_key!r} in {path.name}")
        records.sort(key=lambda record: str(record[primary_key]))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(columns),
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        for record in records:
            writer.writerow(
                {column: _serialize(record.get(column)) for column in columns}
            )

    content = path.read_bytes()
    return len(records), hashlib.sha256(content).hexdigest()


def _export_tables(
    destination: Path,
    subdirectory: str,
    tables: Mapping[str, Sequence[Any]],
) -> dict[str, dict[str, Any]]:
    manifest_files: dict[str, dict[str, Any]] = {}
    unknown_tables = set(tables).difference(TABLE_PRIMARY_KEYS)
    if unknown_tables:
        raise ValueError(f"Unregistered CSV tables: {sorted(unknown_tables)}")
    for table_name in sorted(TABLE_PRIMARY_KEYS):
        if table_name not in tables:
            raise ValueError(f"Missing required table: {table_name}")
        rows = tables[table_name]
        if rows:
            first = rows[0]
            columns = tuple(first.to_dict().keys()) if hasattr(first, "to_dict") else tuple(first)
        else:
            columns = _empty_table_columns(table_name)
        relative_path = destination / subdirectory / f"{table_name}.csv"
        count, digest = _write_csv(
            relative_path,
            rows,
            TABLE_PRIMARY_KEYS[table_name],
            columns,
        )
        manifest_files[relative_path.relative_to(destination).as_posix()] = {
            "row_count": count,
            "sha256": digest,
        }
    return manifest_files


def _empty_table_columns(table_name: str) -> tuple[str, ...]:
    from dataclasses import fields
    from workforce_generator.models import entities

    class_by_table = {
        "teams": entities.Team,
        "skills": entities.Skill,
        "employees": entities.Employee,
        "employee_skills": entities.EmployeeSkill,
        "shift_templates": entities.ShiftTemplate,
        "holidays": entities.HolidayCalendar,
        "coverage_requirements": entities.CoverageRequirement,
        "leave_requests": entities.LeaveRequest,
        "leave_events": entities.LeaveRequestEvent,
        "shift_assignments": entities.ShiftAssignment,
        "swap_requests": entities.SwapRequest,
        "swap_events": entities.SwapRequestEvent,
        "attendance_events": entities.AttendanceEvent,
        "coverage_gaps": entities.CoverageGap,
        "weekly_hours_summaries": entities.WeeklyHoursSummary,
    }
    return tuple(field.name for field in fields(class_by_table[table_name]))


def write_dataset(
    output_dir: str | Path,
    profile: str,
    clean_tables: Mapping[str, Sequence[Any]],
    dirty_tables: Mapping[str, Sequence[Any]],
    ground_truth: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Write clean/dirty CSV tables, ground truth, and a per-file manifest."""
    if profile not in {"starter", "full"}:
        raise ValueError(f"Unsupported profile: {profile!r}")
    destination = Path(output_dir) / profile
    file_entries: dict[str, dict[str, Any]] = {}
    file_entries.update(_export_tables(destination, "clean", clean_tables))
    file_entries.update(_export_tables(destination, "dirty", dirty_tables))

    truth_path = destination / "ground_truth" / "ground_truth.csv"
    truth_rows = list(ground_truth)
    count, digest = _write_csv(
        truth_path,
        truth_rows,
        "anomaly_id",
        GROUND_TRUTH_COLUMNS,
    )
    file_entries[truth_path.relative_to(destination).as_posix()] = {
        "row_count": count,
        "sha256": digest,
    }

    json_path = destination / "ground_truth" / "ground_truth.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_content = (
        json.dumps(truth_rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    json_path.write_bytes(json_content)
    file_entries[json_path.relative_to(destination).as_posix()] = {
        "row_count": count,
        "sha256": hashlib.sha256(json_content).hexdigest(),
    }

    manifest = {
        "profile": profile,
        "row_counts": {
            "clean": {table: len(rows) for table, rows in sorted(clean_tables.items())},
            "dirty": {table: len(rows) for table, rows in sorted(dirty_tables.items())},
            "ground_truth": count,
        },
        "files": dict(sorted(file_entries.items())),
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest

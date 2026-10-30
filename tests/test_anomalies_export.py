from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np

from workforce_generator.anomalies import (
    ANOMALY_TYPES,
    generate_dirty_dataset,
)
from workforce_generator.export import TABLE_PRIMARY_KEYS, write_dataset
from workforce_generator.rules import check_employee_rule_findings


def _snapshots(data):
    return {
        name: [row.to_dict() for row in rows]
        for name, rows in data.items()
        if isinstance(rows, list)
    }


def _inject_starter(data):
    cfg = data["config"]
    streams = np.random.SeedSequence(cfg.seed).spawn(6)[5].spawn(2)
    return generate_dirty_dataset(
        cfg,
        data,
        np.random.default_rng(streams[0]),
        np.random.default_rng(streams[1]),
        counts_per_type=3,
    )


def _parse_utc(value):
    return datetime.fromisoformat(value.rstrip("Z"))


def test_starter_anomalies_are_targeted_and_ground_truth_resolves(
    starter_pipeline_data, tmp_path
):
    clean_data = starter_pipeline_data
    clean_tables = {
        name: rows
        for name, rows in clean_data.items()
        if isinstance(rows, list)
    }
    write_dataset(tmp_path / "before", "starter", clean_tables, clean_tables, [])
    clean_before = _snapshots(clean_data)
    dirty, ground_truth = _inject_starter(clean_data)
    assert _snapshots(clean_data) == clean_before
    write_dataset(tmp_path / "after", "starter", clean_tables, clean_tables, [])
    for table_name in TABLE_PRIMARY_KEYS:
        before_bytes = (
            tmp_path / "before" / "starter" / "clean" / f"{table_name}.csv"
        ).read_bytes()
        after_bytes = (
            tmp_path / "after" / "starter" / "clean" / f"{table_name}.csv"
        ).read_bytes()
        assert before_bytes == after_bytes
    assert Counter(row["issue_type"] for row in ground_truth) == {
        anomaly_type: 3 for anomaly_type in ANOMALY_TYPES
    }

    clean_primary_keys = {
        table: {row[TABLE_PRIMARY_KEYS[table]] for row in clean_before[table]}
        for table in TABLE_PRIMARY_KEYS
    }
    dirty_rows_by_table = {
        table: {
            row_dict[TABLE_PRIMARY_KEYS[table]]: row_dict
            for row in dirty[table]
            for row_dict in (row.to_dict(),)
        }
        for table in TABLE_PRIMARY_KEYS
    }
    all_dirty_ids = {
        identifier
        for rows in dirty_rows_by_table.values()
        for identifier in rows
    }
    entity_tables = {
        "ShiftAssignment": "shift_assignments",
        "SwapRequest": "swap_requests",
    }
    anomaly_assignment_ids = set()
    touched_employee_days = set()

    for truth in ground_truth:
        table = entity_tables[truth["entity"]]
        assert truth["record_id"] in dirty_rows_by_table[table]
        assert truth["record_id"] not in clean_primary_keys[table]
        assert all(identifier in all_dirty_ids for identifier in truth["related_record_ids"])
        if truth["entity"] == "ShiftAssignment":
            anomaly_assignment_ids.add(truth["record_id"])

    assignment_by_id = dirty_rows_by_table["shift_assignments"]
    skills_by_employee = defaultdict(list)
    for certification in clean_data["employee_skills"]:
        skills_by_employee[certification.employee_id].append(certification)
    leave_by_id = {
        leave.leave_request_id: leave for leave in clean_data["leave_requests"]
    }
    requests_by_id = {
        request.swap_request_id: request for request in dirty["swap_requests"]
    }
    events_by_request = defaultdict(list)
    for event in dirty["swap_events"]:
        events_by_request[event.swap_request_id].append(event)

    for truth in ground_truth:
        issue_type = truth["issue_type"]
        row = assignment_by_id.get(truth["record_id"])
        if issue_type == "DOUBLE_BOOKED":
            source = assignment_by_id[truth["related_record_ids"][0]]
            assert row["employee_id"] == source["employee_id"]
            assert _parse_utc(row["scheduled_start"]) < _parse_utc(source["scheduled_end"])
            anomaly_assignment_ids.add(row["assignment_id"])
        elif issue_type == "LEAVE_OVERLAP":
            leave = leave_by_id[truth["related_record_ids"][0]]
            assert leave.current_status == "APPROVED"
            shift_date = date.fromisoformat(row["shift_date"])
            assert leave.start_date <= shift_date <= leave.end_date
        elif issue_type == "SKILL_ABSENT":
            certs = skills_by_employee[row["employee_id"]]
            assert not any(
                certification.skill_id == row["fulfills_skill_id"]
                and certification.certified_date
                <= datetime.fromisoformat(row["shift_date"]).date()
                and (
                    certification.expiry_date is None
                    or certification.expiry_date
                    >= datetime.fromisoformat(row["shift_date"]).date()
                )
                for certification in certs
            )
        elif issue_type == "OVERTIME_CAP_EXCEEDED":
            employee_rows = [
                assignment
                for assignment in dirty["shift_assignments"]
                if assignment.assignment_status == "ACTIVE"
                and assignment.employee_id == truth["employee_id"]
                and assignment.shift_date.isocalendar()[:2]
                == datetime.fromisoformat(row["shift_date"]).date().isocalendar()[:2]
            ]
            assert len(employee_rows) == 7
            assert len({assignment.shift_date for assignment in employee_rows}) == 7
            assert sum(
                clean_data["config"].labor_constraints.paid_hours_per_shift
                for _ in employee_rows
            ) == 52.5
            assert "MAX_CONSECUTIVE_DAYS" in truth["also_triggers"]
            anomaly_assignment_ids.update(
                identifier
                for identifier in truth["related_record_ids"]
                if identifier not in clean_primary_keys["shift_assignments"]
            )
        elif issue_type == "SWAP_WITHOUT_APPROVAL":
            request = requests_by_id[truth["record_id"]]
            request_events = events_by_request[request.swap_request_id]
            assert [event.to_status for event in request_events] == [
                "REQUESTED",
                "APPLIED",
            ]
            assert request.current_status == "APPLIED"
            assert all(event.to_status != "APPROVED" for event in request_events)
            anomaly_assignment_ids.update(
                identifier
                for identifier in truth["related_record_ids"]
                if identifier in assignment_by_id
                and identifier not in clean_primary_keys["shift_assignments"]
            )

    expected_rules = {
        "DOUBLE_BOOKED": {"DOUBLE_BOOKED"},
        "LEAVE_OVERLAP": {"LEAVE_OVERLAP"},
        "SKILL_ABSENT": {"SKILL_ABSENT"},
        "OVERTIME_CAP_EXCEEDED": {
            "OVERTIME_CAP_EXCEEDED",
            "MAX_CONSECUTIVE_DAYS",
        },
        "SWAP_WITHOUT_APPROVAL": {"SWAP_WITHOUT_APPROVAL"},
    }
    for truth in ground_truth:
        affected_employees = {truth["employee_id"]}
        if truth["issue_type"] == "SWAP_WITHOUT_APPROVAL":
            request = requests_by_id[truth["record_id"]]
            affected_employees.update(
                {request.requestor_employee_id, request.target_employee_id}
            )
        findings = check_employee_rule_findings(
            affected_employees,
            dirty["shift_assignments"],
            clean_data["employees"],
            clean_data["employee_skills"],
            clean_data["leave_requests"],
            clean_data["config"].horizon.start_date,
            clean_data["config"].horizon.end_date,
            dirty["swap_requests"],
            dirty["swap_events"],
        )
        assert findings == {
            *expected_rules[truth["issue_type"]],
            *truth["also_triggers"],
        }

    for assignment_id in anomaly_assignment_ids:
        assert assignment_id not in clean_primary_keys["shift_assignments"]
        assert assignment_id in assignment_by_id
        assignment = assignment_by_id[assignment_id]
        employee_day = (assignment["employee_id"], assignment["shift_date"])
        assert employee_day not in touched_employee_days
        touched_employee_days.add(employee_day)


def test_dirty_derived_tables_and_attendance_reconcile_from_raw_assignments(
    starter_pipeline_data,
):
    clean = starter_pipeline_data
    dirty, _ = _inject_starter(clean)
    cfg = clean["config"]
    active_assignments = [
        row for row in dirty["shift_assignments"] if row.assignment_status == "ACTIVE"
    ]

    assigned_by_requirement = Counter(
        (
            row.shift_date,
            row.team_id,
            row.shift_template_id,
            row.fulfills_skill_id,
        )
        for row in active_assignments
        if row.fulfills_skill_id
    )
    coverage_gaps = {gap.coverage_id: gap for gap in dirty["coverage_gaps"]}
    assert len(coverage_gaps) == len(dirty["coverage_requirements"])
    for requirement in dirty["coverage_requirements"]:
        key = (
            requirement.effective_date,
            requirement.team_id,
            requirement.shift_template_id,
            requirement.skill_id,
        )
        scheduled = assigned_by_requirement[key]
        gap = coverage_gaps[requirement.coverage_id]
        assert gap.scheduled_headcount == scheduled
        assert gap.gap_headcount == max(0, requirement.min_headcount - scheduled)
        assert gap.target_shortfall == max(0, requirement.target_headcount - scheduled)

    weekly_hours = Counter()
    for assignment in active_assignments:
        iso_year, iso_week, _ = assignment.shift_date.isocalendar()
        weekly_hours[(assignment.employee_id, iso_year, iso_week)] += (
            cfg.labor_constraints.paid_hours_per_shift
        )
    for summary in dirty["weekly_hours_summaries"]:
        key = (summary.employee_id, summary.iso_year, summary.iso_week)
        assert summary.scheduled_hours == weekly_hours[key]
        assert summary.regular_hours == min(summary.contract_hours, weekly_hours[key])
        assert summary.overtime_hours == max(
            0.0, weekly_hours[key] - summary.contract_hours
        )

    active_ids = {row.assignment_id for row in active_assignments}
    assert len({event.attendance_event_id for event in dirty["attendance_events"]}) == len(
        dirty["attendance_events"]
    )
    assert all(event.assignment_id in active_ids for event in dirty["attendance_events"])
    assert all(
        event.event_timestamp.endswith("Z") for event in dirty["attendance_events"]
    )


def test_starter_export_is_deterministic_across_runs_and_timezones(
    starter_pipeline_data, tmp_path
):
    dirty, ground_truth = _inject_starter(starter_pipeline_data)
    clean_tables = {
        name: rows
        for name, rows in starter_pipeline_data.items()
        if isinstance(rows, list)
    }
    first = write_dataset(tmp_path / "first", "starter", clean_tables, dirty, ground_truth)
    second = write_dataset(tmp_path / "second", "starter", clean_tables, dirty, ground_truth)
    assert first["files"] == second["files"]

    script = (
        "import json,sys; from pathlib import Path; "
        "sys.path.insert(0, 'src'); "
        "from run_generator import generate_profile; "
        "generate_profile('starter', Path(sys.argv[1]))"
    )
    manifests = []
    for name, timezone in (("ist", "IST-5:30"), ("utc", "UTC0")):
        output = tmp_path / name
        env = os.environ.copy()
        env["TZ"] = timezone
        subprocess.run(
            [sys.executable, "-c", script, str(output)],
            check=True,
            capture_output=True,
            text=True,
            env=env,
            cwd=Path(__file__).resolve().parents[1],
        )
        manifests.append(
            json.loads(
                (output / "starter" / "manifest.json").read_text(encoding="utf-8")
            )["files"]
        )
    assert manifests[0] == manifests[1]

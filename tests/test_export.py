from __future__ import annotations

import csv
import hashlib
import json

from datetime import date

import pytest

from workforce_generator.export import TABLE_PRIMARY_KEYS, write_dataset
from workforce_generator.models.entities import Employee


def _empty_tables():
    return {table_name: [] for table_name in TABLE_PRIMARY_KEYS}


def test_csv_export_is_sorted_utf8_lf_and_manifest_hashes_files(tmp_path):
    clean = _empty_tables()
    dirty = _empty_tables()
    clean["employees"] = [
        Employee(
            employee_id="EMP-000002",
            team_id="TEAM-001",
            first_name="Zoë",
            last_name="Example",
            email="zoe@example.test",
            role="STAFF",
            weekly_contract_hours=40.0,
            fte=1.0,
            hire_date=date(2026, 1, 1),
            termination_date=None,
            is_active=True,
        ),
        Employee(
            employee_id="EMP-000001",
            team_id="TEAM-001",
            first_name="Ada",
            last_name="Example",
            email="ada@example.test",
            role="STAFF",
            weekly_contract_hours=32.0,
            fte=0.8,
            hire_date=date(2026, 1, 2),
            termination_date=None,
            is_active=False,
        ),
    ]
    dirty["employees"] = list(clean["employees"])
    ground_truth = [
        {
            "anomaly_id": "ANM-0001",
            "issue_type": "DOUBLE_BOOKED",
            "entity": "ShiftAssignment",
            "record_id": "ASN-2026-000001",
            "related_record_ids": ["ASN-2026-000002"],
            "employee_id": "EMP-000001",
            "description": "Injected overlap",
            "also_triggers": "",
        }
    ]

    manifest = write_dataset(tmp_path, "starter", clean, dirty, ground_truth)
    employee_csv = tmp_path / "starter" / "clean" / "employees.csv"
    content = employee_csv.read_bytes()
    assert b"\r" not in content
    assert content.startswith(b"employee_id,team_id,")
    assert content.index(b"EMP-000001") < content.index(b"EMP-000002")
    assert "2026-01-02" in content.decode("utf-8")
    assert ",false\n" in content.decode("utf-8")
    assert ",true\n" in content.decode("utf-8")
    assert "Zoë" in content.decode("utf-8")

    expected_hash = hashlib.sha256(content).hexdigest()
    assert manifest["files"]["clean/employees.csv"] == {
        "row_count": 2,
        "sha256": expected_hash,
    }
    saved_manifest = json.loads(
        (tmp_path / "starter" / "manifest.json").read_text(encoding="utf-8")
    )
    assert saved_manifest == manifest
    assert saved_manifest["row_counts"]["clean"]["employees"] == 2
    assert saved_manifest["row_counts"]["dirty"]["employees"] == 2
    with (tmp_path / "starter" / "ground_truth" / "ground_truth.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        exported_truth = list(csv.DictReader(stream))
    assert exported_truth[0]["related_record_ids"] == '["ASN-2026-000002"]'


def test_export_rejects_missing_or_unknown_tables(tmp_path):
    clean = _empty_tables()
    dirty = _empty_tables()
    del clean["employees"]
    with pytest.raises(ValueError, match="Missing required table: employees"):
        write_dataset(tmp_path, "starter", clean, dirty, [])

    clean = _empty_tables()
    clean["extra"] = []
    with pytest.raises(ValueError, match="Unregistered CSV tables"):
        write_dataset(tmp_path, "starter", clean, dirty, [])

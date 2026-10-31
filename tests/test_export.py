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


def test_identical_seed_produces_byte_identical_csvs(tmp_path):
    from run_generator import generate_profile

    dir_a = tmp_path / 'run_a'
    dir_b = tmp_path / 'run_b'

    generate_profile('starter', dir_a)
    generate_profile('starter', dir_b)

    files_a = sorted((dir_a / 'starter').rglob('*.csv'))
    files_b = sorted((dir_b / 'starter').rglob('*.csv'))
    assert len(files_a) > 0
    assert len(files_a) == len(files_b)

    for fa, fb in zip(files_a, files_b):
        rel_a = fa.relative_to(dir_a / 'starter').as_posix()
        rel_b = fb.relative_to(dir_b / 'starter').as_posix()
        assert rel_a == rel_b
        hash_a = hashlib.sha256(fa.read_bytes()).hexdigest()
        hash_b = hashlib.sha256(fb.read_bytes()).hexdigest()
        assert hash_a == hash_b, f'Hash mismatch for {rel_a}: {hash_a} != {hash_b}'
def test_json_export_row_counts_match_csvs(tmp_path):
    """Each exported JSON file must match its companion CSV in row count."""
    from run_generator import generate_profile

    generate_profile("starter", tmp_path)
    manifest_path = tmp_path / "starter" / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest["files"]

    csv_files = [f for f in files if f.endswith(".csv")]
    assert len(csv_files) == 31  # 15 clean + 15 dirty + 1 ground_truth
    for cf in csv_files:
        jf = cf[:-4] + ".json"
        assert jf in files, f"Manifest missing companion JSON for {cf}"
        json_file_path = tmp_path / "starter" / jf
        assert json_file_path.exists(), f"File does not exist: {jf}"
        json_data = json.loads(json_file_path.read_text(encoding="utf-8"))
        assert len(json_data) == files[cf]["row_count"], f"Row count mismatch for {jf}"
        assert files[jf]["row_count"] == files[cf]["row_count"]


def test_sql_export_row_counts_match_csvs(tmp_path):
    """Load schema.sql + starter_inserts.sql into sqlite3 and verify row counts match CSVs."""
    import csv
    import sqlite3
    import subprocess
    import sys

    from run_generator import generate_profile

    # Generate starter dataset into tmp_path
    generate_profile("starter", tmp_path)
    clean_dir = tmp_path / "starter" / "clean"

    # Run generate_sql.py against the tmp output
    result = subprocess.run(
        [sys.executable, "scripts/generate_sql.py", "--out", str(tmp_path / "starter")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    schema_path = tmp_path / "starter" / "schema.sql"
    inserts_path = tmp_path / "starter" / "starter_inserts.sql"
    assert schema_path.exists()
    assert inserts_path.exists()

    # Load into in-memory SQLite
    con = sqlite3.connect(":memory:")
    con.executescript(schema_path.read_text(encoding="utf-8"))
    con.executescript(inserts_path.read_text(encoding="utf-8"))

    # Compare row counts against CSVs
    tables = [
        "skills", "teams", "holidays", "shift_templates", "employees",
        "employee_skills", "coverage_requirements", "shift_assignments",
        "leave_requests", "leave_events", "swap_requests", "swap_events",
        "attendance_events", "coverage_gaps", "weekly_hours_summaries",
    ]
    for table in tables:
        csv_path = clean_dir / f"{table}.csv"
        if not csv_path.exists():
            continue
        csv_rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8").splitlines()))
        (db_count,) = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        assert db_count == len(csv_rows), (
            f"{table}: sqlite has {db_count} rows, CSV has {len(csv_rows)}"
        )
    con.close()

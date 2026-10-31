"""
Generate schema.sql (CREATE TABLE + FK definitions) and starter_inserts.sql
(INSERT statements for starter dataset clean tables) into output/starter/.

Usage:
    python scripts/generate_sql.py [--out output/starter]
"""
import argparse
import csv
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------
# Each entry: (table_name, column_defs, primary_key, [foreign_keys])
# Column defs use SQLite-compatible types.
_SCHEMA = [
    (
        "skills",
        [
            "skill_id    TEXT NOT NULL",
            "code        TEXT NOT NULL",
            "name        TEXT NOT NULL",
            "category    TEXT NOT NULL",
            "description TEXT",
        ],
        "skill_id",
        [],
    ),
    (
        "teams",
        [
            "team_id    TEXT NOT NULL",
            "name       TEXT NOT NULL",
            "department TEXT NOT NULL",
            "timezone   TEXT NOT NULL",
            "is_active  INTEGER NOT NULL",
        ],
        "team_id",
        [],
    ),
    (
        "holidays",
        [
            "holiday_id    TEXT NOT NULL",
            "calendar_date TEXT NOT NULL",
            "name          TEXT NOT NULL",
            "jurisdiction  TEXT NOT NULL",
            "is_statutory  INTEGER NOT NULL",
        ],
        "holiday_id",
        [],
    ),
    (
        "shift_templates",
        [
            "shift_template_id    TEXT NOT NULL",
            "template_code        TEXT NOT NULL",
            "name                 TEXT NOT NULL",
            "start_time           TEXT NOT NULL",
            "end_time             TEXT NOT NULL",
            "duration_hours       REAL NOT NULL",
            "unpaid_break_minutes INTEGER NOT NULL",
            "is_overnight         INTEGER NOT NULL",
        ],
        "shift_template_id",
        [],
    ),
    (
        "employees",
        [
            "employee_id           TEXT NOT NULL",
            "team_id               TEXT NOT NULL",
            "first_name            TEXT NOT NULL",
            "last_name             TEXT NOT NULL",
            "email                 TEXT NOT NULL",
            "role                  TEXT NOT NULL",
            "weekly_contract_hours REAL NOT NULL",
            "fte                   REAL NOT NULL",
            "hire_date             TEXT NOT NULL",
            "termination_date      TEXT",
            "is_active             INTEGER NOT NULL",
        ],
        "employee_id",
        [("team_id", "teams", "team_id")],
    ),
    (
        "employee_skills",
        [
            "employee_skill_id TEXT NOT NULL",
            "employee_id       TEXT NOT NULL",
            "skill_id          TEXT NOT NULL",
            "proficiency_level TEXT NOT NULL",
            "certified_date    TEXT NOT NULL",
            "expiry_date       TEXT",
            "is_primary        INTEGER NOT NULL",
        ],
        "employee_skill_id",
        [
            ("employee_id", "employees", "employee_id"),
            ("skill_id", "skills", "skill_id"),
        ],
    ),
    (
        "coverage_requirements",
        [
            "coverage_id       TEXT NOT NULL",
            "effective_date    TEXT NOT NULL",
            "team_id           TEXT NOT NULL",
            "shift_template_id TEXT NOT NULL",
            "skill_id          TEXT NOT NULL",
            "min_headcount     INTEGER NOT NULL",
            "target_headcount  INTEGER NOT NULL",
        ],
        "coverage_id",
        [
            ("team_id", "teams", "team_id"),
            ("shift_template_id", "shift_templates", "shift_template_id"),
            ("skill_id", "skills", "skill_id"),
        ],
    ),
    (
        "shift_assignments",
        [
            "assignment_id       TEXT NOT NULL",
            "shift_instance_id   TEXT NOT NULL",
            "employee_id         TEXT NOT NULL",
            "team_id             TEXT NOT NULL",
            "shift_template_id   TEXT NOT NULL",
            "shift_date          TEXT NOT NULL",
            "scheduled_start     TEXT NOT NULL",
            "scheduled_end       TEXT NOT NULL",
            "duration_hours      REAL NOT NULL",
            "assigned_role       TEXT NOT NULL",
            "assignment_status   TEXT NOT NULL",
            "effective_from      TEXT NOT NULL",
            "effective_to        TEXT",
            "swap_request_id     TEXT",
            "replaces_assignment_id TEXT",
            "fulfills_skill_id   TEXT",
            "is_holiday_premium  INTEGER NOT NULL",
        ],
        "assignment_id",
        [
            ("employee_id", "employees", "employee_id"),
            ("team_id", "teams", "team_id"),
            ("shift_template_id", "shift_templates", "shift_template_id"),
        ],
    ),
    (
        "leave_requests",
        [
            "leave_request_id TEXT NOT NULL",
            "employee_id      TEXT NOT NULL",
            "leave_type       TEXT NOT NULL",
            "start_date       TEXT NOT NULL",
            "end_date         TEXT NOT NULL",
            "requested_hours  REAL NOT NULL",
            "current_status   TEXT NOT NULL",
            "submitted_at     TEXT NOT NULL",
            "decided_at       TEXT",
            "decision_reason  TEXT",
        ],
        "leave_request_id",
        [("employee_id", "employees", "employee_id")],
    ),
    (
        "leave_events",
        [
            "leave_event_id   TEXT NOT NULL",
            "leave_request_id TEXT NOT NULL",
            "from_status      TEXT",          # NULL on initial submission
            "to_status        TEXT NOT NULL",
            "event_timestamp  TEXT NOT NULL",
            "actor_type       TEXT NOT NULL",
            "actor_id         TEXT",          # NULL for SYSTEM-generated events
            "reason           TEXT",
        ],
        "leave_event_id",
        [("leave_request_id", "leave_requests", "leave_request_id")],
    ),
    (
        "swap_requests",
        [
            "swap_request_id         TEXT NOT NULL",
            "requestor_employee_id   TEXT NOT NULL",
            "requestor_assignment_id TEXT NOT NULL",
            "target_employee_id      TEXT NOT NULL",
            "target_assignment_id    TEXT NOT NULL",
            "current_status          TEXT NOT NULL",
            "manager_employee_id     TEXT",
            "requested_at            TEXT NOT NULL",
            "decided_at              TEXT",
            "applied_at              TEXT",
            "rejection_reason        TEXT",
        ],
        "swap_request_id",
        [
            ("requestor_employee_id", "employees", "employee_id"),
            ("target_employee_id", "employees", "employee_id"),
            ("requestor_assignment_id", "shift_assignments", "assignment_id"),
            ("target_assignment_id", "shift_assignments", "assignment_id"),
        ],
    ),
    (
        "swap_events",
        [
            "swap_event_id    TEXT NOT NULL",
            "swap_request_id  TEXT NOT NULL",
            "from_status      TEXT",          # NULL on initial request
            "to_status        TEXT NOT NULL",
            "event_timestamp  TEXT NOT NULL",
            "actor_type       TEXT NOT NULL",
            "actor_id         TEXT",          # NULL for SYSTEM-generated events
            "notes            TEXT",
        ],
        "swap_event_id",
        [("swap_request_id", "swap_requests", "swap_request_id")],
    ),
    (
        "attendance_events",
        [
            "attendance_event_id TEXT NOT NULL",
            "employee_id         TEXT NOT NULL",
            "assignment_id       TEXT NOT NULL",
            "event_type          TEXT NOT NULL",
            "event_timestamp     TEXT NOT NULL",
            "source              TEXT NOT NULL",
            "variance_minutes    REAL",
            "worked_minutes      REAL",
            "is_exception        INTEGER NOT NULL",
            "exception_type      TEXT",
        ],
        "attendance_event_id",
        [
            ("employee_id", "employees", "employee_id"),
            ("assignment_id", "shift_assignments", "assignment_id"),
        ],
    ),
    (
        "coverage_gaps",
        [
            "gap_id              TEXT NOT NULL",
            "coverage_id         TEXT NOT NULL",
            "effective_date      TEXT NOT NULL",
            "team_id             TEXT NOT NULL",
            "shift_template_id   TEXT NOT NULL",
            "skill_id            TEXT NOT NULL",
            "required_headcount  INTEGER NOT NULL",
            "scheduled_headcount INTEGER NOT NULL",
            "gap_headcount       INTEGER NOT NULL",
            "target_shortfall    INTEGER NOT NULL",
            "reason              TEXT NOT NULL",
            "is_critical         INTEGER NOT NULL",
            "has_gap             INTEGER NOT NULL",
            "computed_at         TEXT NOT NULL",
        ],
        "gap_id",
        [
            ("coverage_id", "coverage_requirements", "coverage_id"),
            ("team_id", "teams", "team_id"),
            ("shift_template_id", "shift_templates", "shift_template_id"),
            ("skill_id", "skills", "skill_id"),
        ],
    ),
    (
        "weekly_hours_summaries",
        [
            "summary_id          TEXT NOT NULL",
            "employee_id         TEXT NOT NULL",
            "iso_year            INTEGER NOT NULL",
            "iso_week            INTEGER NOT NULL",
            "is_partial_week     INTEGER NOT NULL",
            "contract_hours      REAL NOT NULL",
            "scheduled_hours     REAL NOT NULL",
            "regular_hours       REAL NOT NULL",
            "overtime_hours      REAL NOT NULL",
            "exceeds_overtime_cap INTEGER NOT NULL",
            "computed_at         TEXT NOT NULL",
        ],
        "summary_id",
        [("employee_id", "employees", "employee_id")],
    ),
]


def _escape(v: str) -> str:
    """SQL-escape a string value (single-quote doubling)."""
    return v.replace("'", "''")


def _sql_value(v: str) -> str:
    """Return SQL literal for a CSV string value."""
    if v == "":
        return "NULL"
    low = v.lower()
    if low in ("true", "1"):
        return "1"
    if low in ("false", "0"):
        return "0"
    return f"'{_escape(v)}'"


def build_schema_sql() -> str:
    lines: list[str] = [
        "-- Generated by scripts/generate_sql.py",
        "-- SQLite-compatible DDL",
        "PRAGMA foreign_keys = ON;",
        "",
    ]
    for table, cols, pk, fks in _SCHEMA:
        lines.append(f"CREATE TABLE IF NOT EXISTS {table} (")
        all_constraints = list(cols)
        all_constraints.append(f"PRIMARY KEY ({pk})")
        for (col, ref_table, ref_col) in fks:
            all_constraints.append(
                f"FOREIGN KEY ({col}) REFERENCES {ref_table} ({ref_col})"
            )
        for i, c in enumerate(all_constraints):
            comma = "," if i < len(all_constraints) - 1 else ""
            lines.append(f"    {c}{comma}")
        lines.append(");")
        lines.append("")
    return "\n".join(lines)


def build_inserts_sql(clean_dir: Path) -> str:
    """Generate INSERT statements for all tables from clean starter CSVs."""
    lines: list[str] = [
        "-- Generated by scripts/generate_sql.py",
        "-- Starter dataset (clean) INSERT statements",
        "BEGIN TRANSACTION;",
        "",
    ]
    table_names = [t[0] for t in _SCHEMA]
    for table in table_names:
        csv_path = clean_dir / f"{table}.csv"
        if not csv_path.exists():
            continue
        rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8").splitlines()))
        if not rows:
            continue
        cols = list(rows[0].keys())
        col_list = ", ".join(cols)
        lines.append(f"-- {table}: {len(rows)} rows")
        for row in rows:
            vals = ", ".join(_sql_value(row[c]) for c in cols)
            lines.append(f"INSERT INTO {table} ({col_list}) VALUES ({vals});")
        lines.append("")
    lines.append("COMMIT;")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate SQL schema and starter inserts.")
    parser.add_argument("--out", default="output/starter", help="Output directory (default: output/starter)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    clean_dir = out_dir / "clean"

    schema_path = out_dir / "schema.sql"
    inserts_path = out_dir / "starter_inserts.sql"

    schema_sql = build_schema_sql()
    schema_path.write_text(schema_sql, encoding="utf-8")
    print(f"Written: {schema_path}")

    inserts_sql = build_inserts_sql(clean_dir)
    inserts_path.write_text(inserts_sql, encoding="utf-8")
    print(f"Written: {inserts_path}")

    # Count rows per INSERT block for verification
    table_names = [t[0] for t in _SCHEMA]
    for table in table_names:
        csv_path = clean_dir / f"{table}.csv"
        if csv_path.exists():
            n = sum(1 for _ in csv.reader(csv_path.read_text(encoding="utf-8").splitlines())) - 1
            print(f"  {table}: {n} rows")


if __name__ == "__main__":
    main()

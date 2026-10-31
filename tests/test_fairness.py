"""Tests for scripts/report_fairness.py using a tiny hand-built dataset."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Make scripts/ importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from report_fairness import compute_metrics, gini


# ---------------------------------------------------------------------------
# Fixtures – hand-built CSVs in a temp directory
# ---------------------------------------------------------------------------

EMPLOYEES_CSV = textwrap.dedent("""\
    employee_id,team_id,first_name,last_name,email,role,weekly_contract_hours,fte,hire_date,termination_date,is_active
    EMP-001,TEAM-A,Alice,Smith,alice@workforce.example.com,STAFF,40.0,1.0,2023-01-01,,true
    EMP-002,TEAM-A,Bob,Jones,bob@workforce.example.com,LEAD,40.0,1.0,2023-01-01,,true
    EMP-003,TEAM-B,Carol,Lee,carol@workforce.example.com,STAFF,40.0,1.0,2023-01-01,,true
    EMP-004,TEAM-B,Dave,Kim,dave@workforce.example.com,MANAGER,40.0,1.0,2023-01-01,,true
""")

TEMPLATES_CSV = textwrap.dedent("""\
    shift_template_id,template_code,name,start_time,end_time,duration_hours,unpaid_break_minutes,is_overnight
    SFT-A,A,Morning,06:00:00,14:00:00,8.0,30,false
    SFT-B,B,Afternoon,14:00:00,22:00:00,8.0,30,false
    SFT-C,C,Night,22:00:00,06:00:00,8.0,30,true
""")

# Assignments:
#   EMP-001 (TEAM-A, non-mgr): 3 shifts — Mon morning, Sat morning, Mon night
#   EMP-002 (TEAM-A, non-mgr): 1 shift — Wed afternoon
#   EMP-003 (TEAM-B, non-mgr): 2 shifts — Sun morning, Tue morning
#   EMP-004 (TEAM-B, MANAGER): 1 shift — Mon morning  (excluded)
ASSIGNMENTS_CSV = textwrap.dedent("""\
    assignment_id,shift_instance_id,employee_id,team_id,shift_template_id,shift_date,scheduled_start,scheduled_end,duration_hours,assigned_role,assignment_status,effective_from,effective_to,swap_request_id,replaces_assignment_id,fulfills_skill_id,is_holiday_premium
    ASN-001,SI-001,EMP-001,TEAM-A,SFT-A,2026-01-05,2026-01-05T06:00:00Z,2026-01-05T14:00:00Z,8.0,STAFF,ACTIVE,2026-01-05T00:00:00Z,,,,SKL-X,false
    ASN-002,SI-002,EMP-001,TEAM-A,SFT-A,2026-01-10,2026-01-10T06:00:00Z,2026-01-10T14:00:00Z,8.0,STAFF,ACTIVE,2026-01-10T00:00:00Z,,,,SKL-X,false
    ASN-003,SI-003,EMP-001,TEAM-A,SFT-C,2026-01-05,2026-01-05T22:00:00Z,2026-01-06T06:00:00Z,8.0,STAFF,ACTIVE,2026-01-05T00:00:00Z,,,,SKL-X,false
    ASN-004,SI-004,EMP-002,TEAM-A,SFT-B,2026-01-07,2026-01-07T14:00:00Z,2026-01-07T22:00:00Z,8.0,LEAD,ACTIVE,2026-01-07T00:00:00Z,,,,SKL-X,false
    ASN-005,SI-005,EMP-003,TEAM-B,SFT-A,2026-01-11,2026-01-11T06:00:00Z,2026-01-11T14:00:00Z,8.0,STAFF,ACTIVE,2026-01-11T00:00:00Z,,,,SKL-Y,false
    ASN-006,SI-006,EMP-003,TEAM-B,SFT-A,2026-01-13,2026-01-13T06:00:00Z,2026-01-13T14:00:00Z,8.0,STAFF,ACTIVE,2026-01-13T00:00:00Z,,,,SKL-Y,false
    ASN-007,SI-007,EMP-004,TEAM-B,SFT-A,2026-01-05,2026-01-05T06:00:00Z,2026-01-05T14:00:00Z,8.0,MANAGER,ACTIVE,2026-01-05T00:00:00Z,,,,SKL-Y,false
    ASN-008,SI-008,EMP-001,TEAM-A,SFT-B,2026-01-05,2026-01-05T14:00:00Z,2026-01-05T22:00:00Z,8.0,STAFF,CANCELLED,2026-01-05T00:00:00Z,,,,SKL-X,false
""")
# Dates used:
#   2026-01-05 = Monday (dayofweek=0)
#   2026-01-07 = Wednesday (dayofweek=2)
#   2026-01-10 = Saturday (dayofweek=5)  <- weekend
#   2026-01-11 = Sunday (dayofweek=6)    <- weekend
#   2026-01-13 = Tuesday (dayofweek=1)
#
# Expected per-employee results (ACTIVE non-manager only):
#   EMP-001: shifts=3 (ASN-001,002,003), hours=24.0, nights=1, weekends=1 (ASN-002 is Sat)
#   EMP-002: shifts=1 (ASN-004), hours=8.0, nights=0, weekends=0
#   EMP-003: shifts=2 (ASN-005,006), hours=16.0, nights=0, weekends=1 (ASN-005 is Sun)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "employees.csv").write_text(EMPLOYEES_CSV, encoding="utf-8")
    (tmp_path / "shift_templates.csv").write_text(TEMPLATES_CSV, encoding="utf-8")
    (tmp_path / "shift_assignments.csv").write_text(ASSIGNMENTS_CSV, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_manager_excluded(data_dir: Path) -> None:
    per_emp = compute_metrics(data_dir)
    assert "EMP-004" not in per_emp["employee_id"].values, "Manager must be excluded"


def test_cancelled_excluded(data_dir: Path) -> None:
    per_emp = compute_metrics(data_dir)
    # EMP-001 has 3 ACTIVE + 1 CANCELLED; shift_count must be 3
    row = per_emp[per_emp["employee_id"] == "EMP-001"].iloc[0]
    assert row["shift_count"] == 3, f"Expected 3 active shifts for EMP-001, got {row['shift_count']}"


def test_employee_metrics(data_dir: Path) -> None:
    per_emp = compute_metrics(data_dir)
    per_emp = per_emp.set_index("employee_id")

    # EMP-001: 3 shifts, 24h, 1 night, 1 weekend (2026-01-10 = Sat)
    r1 = per_emp.loc["EMP-001"]
    assert r1["shift_count"] == 3
    assert r1["paid_hours"] == pytest.approx(24.0)
    assert r1["night_shifts"] == 1
    assert r1["weekend_shifts"] == 1

    # EMP-002: 1 shift, 8h, 0 night, 0 weekend
    r2 = per_emp.loc["EMP-002"]
    assert r2["shift_count"] == 1
    assert r2["paid_hours"] == pytest.approx(8.0)
    assert r2["night_shifts"] == 0
    assert r2["weekend_shifts"] == 0

    # EMP-003: 2 shifts, 16h, 0 night, 1 weekend (2026-01-11 = Sun)
    r3 = per_emp.loc["EMP-003"]
    assert r3["shift_count"] == 2
    assert r3["paid_hours"] == pytest.approx(16.0)
    assert r3["night_shifts"] == 0
    assert r3["weekend_shifts"] == 1


def test_gini_equal_distribution() -> None:
    # Equal values -> Gini = 0
    assert gini(np.array([5.0, 5.0, 5.0])) == pytest.approx(0.0)


def test_gini_max_inequality() -> None:
    # [0, 0, 3]: mean=1, n=3, sum|xi-xj|=12, Gini=12/(2*3^2*1)=2/3
    arr = np.array([0.0, 0.0, 3.0])
    assert gini(arr) == pytest.approx(12 / (2 * 9 * 1.0))


def test_gini_known_value() -> None:
    # [1, 2, 3]: mean=2, n=3, sum|xi-xj|=2*(1+2+1)=8, Gini=8/(2*9*2)=2/9
    arr = np.array([1.0, 2.0, 3.0])
    assert gini(arr) == pytest.approx(8 / (2 * 9 * 2.0))


def test_three_employees_overall_means(data_dir: Path) -> None:
    per_emp = compute_metrics(data_dir)
    assert len(per_emp) == 3  # EMP-001, EMP-002, EMP-003
    assert per_emp["shift_count"].sum() == 6
    assert per_emp["paid_hours"].sum() == pytest.approx(48.0)
    assert per_emp["night_shifts"].sum() == 1
    assert per_emp["weekend_shifts"].sum() == 2

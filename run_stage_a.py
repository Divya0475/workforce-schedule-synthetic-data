"""Stage A Execution Script: Generates organizational dimensions, holidays, and coverage demand."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List
import numpy as np
import pandas as pd

# Ensure src is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from workforce_generator.config import load_config
from workforce_generator.pipeline import run_pipeline


def run_stage_a() -> Dict[str, pd.DataFrame]:
    # Starter profile configuration overrides
    starter_overrides = {
        "seed": 20261022,
        "workforce": {
            "num_teams": 5,
            "employees_per_team": 10,
        },
        "horizon": {
            "start_date": "2026-01-01",
            "end_date": "2026-01-14",
        },
    }

    config = load_config(overrides=starter_overrides)

    print("=" * 80)
    print("STAGE A: WORKFORCE SYNTHETIC DATA GENERATOR (STARTER PROFILE)")
    print(f"Seed: {config.seed}")
    print(f"Workforce: {config.workforce.num_teams} teams x {config.workforce.employees_per_team} employees/team = {config.workforce.total_employees} employees")
    print(f"Planning Horizon: {config.horizon.start_date} to {config.horizon.end_date} ({config.horizon.total_days} days)")
    print("=" * 80)

    # Run pipeline
    data = run_pipeline(config)

    # Convert to DataFrames
    dfs = {
        "Team": pd.DataFrame([t.to_dict() for t in data["teams"]]),
        "Skill": pd.DataFrame([s.to_dict() for s in data["skills"]]),
        "Employee": pd.DataFrame([e.to_dict() for e in data["employees"]]),
        "EmployeeSkill": pd.DataFrame([es.to_dict() for es in data["employee_skills"]]),
        "ShiftTemplate": pd.DataFrame([st.to_dict() for st in data["shift_templates"]]),
        "HolidayCalendar": pd.DataFrame([h.to_dict() for h in data["holidays"]]),
        "CoverageRequirement": pd.DataFrame([c.to_dict() for c in data["coverage_requirements"]]),
    }

    print("\nROW COUNTS SUMMARY:")
    print("-" * 40)
    for name, df in dfs.items():
        print(f"  * {name:<22}: {len(df):>5} rows")
    print("-" * 40)

    print("\nSAMPLE ROWS (5 FROM EACH TABLE):")
    for name, df in dfs.items():
        print("\n" + "=" * 80)
        print(f"TABLE: {name} (Total: {len(df)} rows)")
        print("=" * 80)
        sample = df.head(min(5, len(df)))
        print(sample.to_string(index=False))

    # Export to output/stage_a/ as CSV
    out_dir = Path(__file__).resolve().parent / "output" / "stage_a"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("\n" + "=" * 80)
    print(f"EXPORTING STAGE A TABLES TO CSV: {out_dir}")
    print("=" * 80)
    for name, df in dfs.items():
        csv_path = out_dir / f"{name}.csv"
        df.to_csv(csv_path, index=False)
        print(f"  -> Exported {name:<22} to {csv_path.name} ({len(df)} rows)")

    return dfs


if __name__ == "__main__":
    run_stage_a()

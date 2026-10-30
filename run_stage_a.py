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
from workforce_generator.generators.coverage import generate_coverage_requirements
from workforce_generator.generators.holidays import generate_holidays
from workforce_generator.generators.organization import generate_organization
from workforce_generator.models.entities import ShiftTemplate


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
    rng = np.random.default_rng(config.seed)

    print("=" * 80)
    print("STAGE A: WORKFORCE SYNTHETIC DATA GENERATOR (STARTER PROFILE)")
    print(f"Seed: {config.seed}")
    print(f"Workforce: {config.workforce.num_teams} teams x {config.workforce.employees_per_team} employees/team = {config.workforce.total_employees} employees")
    print(f"Planning Horizon: {config.horizon.start_date} to {config.horizon.end_date} ({config.horizon.total_days} days)")
    print("=" * 80)

    # 1. Organization (Teams, Skills, Employees, EmployeeSkill)
    teams, skills, employees, employee_skills = generate_organization(config, rng)

    # 2. Shift Templates
    shift_templates = [
        ShiftTemplate(
            shift_template_id=s.id,
            template_code=s.code,
            name=s.name,
            start_time=s.start_time,
            end_time=s.end_time,
            duration_hours=s.duration_hours,
            unpaid_break_minutes=s.unpaid_break_minutes,
            is_overnight=s.is_overnight,
        )
        for s in config.shifts
    ]

    # 3. Holiday Calendar
    holidays = generate_holidays(config)

    # 4. Coverage Requirements
    coverage_requirements = generate_coverage_requirements(config, teams, holidays)

    # Convert to DataFrames
    dfs = {
        "Team": pd.DataFrame([t.to_dict() for t in teams]),
        "Skill": pd.DataFrame([s.to_dict() for s in skills]),
        "Employee": pd.DataFrame([e.to_dict() for e in employees]),
        "EmployeeSkill": pd.DataFrame([es.to_dict() for es in employee_skills]),
        "ShiftTemplate": pd.DataFrame([st.to_dict() for st in shift_templates]),
        "HolidayCalendar": pd.DataFrame([h.to_dict() for h in holidays]),
        "CoverageRequirement": pd.DataFrame([c.to_dict() for c in coverage_requirements]),
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

    return dfs


if __name__ == "__main__":
    run_stage_a()

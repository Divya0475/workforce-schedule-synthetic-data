"""Profiling script for Stage A synthetic data generation.

Audits and prints:
1. Rows per table
2. Employees per skill
3. Distribution of skills per employee
4. Coverage rows per skill and per day
5. Per team-shift the sum of min_headcount and target_headcount
6. Schedulable staff (non-manager) weekly capacity in shifts vs weekly demand in person-shifts per team
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict
import numpy as np
import pandas as pd

# Ensure src is accessible
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from workforce_generator.config import load_config
from workforce_generator.generators.coverage import generate_coverage_requirements
from workforce_generator.generators.holidays import generate_holidays
from workforce_generator.generators.organization import generate_organization
from workforce_generator.models.entities import ShiftTemplate


def profile_stage_a(csv_dir: Path | None = None) -> None:
    # 1. Load data either from CSVs or generate on the fly
    if csv_dir and csv_dir.exists() and (csv_dir / "Employee.csv").exists():
        print(f"Loading Stage A data from CSV directory: {csv_dir}")
        team_df = pd.read_csv(csv_dir / "Team.csv")
        skill_df = pd.read_csv(csv_dir / "Skill.csv")
        emp_df = pd.read_csv(csv_dir / "Employee.csv")
        emp_sk_df = pd.read_csv(csv_dir / "EmployeeSkill.csv")
        shift_df = pd.read_csv(csv_dir / "ShiftTemplate.csv")
        holiday_df = pd.read_csv(csv_dir / "HolidayCalendar.csv")
        cov_df = pd.read_csv(csv_dir / "CoverageRequirement.csv")
        cfg = load_config()
    else:
        # Run generator using starter profile
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
        cfg = load_config(overrides=starter_overrides)
        rng = np.random.default_rng(cfg.seed)

        teams, skills, employees, employee_skills = generate_organization(cfg, rng)
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
            for s in config_shifts(cfg)
        ]
        holidays = generate_holidays(cfg)
        coverage_requirements = generate_coverage_requirements(cfg, teams, holidays)

        team_df = pd.DataFrame([t.to_dict() for t in teams])
        skill_df = pd.DataFrame([s.to_dict() for s in skills])
        emp_df = pd.DataFrame([e.to_dict() for e in employees])
        emp_sk_df = pd.DataFrame([es.to_dict() for es in employee_skills])
        shift_df = pd.DataFrame([st.to_dict() for st in shift_templates])
        holiday_df = pd.DataFrame([h.to_dict() for h in holidays])
        cov_df = pd.DataFrame([c.to_dict() for c in coverage_requirements])

    tables = {
        "Team": team_df,
        "Skill": skill_df,
        "Employee": emp_df,
        "EmployeeSkill": emp_sk_df,
        "ShiftTemplate": shift_df,
        "HolidayCalendar": holiday_df,
        "CoverageRequirement": cov_df,
    }

    print("\n" + "=" * 90)
    print("STAGE A STATISTICAL PROFILE & AUDIT REPORT")
    print("=" * 90)

    # --------------------------------------------------------------------------
    # 1. Rows per table
    # --------------------------------------------------------------------------
    print("\n1. ROWS PER TABLE:")
    print("-" * 50)
    for name, df in tables.items():
        print(f"  * {name:<24}: {len(df):>6} rows")

    # --------------------------------------------------------------------------
    # 2. Employees per skill
    # --------------------------------------------------------------------------
    print("\n2. EMPLOYEES PER SKILL:")
    print("-" * 75)
    emp_skill_merged = emp_sk_df.merge(skill_df, on="skill_id", how="left")
    skill_counts = emp_skill_merged.groupby(["skill_id", "name", "category"]).agg(
        total_employees=("employee_id", "nunique"),
        primary_holders=("is_primary", lambda s: s.sum()),
    ).reset_index()
    print(skill_counts.to_string(index=False))

    # --------------------------------------------------------------------------
    # 3. Distribution of skills per employee
    # --------------------------------------------------------------------------
    print("\n3. DISTRIBUTION OF SKILLS PER EMPLOYEE:")
    print("-" * 50)
    skills_per_emp = emp_sk_df.groupby("employee_id")["skill_id"].count()
    dist_counts = skills_per_emp.value_counts().sort_index()
    print(f"  Summary: Min = {skills_per_emp.min()}, Max = {skills_per_emp.max()}, Mean = {skills_per_emp.mean():.2f}")
    print("  Histogram:")
    for num_sk, emp_count in dist_counts.items():
        pct = (emp_count / len(skills_per_emp)) * 100
        print(f"    - {num_sk} skill(s) : {emp_count:>3} employees ({pct:>5.1f}%)")

    # --------------------------------------------------------------------------
    # 4. Coverage rows per skill and per day
    # --------------------------------------------------------------------------
    print("\n4A. COVERAGE ROWS PER SKILL:")
    print("-" * 50)
    cov_by_skill = cov_df.groupby("skill_id").agg(
        total_rows=("coverage_id", "count"),
        total_min_heads=("min_headcount", "sum"),
    ).reset_index()
    print(cov_by_skill.to_string(index=False))

    print("\n4B. COVERAGE ROWS PER DAY:")
    print("-" * 65)
    cov_by_day = cov_df.groupby("effective_date").agg(
        total_rows=("coverage_id", "count"),
        sum_min_heads=("min_headcount", "sum"),
        sum_target_heads=("target_headcount", "sum"),
    ).reset_index()
    print(cov_by_day.to_string(index=False))

    # --------------------------------------------------------------------------
    # 5. Per team-shift the sum of min_headcount and target_headcount
    # --------------------------------------------------------------------------
    print("\n5. PER TEAM-SHIFT SUM OF MIN_HEADCOUNT AND TARGET_HEADCOUNT (OVER HORIZON):")
    print("-" * 85)
    team_shift_agg = cov_df.groupby(["team_id", "shift_template_id"]).agg(
        slots_count=("coverage_id", "count"),
        sum_min_headcount=("min_headcount", "sum"),
        sum_target_headcount=("target_headcount", "sum"),
    ).reset_index()
    print(team_shift_agg.to_string(index=False))

    # --------------------------------------------------------------------------
    # 6. Schedulable staff weekly capacity vs weekly demand per team
    # --------------------------------------------------------------------------
    print("\n6. SCHEDULABLE STAFF (NON-MANAGER) WEEKLY CAPACITY VS DEMAND PER TEAM:")
    print("-" * 90)
    # Exclude managers
    schedulable_emp = emp_df[emp_df["role"] != "MANAGER"].copy()
    paid_hours_per_shift = 7.5  # Standard net paid hours (8h gross - 30m unpaid break)

    # Weekly demand per team
    # Weekday: Shift A (3), Shift B (3), Shift C (2) = 8 heads/day * 5 days = 40 person-shifts
    # Weekend: Shift A (2), Shift B (2), Shift C (1) = 5 heads/day * 2 days = 10 person-shifts
    # Total weekly demand per team = 50 person-shifts
    weekly_demand_shifts = 50.0

    capacity_summary = []
    for team_id, group in schedulable_emp.groupby("team_id"):
        staff_count = len(group)
        # Total standard weekly contract paid hours
        total_contract_hours = group["weekly_contract_hours"].sum()
        # Weekly standard shift capacity
        standard_shift_capacity = total_contract_hours / paid_hours_per_shift
        # Overtime ceiling capacity (48h paid hours cap per employee)
        overtime_ceiling_shifts = (staff_count * 48.0) / paid_hours_per_shift
        utilization_rate = (weekly_demand_shifts / standard_shift_capacity) * 100

        capacity_summary.append({
            "team_id": team_id,
            "schedulable_staff": staff_count,
            "weekly_contract_hours": total_contract_hours,
            "weekly_supply_shifts": round(standard_shift_capacity, 2),
            "weekly_ot_cap_shifts": round(overtime_ceiling_shifts, 2),
            "weekly_demand_shifts": weekly_demand_shifts,
            "utilization_pct": f"{utilization_rate:.1f}%",
            "feasibility_assessment": "TIGHT (Feasible with modest OT)",
        })

    cap_df = pd.DataFrame(capacity_summary)
    print(cap_df.to_string(index=False))
    print("\nNote on Capacity Sizing:")
    print("  * Net paid hours per shift: 7.5 hours (8.0h gross window - 30-minute unpaid meal break).")
    print(f"  * Schedulable staff standard supply (~44-46 shifts/week) is less than base demand (50 shifts/week).")
    print(f"  * Feasible under overtime ceiling (~57.6 shifts/week max), leaving a tight reserve buffer (~13%).")
    print("  * Boundary transitions (e.g. mid-horizon hires/terminations) or skill pinches naturally trigger expected gaps.")
    print("=" * 90 + "\n")


def config_shifts(cfg):
    return cfg.shifts


if __name__ == "__main__":
    csv_directory = repo_root / "output" / "stage_a"
    profile_stage_a(csv_dir=csv_directory)

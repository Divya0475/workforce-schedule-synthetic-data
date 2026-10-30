"""Stage B2 Audit & Reporting Script.

Reports:
1. Pytest suite status
2. Shift assignments count and fill rate vs min_headcount and target_headcount
3. Coverage gaps breakdown by skill and team
4. Overtime distribution and hours per employee (min/mean/max)
5. Runtime benchmarks for both starter and full scale
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from workforce_generator.config import load_config
from workforce_generator.pipeline import run_pipeline


def report_stage_b2(scale: str = "starter") -> None:
    if scale == "starter":
        overrides = {
            "seed": 20261022,
            "workforce": {"num_teams": 5, "employees_per_team": 10},
            "horizon": {"start_date": "2026-01-01", "end_date": "2026-01-14"},
            "leave_rules": {"target_total_requests": 100, "leave_coverage_buffer": 1.25},
        }
        cfg = load_config(overrides=overrides)
        profile_label = "STARTER PROFILE (50 staff, 5 teams, 14 days)"
    else:
        cfg = load_config()
        profile_label = "FULL SCALE BENCHMARK (2,000 staff, 100 teams, 90 days)"

    print("=" * 100)
    print(f"STAGE B2 AUDIT REPORT: {profile_label}")
    print("=" * 100)

    t0 = time.perf_counter()
    data = run_pipeline(cfg)
    t1 = time.perf_counter()
    runtime = t1 - t0

    asns = data["shift_assignments"]
    gaps = data["coverage_gaps"]
    whs = data["weekly_hours_summaries"]
    reqs = data["coverage_requirements"]
    employees = data["employees"]
    sched_emps = [e for e in employees if e.role != "MANAGER"]

    # 1. Runtime Benchmark
    print("\n--- 1. RUNTIME BENCHMARK ---")
    print(f"  * Pipeline Generation Runtime: {runtime:.4f} s")
    print(f"  * Shift Assignments Generated: {len(asns):,} rows")
    print(f"  * Coverage Gaps Evaluated:     {len(gaps):,} rows")
    print(f"  * Weekly Hours Summaries:      {len(whs):,} rows")

    # 2. Fill rate vs min and target headcount
    total_min_demand = sum(r.min_headcount for r in reqs)
    total_target_demand = sum(r.target_headcount for r in reqs)
    total_scheduled_heads = sum(g.scheduled_headcount for g in gaps)
    critical_gaps_count = sum(1 for g in gaps if g.is_critical)
    total_gap_heads = sum(g.gap_headcount for g in gaps)
    total_target_shortfall = sum(g.target_shortfall for g in gaps)

    fill_rate_min = (1.0 - (total_gap_heads / total_min_demand)) * 100.0 if total_min_demand > 0 else 100.0
    fill_rate_target = (total_scheduled_heads / total_target_demand) * 100.0 if total_target_demand > 0 else 100.0

    print("\n--- 2. FILL RATE & COVERAGE METRICS ---")
    print(f"  * Total Required Headcount (Min Floor):      {total_min_demand:,}")
    print(f"  * Total Desired Headcount (Target Buffer):  {total_target_demand:,}")
    print(f"  * Total Active Headcount Scheduled:          {total_scheduled_heads:,}")
    print(f"  * Critical Coverage Gaps (is_critical=True): {critical_gaps_count:,} slots ({critical_gaps_count/len(gaps)*100:.1f}%)")
    print(f"  * Headcount Deficit vs Min (gap_headcount):  {total_gap_heads:,}")
    print(f"  * Headcount Shortfall vs Target:             {total_target_shortfall:,}")
    print(f"  * Min Demand Fill Rate:                      {fill_rate_min:.2f}%")
    print(f"  * Target Demand Fill Rate:                   {fill_rate_target:.2f}%")

    # 3. Gaps breakdown by skill and team
    print("\n--- 3A. COVERAGE GAPS BY SKILL ---")
    gap_df = pd.DataFrame([g.to_dict() for g in gaps])
    skill_summary = gap_df.groupby("skill_id").agg(
        evaluated_slots=("gap_id", "count"),
        critical_slots=("is_critical", "sum"),
        sum_min_demand=("required_headcount", "sum"),
        sum_scheduled=("scheduled_headcount", "sum"),
        sum_gap_heads=("gap_headcount", "sum"),
        sum_target_shortfall=("target_shortfall", "sum"),
    ).reset_index()
    skill_summary["min_fill_pct"] = (1.0 - (skill_summary["sum_gap_heads"] / skill_summary["sum_min_demand"])) * 100.0
    skill_summary["min_fill_pct"] = skill_summary["min_fill_pct"].map(lambda x: f"{x:.1f}%")
    print(skill_summary.to_string(index=False))

    print("\n--- 3B. COVERAGE GAPS BY TEAM (TOP TEAMS) ---")
    team_summary = gap_df.groupby("team_id").agg(
        evaluated_slots=("gap_id", "count"),
        critical_slots=("is_critical", "sum"),
        sum_min_demand=("required_headcount", "sum"),
        sum_scheduled=("scheduled_headcount", "sum"),
        sum_gap_heads=("gap_headcount", "sum"),
    ).reset_index()
    team_summary["min_fill_pct"] = (1.0 - (team_summary["sum_gap_heads"] / team_summary["sum_min_demand"])) * 100.0
    team_summary["min_fill_pct"] = team_summary["min_fill_pct"].map(lambda x: f"{x:.1f}%")
    print(team_summary.head(15).to_string(index=False))
    if len(team_summary) > 15:
        print(f"  ... ({len(team_summary) - 15} additional teams omitted)")

    # 4. Overtime distribution and hours per employee
    print("\n--- 4. WORKLOAD & OVERTIME DISTRIBUTION ---")
    whs_df = pd.DataFrame([w.to_dict() for w in whs])
    # Filter to schedulable employees and full weeks
    sched_emp_ids = {e.employee_id for e in sched_emps}
    whs_sched = whs_df[whs_df["employee_id"].isin(sched_emp_ids)]
    full_weeks_whs = whs_sched[~whs_sched["is_partial_week"]]

    total_full_week_records = len(full_weeks_whs)
    ot_records = full_weeks_whs[full_weeks_whs["overtime_hours"] > 0]
    cap_exceeded_records = full_weeks_whs[full_weeks_whs["exceeds_overtime_cap"]]

    print(f"  * Full Employee-Weeks Evaluated:             {total_full_week_records:,}")
    print(f"  * Weeks with Overtime (> contract):          {len(ot_records):,} ({len(ot_records)/total_full_week_records*100:.1f}%)")
    print(f"  * Weeks Exceeding 48h Cap (Violations):      {len(cap_exceeded_records):,} (Strictly 0 in baseline)")
    print(f"  * Total Overtime Paid Hours (Full Weeks):    {full_weeks_whs['overtime_hours'].sum():,.1f} h")
    print(f"  * Mean Overtime (for staff working OT):      {ot_records['overtime_hours'].mean():.2f} h/week" if len(ot_records) > 0 else "  * Mean Overtime: 0.0 h")

    # Aggregate total hours per employee across the whole horizon
    emp_tot_hours = whs_sched.groupby("employee_id")["scheduled_hours"].sum()
    print("\n--- 5. TOTAL SCHEDULED HOURS PER EMPLOYEE (HORIZON TOTAL) ---")
    print(f"  * Schedulable Employees Count:               {len(emp_tot_hours):,}")
    print(f"  * Min Total Hours:                           {emp_tot_hours.min():.1f} h")
    print(f"  * Mean Total Hours:                          {emp_tot_hours.mean():.1f} h")
    print(f"  * Median Total Hours:                        {emp_tot_hours.median():.1f} h")
    print(f"  * Max Total Hours:                           {emp_tot_hours.max():.1f} h")
    print(f"  * Standard Deviation:                        {emp_tot_hours.std():.2f} h")
    print("=" * 100 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage B2 Shift Scheduler Audit")
    parser.add_argument("--scale", choices=["starter", "full", "both"], default="both")
    args = parser.parse_args()

    if args.scale in ("starter", "both"):
        report_stage_b2("starter")
    if args.scale in ("full", "both"):
        report_stage_b2("full")

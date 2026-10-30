"""Stage B2 Audit & Reporting Script.

Reports:
 1. Runtime benchmark
 2. Fill rate vs min_headcount and target_headcount
 3. Coverage gaps breakdown by skill and team
 4. Overtime distribution and hours per employee (min/mean/max)
 5. Before/after comparison: day-by-day scheduler vs ISO-week two-pass scheduler
 6. Zero-hour employees: id, team, skills, reason
 7. CoverageGap reason distribution
 8. has_gap count
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
import pandas as pd

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from workforce_generator.config import load_config
from workforce_generator.pipeline import run_pipeline


# Baseline captured before tightening the Stage B2 invariant and reason audit.
BEFORE_FIX_MIN_FILL_PCT = 100.0 * (1.0 - 37 / 309)
BEFORE_FIX_CRITICAL_GAPS = 37
BEFORE_FIX_OT_EMPLOYEE_WEEKS = 7
BEFORE_FIX_MIN_HOURS = 0.0
BEFORE_FIX_MEDIAN_HOURS = 60.0
BEFORE_FIX_MAX_HOURS = 90.0
BEFORE_FIX_ALL_REASON_COUNTS = {
    "Target buffer omitted to prevent contractual overtime": 209,
    "Fully staffed": 63,
    "Single shift per day constraint": 21,
    "Approved leave absence": 11,
    "Unscheduled sick leave absence": 5,
}
BEFORE_FIX_CRITICAL_REASON_COUNTS = {
    "Single shift per day constraint": 21,
    "Approved leave absence": 11,
    "Unscheduled sick leave absence": 5,
}


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
    employee_skills = data["employee_skills"]
    leave_requests = data["leave_requests"]
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
    critical_gaps_count = sum(1 for g in gaps if g.gap_headcount > 0)
    has_gap_count = sum(1 for g in gaps if g.has_gap)
    total_gap_heads = sum(g.gap_headcount for g in gaps)
    total_target_shortfall = sum(g.target_shortfall for g in gaps)
    employee_skills_by_id = defaultdict(list)
    for employee_skill in employee_skills:
        employee_skills_by_id[employee_skill.employee_id].append(employee_skill)
    team_staff = defaultdict(list)
    for employee in sched_emps:
        team_staff[employee.team_id].append(employee)
    skill_pinches = sum(
        1
        for gap in gaps
        if gap.gap_headcount > 0
        and sum(
            any(
                employee_skill.skill_id == gap.skill_id
                and employee_skill.certified_date <= gap.effective_date
                and (
                    employee_skill.expiry_date is None
                    or employee_skill.expiry_date >= gap.effective_date
                )
                for employee_skill in employee_skills_by_id[employee.employee_id]
            )
            for employee in team_staff[gap.team_id]
        )
        < gap.required_headcount
    )

    fill_rate_min = (1.0 - (total_gap_heads / total_min_demand)) * 100.0 if total_min_demand > 0 else 100.0
    fill_rate_target = (total_scheduled_heads / total_target_demand) * 100.0 if total_target_demand > 0 else 100.0

    print("\n--- 2. FILL RATE & COVERAGE METRICS ---")
    print(f"  * Total Required Headcount (Min Floor):      {total_min_demand:,}")
    print(f"  * Total Desired Headcount (Target Buffer):  {total_target_demand:,}")
    print(f"  * Total Active Headcount Scheduled:          {total_scheduled_heads:,}")
    print(f"  * Critical Coverage Gaps (gap_headcount > 0): {critical_gaps_count:,} slots ({critical_gaps_count/len(gaps)*100:.1f}%)")
    print(f"  * has_gap=True count:                        {has_gap_count:,}  [matches critical count: {critical_gaps_count == has_gap_count}]")
    print(f"  * Headcount Deficit vs Min (gap_headcount):  {total_gap_heads:,}")
    print(f"  * Headcount Shortfall vs Target:             {total_target_shortfall:,}")
    print(f"  * Min Demand Fill Rate:                      {fill_rate_min:.2f}%")
    print(f"  * Target Demand Fill Rate:                   {fill_rate_target:.2f}%")
    print(f"  * Genuine Skill Pinches (qualified < minimum): {skill_pinches:,}")

    # 3. Gaps breakdown by skill and team
    print("\n--- 3A. COVERAGE GAPS BY SKILL ---")
    gap_df = pd.DataFrame([g.to_dict() for g in gaps])
    gap_df["critical_slot"] = gap_df["gap_headcount"] > 0
    skill_summary = gap_df.groupby("skill_id").agg(
        evaluated_slots=("gap_id", "count"),
        critical_slots=("critical_slot", "sum"),
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
        critical_slots=("critical_slot", "sum"),
        sum_min_demand=("required_headcount", "sum"),
        sum_scheduled=("scheduled_headcount", "sum"),
        sum_gap_heads=("gap_headcount", "sum"),
    ).reset_index()
    team_summary["min_fill_pct"] = (1.0 - (team_summary["sum_gap_heads"] / team_summary["sum_min_demand"])) * 100.0
    team_summary["min_fill_pct"] = team_summary["min_fill_pct"].map(lambda x: f"{x:.1f}%")
    print(team_summary.head(15).to_string(index=False))
    if len(team_summary) > 15:
        print(f"  ... ({len(team_summary) - 15} additional teams omitted)")

    # 4. CoverageGap reason distribution
    print("\n--- 4. COVERAGEGAP REASON DISTRIBUTION ---")
    all_reasons = Counter(g.reason for g in gaps)
    crit_reasons = Counter(g.reason for g in gaps if g.gap_headcount > 0)
    print("  All gaps:")
    for reason, cnt in sorted(all_reasons.items(), key=lambda x: -x[1]):
        pct = cnt / len(gaps) * 100.0
        print(f"    {cnt:5d} ({pct:5.1f}%)  {reason}")
    print("  Critical gaps only (gap_headcount > 0):")
    if crit_reasons:
        for reason, cnt in sorted(crit_reasons.items(), key=lambda x: -x[1]):
            pct = cnt / critical_gaps_count * 100.0
            print(f"    {cnt:5d} ({pct:5.1f}%)  {reason}")
    else:
        print("    (none)")

    # 5. Overtime distribution and hours per employee
    print("\n--- 5. WORKLOAD & OVERTIME DISTRIBUTION ---")
    whs_df = pd.DataFrame([w.to_dict() for w in whs])
    sched_emp_ids = {e.employee_id for e in sched_emps}
    whs_sched = whs_df[whs_df["employee_id"].isin(sched_emp_ids)]
    full_weeks_whs = whs_sched[~whs_sched["is_partial_week"]]

    total_full_week_records = len(full_weeks_whs)
    ot_records = full_weeks_whs[full_weeks_whs["overtime_hours"] > 0]
    cap_exceeded_records = full_weeks_whs[full_weeks_whs["exceeds_overtime_cap"]]

    print(f"  * Full Employee-Weeks Evaluated:             {total_full_week_records:,}")
    if total_full_week_records > 0:
        print(f"  * Weeks with Overtime (> contract):          {len(ot_records):,} ({len(ot_records)/total_full_week_records*100:.1f}%)")
    else:
        print(f"  * Weeks with Overtime (> contract):          {len(ot_records):,} (N/A)")
    print(f"  * Weeks Exceeding 48h Cap (Violations):      {len(cap_exceeded_records):,} (Strictly 0 in baseline)")
    print(f"  * Total Overtime Paid Hours (Full Weeks):    {full_weeks_whs['overtime_hours'].sum():,.1f} h")
    if len(ot_records) > 0:
        print(f"  * Mean Overtime (for staff working OT):      {ot_records['overtime_hours'].mean():.2f} h/week")
    else:
        print(f"  * Mean Overtime: 0.0 h")

    # 6. Total scheduled hours per employee
    emp_tot_hours = whs_sched.groupby("employee_id")["scheduled_hours"].sum()
    print("\n--- 6. TOTAL SCHEDULED HOURS PER EMPLOYEE (HORIZON TOTAL) ---")
    print(f"  * Schedulable Employees Count:               {len(emp_tot_hours):,}")
    print(f"  * Min Total Hours:                           {emp_tot_hours.min():.1f} h")
    print(f"  * Mean Total Hours:                          {emp_tot_hours.mean():.1f} h")
    print(f"  * Median Total Hours:                        {emp_tot_hours.median():.1f} h")
    print(f"  * Max Total Hours:                           {emp_tot_hours.max():.1f} h")
    print(f"  * Standard Deviation:                        {emp_tot_hours.std():.2f} h")

    # 7. Zero-hour employees table
    print("\n--- 7. ZERO-HOUR EMPLOYEES ---")
    paid_hours_per_shift = cfg.labor_constraints.paid_hours_per_shift
    emp_hours: dict = defaultdict(float)
    for a in asns:
        if a.assignment_status == "ACTIVE":
            emp_hours[a.employee_id] += paid_hours_per_shift

    emp_skills_map: dict = defaultdict(list)
    for es in employee_skills:
        emp_skills_map[es.employee_id].append(es.skill_id)

    emp_leave_days: dict = defaultdict(int)
    for lve in leave_requests:
        if lve.current_status == "APPROVED":
            days = (lve.end_date - lve.start_date).days + 1
            emp_leave_days[lve.employee_id] += days

    zero_hour_emps = [e for e in sched_emps if emp_hours[e.employee_id] == 0.0]
    gap_by_coverage_id = {gap.coverage_id: gap for gap in gaps}
    if zero_hour_emps:
        print(f"  Found {len(zero_hour_emps)} employees with 0.0 scheduled hours:")
        for emp in zero_hour_emps:
            eid = emp.employee_id
            skills = emp_skills_map[eid]
            leave_days = emp_leave_days[eid]
            qualified_requirements = [
                req
                for req in reqs
                if req.team_id == emp.team_id
                and any(
                    employee_skill.skill_id == req.skill_id
                    and employee_skill.certified_date <= req.effective_date
                    and (
                        employee_skill.expiry_date is None
                        or employee_skill.expiry_date >= req.effective_date
                    )
                    for employee_skill in employee_skills_by_id[eid]
                )
            ]
            if qualified_requirements:
                target_gaps = [
                    gap_by_coverage_id[req.coverage_id]
                    for req in qualified_requirements
                    if gap_by_coverage_id[req.coverage_id].target_shortfall > 0
                ]
                if target_gaps:
                    reasons = sorted({gap.reason for gap in target_gaps})
                else:
                    reasons = ["all qualified target demand met"]
            else:
                reasons = ["no qualified coverage requirements during horizon"]

            print(
                f"    {eid}  team={emp.team_id}  role={emp.role}  "
                f"skills={skills}  leave_days={leave_days}  reason={'; '.join(reasons)}"
            )
    else:
        print("  None — all schedulable employees have at least 1 shift.")

    # 8. Before/After comparison (starter profile only)
    if scale == "starter":
        after_critical = critical_gaps_count
        after_ot_weeks = len(ot_records)
        after_min_h = emp_tot_hours.min() if len(emp_tot_hours) > 0 else 0.0
        after_median_h = emp_tot_hours.median() if len(emp_tot_hours) > 0 else 0.0
        after_max_h = emp_tot_hours.max() if len(emp_tot_hours) > 0 else 0.0

        print("\n--- 8. STARTER BEFORE vs AFTER: STRICT INVARIANT FIX ---")
        print(f"  {'Metric':<45} {'BEFORE FIX':>15} {'AFTER FIX':>15} {'Change':>12}")
        print(f"  {'-'*45} {'-'*15} {'-'*15} {'-'*12}")

        def row(label, before, after, fmt=".0f", invert=False):
            delta = after - before
            arrow = "v" if delta < 0 else ("^" if delta > 0 else "=")
            if invert:
                # For gap/OT metrics, down is good
                colour = "OK" if delta < 0 else ("!!" if delta > 0 else "--")
            else:
                colour = "OK" if delta > 0 else ("!!" if delta < 0 else "--")
            print(f"  {label:<45} {before:>15{fmt}} {after:>15{fmt}} {arrow} {abs(delta):{fmt}} {colour}")

        row("Min demand fill rate (%)", BEFORE_FIX_MIN_FILL_PCT, fill_rate_min, fmt=".2f")
        row("Critical gaps (gap_headcount > 0)", BEFORE_FIX_CRITICAL_GAPS, after_critical, fmt=".0f", invert=True)
        row("Employee-weeks with overtime", BEFORE_FIX_OT_EMPLOYEE_WEEKS, after_ot_weeks, fmt=".0f", invert=True)
        row("Min horizon hours per employee (h)", BEFORE_FIX_MIN_HOURS, after_min_h, fmt=".1f")
        row("Median horizon hours per employee (h)", BEFORE_FIX_MEDIAN_HOURS, after_median_h, fmt=".1f")
        row("Max horizon hours per employee (h)", BEFORE_FIX_MAX_HOURS, after_max_h, fmt=".1f")

        print("  All-gap reason counts (before -> after):")
        for reason in sorted(set(BEFORE_FIX_ALL_REASON_COUNTS) | set(all_reasons)):
            print(
                f"    {reason}: {BEFORE_FIX_ALL_REASON_COUNTS.get(reason, 0)} "
                f"-> {all_reasons.get(reason, 0)}"
            )
        print("  Critical-gap reason counts (before -> after):")
        for reason in sorted(set(BEFORE_FIX_CRITICAL_REASON_COUNTS) | set(crit_reasons)):
            print(
                f"    {reason}: {BEFORE_FIX_CRITICAL_REASON_COUNTS.get(reason, 0)} "
                f"-> {crit_reasons.get(reason, 0)}"
            )

    print("=" * 100 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage B2 Shift Scheduler Audit")
    parser.add_argument("--scale", choices=["starter", "full", "both"], default="both")
    args = parser.parse_args()

    if args.scale in ("starter", "both"):
        report_stage_b2("starter")
    if args.scale in ("full", "both"):
        report_stage_b2("full")

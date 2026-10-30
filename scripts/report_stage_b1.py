"""Stage B1 Reporting Script: Leave requests, decisions, and lifecycle events audit.

Generates and displays:
1. Leave counts by type and status (cross-tabulation)
2. Approval rate by month
3. Rejection reasons breakdown
4. Leave hours per team
5. Runtime benchmarks for both Starter and Full scale
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict
import numpy as np
import pandas as pd

# Ensure src is on sys.path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from workforce_generator.config import load_config
from workforce_generator.generators.coverage import generate_coverage_requirements
from workforce_generator.generators.holidays import generate_holidays
from workforce_generator.generators.leave import generate_leave_requests_and_events
from workforce_generator.generators.organization import generate_organization


def run_and_report_b1(scale: str = "starter") -> None:
    if scale == "starter":
        overrides = {
            "seed": 20261022,
            "workforce": {
                "num_teams": 5,
                "employees_per_team": 10,
            },
            "horizon": {
                "start_date": "2026-01-01",
                "end_date": "2026-01-14",
            },
            "leave_rules": {
                "target_total_requests": 100,
                "leave_coverage_buffer": 1.4,
            },
        }
        cfg = load_config(overrides=overrides)
        profile_name = "STARTER PROFILE (50 staff, 5 teams, 14 days, 100 requests)"
    else:
        # Full scale production configuration
        cfg = load_config()
        profile_name = "FULL SCALE BENCHMARK (2,000 staff, 100 teams, 90 days, 5,000 requests)"

    print("=" * 100)
    print(f"STAGE B1 AUDIT REPORT: {profile_name}")
    print("=" * 100)

    seed_seq = np.random.SeedSequence(cfg.seed)
    rng_org, rng_leave = [np.random.default_rng(s) for s in seed_seq.spawn(2)]

    t0 = time.perf_counter()
    teams, skills, employees, employee_skills = generate_organization(cfg, rng_org)
    holidays = generate_holidays(cfg)
    coverage_reqs = generate_coverage_requirements(cfg, teams, holidays)
    t_prep = time.perf_counter()

    leave_requests, leave_events = generate_leave_requests_and_events(
        cfg, rng_leave, employees, employee_skills, coverage_reqs, holidays
    )
    t_end = time.perf_counter()

    prep_duration = t_prep - t0
    leave_duration = t_end - t_prep
    total_duration = t_end - t0

    req_df = pd.DataFrame([r.to_dict() for r in leave_requests])
    evt_df = pd.DataFrame([ev.to_dict() for ev in leave_events])
    emp_df = pd.DataFrame([e.to_dict() for e in employees])
    team_df = pd.DataFrame([t.to_dict() for t in teams])

    # Join team_id to requests via employee
    req_df = req_df.merge(emp_df[["employee_id", "team_id"]], on="employee_id", how="left")

    print("\n--- RUNTIME BENCHMARK ---")
    print(f"  * Upstream Dimensions Generation (Org, Holidays, Coverage): {prep_duration:.4f} s")
    print(f"  * Leave Requests & Events Generation (Stage B1):           {leave_duration:.4f} s")
    print(f"  * Total End-to-End Execution Time:                         {total_duration:.4f} s")
    print(f"  * Generated Leave Requests:                                {len(req_df):,} rows")
    print(f"  * Generated Leave Request Events:                          {len(evt_df):,} rows")

    # --------------------------------------------------------------------------
    # 1. Leave counts by type and status
    # --------------------------------------------------------------------------
    print("\n--- 1. LEAVE COUNTS BY TYPE AND STATUS ---")
    ct = pd.crosstab(req_df["leave_type"], req_df["current_status"], margins=True, margins_name="TOTAL")
    print(ct.to_string())

    # --------------------------------------------------------------------------
    # 2. Approval rate by month and by team
    # --------------------------------------------------------------------------
    print("\n--- 2A. APPROVAL RATE BY MONTH ---")
    req_df["start_month"] = pd.to_datetime(req_df["start_date"]).dt.to_period("M").astype(str)

    # Note: requests with SUBMITTED or CANCELLED_BEFORE_DECISION were not decided.
    # Decided requests are APPROVED, CANCELLED (post-approval), and REJECTED.
    monthly_stats = req_df.groupby("start_month").agg(
        total_requests=("leave_request_id", "count"),
        approved=("current_status", lambda s: (s == "APPROVED").sum()),
        cancelled_post_approval=("current_status", lambda s: (s == "CANCELLED").sum()),
        rejected=("current_status", lambda s: (s == "REJECTED").sum()),
        pending_submitted=("current_status", lambda s: (s == "SUBMITTED").sum()),
        cancelled_pre_decision=("decided_at", lambda d: (d.isna() & (req_df.loc[d.index, "current_status"] == "CANCELLED")).sum()),
    ).reset_index()

    monthly_stats["initially_approved"] = monthly_stats["approved"] + (monthly_stats["cancelled_post_approval"] - monthly_stats["cancelled_pre_decision"])
    monthly_stats["decided_requests"] = monthly_stats["initially_approved"] + monthly_stats["rejected"]
    monthly_stats["approval_rate_pct"] = (monthly_stats["initially_approved"] / monthly_stats["decided_requests"].replace(0, np.nan)) * 100.0
    monthly_stats["approval_rate_pct"] = monthly_stats["approval_rate_pct"].map(lambda x: f"{x:.1f}%" if pd.notna(x) else "N/A")
    print(monthly_stats[["start_month", "total_requests", "decided_requests", "initially_approved", "rejected", "approval_rate_pct"]].to_string(index=False))

    print("\n--- 2B. APPROVAL RATE BY TEAM ---")
    team_stats = req_df.groupby("team_id").agg(
        total_requests=("leave_request_id", "count"),
        approved=("current_status", lambda s: (s == "APPROVED").sum()),
        cancelled_post_approval=("current_status", lambda s: (s == "CANCELLED").sum()),
        rejected=("current_status", lambda s: (s == "REJECTED").sum()),
        cancelled_pre_decision=("decided_at", lambda d: (d.isna() & (req_df.loc[d.index, "current_status"] == "CANCELLED")).sum()),
    ).reset_index()
    team_stats["initially_approved"] = team_stats["approved"] + (team_stats["cancelled_post_approval"] - team_stats["cancelled_pre_decision"])
    team_stats["decided_requests"] = team_stats["initially_approved"] + team_stats["rejected"]
    team_stats["approval_rate_pct"] = (team_stats["initially_approved"] / team_stats["decided_requests"].replace(0, np.nan)) * 100.0
    team_stats["approval_rate_pct"] = team_stats["approval_rate_pct"].map(lambda x: f"{x:.1f}%" if pd.notna(x) else "N/A")
    team_stats = team_stats.merge(team_df[["team_id", "department"]], on="team_id", how="left")
    cols_team = ["team_id", "department", "total_requests", "decided_requests", "initially_approved", "rejected", "approval_rate_pct"]
    print(team_stats[cols_team].head(25).to_string(index=False))
    if len(team_stats) > 25:
        print(f"  ... ({len(team_stats) - 25} additional teams omitted from preview)")

    # --------------------------------------------------------------------------
    # 3. Employees who can never get weekday leave (Oncology pinch & structural bottlenecks)
    # --------------------------------------------------------------------------
    print("\n--- 3. EMPLOYEES WHO CAN NEVER GET WEEKDAY LEAVE ---")
    # An employee can never get weekday leave if for some required skill on their team,
    # non-manager baseline qualified holders <= ceil(daily_min_sum * buffer)
    from datetime import date, timedelta
    import math

    # Map employee -> skills
    emp_skills_map: Dict[str, set] = {}
    for es in employee_skills:
        emp_skills_map.setdefault(es.employee_id, set()).add(es.skill_id)

    # Non-manager active employees per (team_id, skill_id) on weekdays
    team_emps: Dict[str, list] = {}
    for e in employees:
        if e.role != "MANAGER":
            team_emps.setdefault(e.team_id, []).append(e)

    # Compute daily weekday min demand by (team_id, skill_id)
    daily_min_demand: Dict[tuple, int] = {}
    for req in coverage_reqs:
        d = req.effective_date if isinstance(req.effective_date, date) else date.fromisoformat(req.effective_date)
        if d.weekday() < 5 and req.min_headcount > 0:
            key = (req.team_id, d, req.skill_id)
            daily_min_demand[key] = daily_min_demand.get(key, 0) + req.min_headcount

    # Identify constrained skills where baseline holders <= threshold
    pinched_employees = []
    buffer = cfg.leave_rules.leave_coverage_buffer

    for e in employees:
        if e.role == "MANAGER":
            continue
        e_skills = emp_skills_map.get(e.employee_id, set())
        for (team_id, d, skill_id), min_sum in daily_min_demand.items():
            if team_id != e.team_id:
                continue
            if skill_id in e_skills:
                # Count non-manager active holders on date d
                holders = [
                    h for h in team_emps.get(team_id, [])
                    if skill_id in emp_skills_map.get(h.employee_id, set())
                    and (h.hire_date if isinstance(h.hire_date, date) else date.fromisoformat(h.hire_date)) <= d
                    and (h.termination_date is None or (h.termination_date if isinstance(h.termination_date, date) else date.fromisoformat(h.termination_date)) >= d)
                ]
                threshold = math.ceil(min_sum * buffer)
                if len(holders) <= threshold:
                    pinched_employees.append({
                        "employee_id": e.employee_id,
                        "team_id": e.team_id,
                        "skill_id": skill_id,
                        "weekday_date": d.isoformat(),
                        "min_demand": min_sum,
                        "threshold": threshold,
                        "team_holders": len(holders),
                    })

    if pinched_employees:
        pinch_df = pd.DataFrame(pinched_employees)
        unique_pinched = pinch_df.groupby(["employee_id", "team_id", "skill_id"]).agg(
            min_demand=("min_demand", "first"),
            threshold=("threshold", "first"),
            team_holders=("team_holders", "first"),
            constrained_days_count=("weekday_date", "count"),
        ).reset_index()
        print(unique_pinched.to_string(index=False))
        print(f"  Total structurally constrained employees: {len(unique_pinched)}")
    else:
        print("  None. All teams have sufficient baseline skill holders for weekday leaves.")

    # --------------------------------------------------------------------------
    # 4. Rejection reasons breakdown
    # --------------------------------------------------------------------------
    print("\n--- 4. REJECTION REASONS BREAKDOWN ---")
    rejections = req_df[req_df["current_status"] == "REJECTED"]
    if len(rejections) == 0:
        print("  Zero rejections recorded.")
    else:
        # Group by skill or reason pattern
        rej_counts = rejections["decision_reason"].value_counts().reset_index()
        rej_counts.columns = ["rejection_reason", "count"]
        print(rej_counts.head(15).to_string(index=False))
        if len(rej_counts) > 15:
            print(f"  ... ({len(rej_counts) - 15} additional distinct date/skill reasons truncated)")

    # --------------------------------------------------------------------------
    # 4. Leave hours per team
    # --------------------------------------------------------------------------
    print("\n--- 4. LEAVE HOURS PER TEAM ---")
    team_hours = req_df.groupby(["team_id", "current_status"])["requested_hours"].sum().unstack(fill_value=0.0).reset_index()
    # Add team department
    team_hours = team_hours.merge(team_df[["team_id", "department"]], on="team_id", how="left")
    # Total requested hours across all statuses
    status_cols = [c for c in team_hours.columns if c not in ("team_id", "department")]
    team_hours["TOTAL_HOURS"] = team_hours[status_cols].sum(axis=1)

    # Reorder columns
    ordered_cols = ["team_id", "department"] + [c for c in ["APPROVED", "CANCELLED", "REJECTED"] if c in team_hours.columns] + ["TOTAL_HOURS"]
    print(team_hours[ordered_cols].head(25).to_string(index=False))
    if len(team_hours) > 25:
        print(f"  ... ({len(team_hours) - 25} additional teams omitted from preview)")
    print("=" * 100 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage B1 Leave Requests & Events Audit")
    parser.add_argument("--scale", choices=["starter", "full", "both"], default="both", help="Scale profile to audit")
    args = parser.parse_args()

    if args.scale in ("starter", "both"):
        run_and_report_b1("starter")
    if args.scale in ("full", "both"):
        run_and_report_b1("full")


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

    rng = np.random.default_rng(cfg.seed)

    t0 = time.perf_counter()
    teams, skills, employees, employee_skills = generate_organization(cfg, rng)
    holidays = generate_holidays(cfg)
    coverage_reqs = generate_coverage_requirements(cfg, teams, holidays)
    t_prep = time.perf_counter()

    leave_requests, leave_events = generate_leave_requests_and_events(
        cfg, rng, employees, employee_skills, coverage_reqs, holidays
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
    # 2. Approval rate by month
    # --------------------------------------------------------------------------
    print("\n--- 2. APPROVAL RATE BY MONTH ---")
    req_df["start_month"] = pd.to_datetime(req_df["start_date"]).dt.to_period("M").astype(str)

    monthly_stats = req_df.groupby("start_month").agg(
        total_requests=("leave_request_id", "count"),
        approved=("current_status", lambda s: (s == "APPROVED").sum()),
        cancelled=("current_status", lambda s: (s == "CANCELLED").sum()),
        rejected=("current_status", lambda s: (s == "REJECTED").sum()),
    ).reset_index()

    # Note: CANCELLED requests were approved before cancellation, so effective approved decisions = approved + cancelled
    monthly_stats["initially_approved"] = monthly_stats["approved"] + monthly_stats["cancelled"]
    monthly_stats["approval_rate_pct"] = (monthly_stats["initially_approved"] / monthly_stats["total_requests"]) * 100.0
    monthly_stats["approval_rate_pct"] = monthly_stats["approval_rate_pct"].map(lambda x: f"{x:.1f}%")
    print(monthly_stats.to_string(index=False))

    # --------------------------------------------------------------------------
    # 3. Rejection reasons breakdown
    # --------------------------------------------------------------------------
    print("\n--- 3. REJECTION REASONS BREAKDOWN ---")
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

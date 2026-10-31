"""Descriptive fairness report for workforce schedule data.

Computes per non-manager employee: shift count, paid hours,
night-shift count, and weekend-shift count.  Reports min, max, mean,
standard deviation and Gini coefficient for each metric, both overall
and grouped by team.

DISCLAIMER: These are descriptive metrics and not a fairness guarantee.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Gini coefficient
# ---------------------------------------------------------------------------

def gini(values: np.ndarray) -> float:
    """Return the Gini coefficient of *values* (non-negative array).

    Returns 0 for arrays of length < 2 or with zero mean.
    Formula (Wikipedia): G = sum_i sum_j |xi - xj| / (2 * n^2 * mean).
    This is always in [0, 1] for non-negative inputs.
    """
    arr = np.asarray(values, dtype=float)
    n = len(arr)
    if n < 2 or arr.mean() == 0:
        return 0.0
    diff_sum = np.sum(np.abs(arr[:, None] - arr[None, :]))
    return float(diff_sum / (2.0 * n**2 * arr.mean()))


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------

def _stats(series: pd.Series) -> dict:
    arr = series.to_numpy(dtype=float)
    return {
        "count": int(len(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0,
        "gini": gini(arr),
    }


METRICS = ["shift_count", "paid_hours", "night_shifts", "weekend_shifts"]

METRIC_LABELS = {
    "shift_count": "Shift count",
    "paid_hours": "Paid hours",
    "night_shifts": "Night shifts",
    "weekend_shifts": "Weekend shifts",
}


def _table_md(title: str, rows: list[dict], metric_col: str = "metric") -> list[str]:
    """Return markdown lines for a stats table."""
    lines = [f"### {title}", ""]
    lines.append(
        f"| {metric_col.capitalize()} | n | Min | Max | Mean | Std | Gini |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in rows:
        label = row.get("label", row.get("metric", ""))
        lines.append(
            f"| {label} | {row['count']} |"
            f" {row['min']:.2f} | {row['max']:.2f} |"
            f" {row['mean']:.2f} | {row['std']:.2f} | {row['gini']:.4f} |"
        )
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def compute_metrics(data_dir: Path) -> pd.DataFrame:
    """Load CSVs from *data_dir* and return per-employee metrics."""
    assignments = pd.read_csv(data_dir / "shift_assignments.csv")
    employees = pd.read_csv(data_dir / "employees.csv")
    templates = pd.read_csv(data_dir / "shift_templates.csv")

    # Active assignments only
    active = assignments[assignments["assignment_status"] == "ACTIVE"].copy()

    # Non-manager employees
    non_mgr = employees[employees["role"] != "MANAGER"][["employee_id", "team_id"]].copy()

    # Merge to get only non-manager active assignments
    merged = active.merge(non_mgr, on="employee_id", suffixes=("", "_emp"))
    # Use team_id from assignment (already present); keep consistent
    if "team_id_emp" in merged.columns:
        merged = merged.drop(columns=["team_id_emp"])

    # Overnight flag from templates
    overnight_ids = set(
        templates.loc[templates["is_overnight"].astype(str).str.lower() == "true",
                      "shift_template_id"]
    )
    merged["is_night"] = merged["shift_template_id"].isin(overnight_ids)

    # Weekend flag
    merged["shift_date"] = pd.to_datetime(merged["shift_date"])
    merged["is_weekend"] = merged["shift_date"].dt.dayofweek >= 5  # Sat=5, Sun=6

    # Per-employee aggregation
    per_emp = (
        merged.groupby("employee_id")
        .agg(
            team_id=("team_id", "first"),
            shift_count=("assignment_id", "count"),
            paid_hours=("duration_hours", "sum"),
            night_shifts=("is_night", "sum"),
            weekend_shifts=("is_weekend", "sum"),
        )
        .reset_index()
    )
    return per_emp


def build_report(per_emp: pd.DataFrame) -> list[str]:
    """Return markdown lines for the full fairness report."""
    lines: list[str] = [
        "# Fairness Summary",
        "",
        "> **Disclaimer:** These are descriptive metrics and not a fairness guarantee.",
        "> The scheduler does not optimise for fairness; these numbers describe",
        "> the distribution that emerges from the synthetic generation process.",
        "",
        f"Non-manager employees included: {len(per_emp)}",
        "",
    ]

    # Overall summary
    lines.append("## Overall metrics")
    lines.append("")
    overall_rows = []
    for m in METRICS:
        stats = _stats(per_emp[m])
        stats["label"] = METRIC_LABELS[m]
        overall_rows.append(stats)
    lines += _table_md("All non-manager employees", overall_rows)

    # Per-team summary
    lines.append("## Per-team metrics")
    lines.append("")
    for team_id, group in per_emp.groupby("team_id"):
        team_rows = []
        for m in METRICS:
            stats = _stats(group[m])
            stats["label"] = METRIC_LABELS[m]
            team_rows.append(stats)
        lines += _table_md(str(team_id), team_rows)

    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate descriptive fairness report for clean schedule data."
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        type=Path,
        help="Path to a clean CSV directory (must contain shift_assignments.csv, "
             "employees.csv, shift_templates.csv).",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output directory; fairness_summary.md is written here.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    per_emp = compute_metrics(data_dir)
    lines = build_report(per_emp)
    out_path = out_dir / "fairness_summary.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"Written: {out_path}")
    print(f"Non-manager employees: {len(per_emp)}")
    for m in METRICS:
        arr = per_emp[m].to_numpy(dtype=float)
        print(
            f"  {m}: min={arr.min():.2f} max={arr.max():.2f} "
            f"mean={arr.mean():.2f} std={arr.std(ddof=1 if len(arr)>1 else 0):.2f} "
            f"gini={gini(arr):.4f}"
        )
    print()
    print("DISCLAIMER: These are descriptive metrics and not a fairness guarantee.")


if __name__ == "__main__":
    main()

"""Raw Stage C lifecycle, attendance, coverage-change, and runtime report."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
import time

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from workforce_generator.config import load_config
from workforce_generator.pipeline import run_pipeline


STARTER_OVERRIDES = {
    "seed": 20261022,
    "workforce": {"num_teams": 5, "employees_per_team": 10},
    "horizon": {"start_date": "2026-01-01", "end_date": "2026-01-14"},
    "leave_rules": {"target_total_requests": 100, "leave_coverage_buffer": 1.25},
}


def report_scale(scale: str) -> None:
    cfg = load_config(overrides=STARTER_OVERRIDES) if scale == "starter" else load_config()
    start = time.perf_counter()
    data = run_pipeline(cfg)
    runtime = time.perf_counter() - start

    print(f"PROFILE: {scale}")
    print(f"Pipeline runtime seconds: {runtime:.4f}")
    print(f"Swap requests: {len(data['swap_requests'])}")
    print(f"Swap events: {len(data['swap_events'])}")
    print(f"Attendance events: {len(data['attendance_events'])}")
    print(f"Shift assignment rows including history: {len(data['shift_assignments'])}")

    status_counts = Counter(request.current_status for request in data["swap_requests"])
    print("Swap counts by status:")
    for status in ("REQUESTED", "APPROVED", "REJECTED", "APPLIED"):
        print(f"  {status}: {status_counts.get(status, 0)}")

    coverage_prefix = "Operational coverage could not accommodate the exchange: "
    rejection_reasons = Counter()
    for request in data["swap_requests"]:
        if request.current_status != "REJECTED":
            continue
        reason = request.rejection_reason or "(missing reason)"
        if reason.startswith(coverage_prefix):
            reason = reason[len(coverage_prefix):]
        reason_code = reason.split(":", 1)[0]
        rejection_reasons[reason_code] += 1
    print("Swap rejection reasons:")
    if rejection_reasons:
        for reason, count in sorted(rejection_reasons.items()):
            print(f"  {reason}: {count}")
    else:
        print("  (none)")

    attendance_counts = Counter(event.event_type for event in data["attendance_events"])
    print("Attendance counts by event type:")
    for event_type in ("CLOCK_IN", "CLOCK_OUT", "NO_SHOW"):
        print(f"  {event_type}: {attendance_counts.get(event_type, 0)}")

    exception_counts = Counter(
        event.exception_type or "NONE" for event in data["attendance_events"]
    )
    print("Attendance counts by exception type:")
    for exception_type in ("LATE", "EARLY_LEAVE", "NO_SHOW", "OVERTIME", "NONE"):
        print(f"  {exception_type}: {exception_counts.get(exception_type, 0)}")

    baseline_counts = Counter()
    final_counts = Counter()
    for assignment in data["shift_assignments"]:
        if not assignment.fulfills_skill_id:
            continue
        key = (
            assignment.shift_date,
            assignment.team_id,
            assignment.shift_template_id,
            assignment.fulfills_skill_id,
        )
        if assignment.swap_request_id is None:
            baseline_counts[key] += 1
        if assignment.assignment_status == "ACTIVE":
            final_counts[key] += 1

    gap_changes = []
    baseline_gap_slots = 0
    final_gap_slots = 0
    baseline_gap_heads = 0
    final_gap_heads = 0
    for requirement in data["coverage_requirements"]:
        key = (
            requirement.effective_date,
            requirement.team_id,
            requirement.shift_template_id,
            requirement.skill_id,
        )
        before_gap = max(0, requirement.min_headcount - baseline_counts[key])
        after_gap = max(0, requirement.min_headcount - final_counts[key])
        baseline_gap_slots += before_gap > 0
        final_gap_slots += after_gap > 0
        baseline_gap_heads += before_gap
        final_gap_heads += after_gap
        if before_gap != after_gap:
            gap_changes.append((requirement.coverage_id, before_gap, after_gap))
    print("CoverageGap changes after swaps:")
    print(f"  Baseline critical slots: {baseline_gap_slots}")
    print(f"  Final critical slots: {final_gap_slots}")
    print(f"  Baseline minimum-gap headcount: {baseline_gap_heads}")
    print(f"  Final minimum-gap headcount: {final_gap_heads}")
    print(f"  Changed coverage requirement slots: {len(gap_changes)}")
    for coverage_id, before_gap, after_gap in gap_changes:
        print(f"  {coverage_id}: {before_gap} -> {after_gap}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stage C raw metrics report")
    parser.add_argument("--scale", choices=("starter", "full", "both"), default="both")
    args = parser.parse_args()
    if args.scale in {"starter", "both"}:
        report_scale("starter")
    if args.scale in {"full", "both"}:
        report_scale("full")

"""Generate clean and dirty workforce datasets for the starter or full profile."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from workforce_generator.anomalies import generate_dirty_dataset
from workforce_generator.config import load_config
from workforce_generator.export import write_dataset
from workforce_generator.pipeline import run_pipeline


STARTER_OVERRIDES = {
    "seed": 20261022,
    "workforce": {"num_teams": 5, "employees_per_team": 10},
    "horizon": {"start_date": "2026-01-01", "end_date": "2026-01-14"},
    "leave_rules": {"target_total_requests": 100, "leave_coverage_buffer": 1.25},
}


def generate_profile(
    profile: str,
    output_dir: str | Path,
    anomalies_per_type: int | None = None,
) -> dict:
    if profile not in {"starter", "full"}:
        raise ValueError(f"Unknown profile: {profile!r}")
    default_count = 3 if profile == "starter" else 15
    counts_per_type = (
        default_count if anomalies_per_type is None else anomalies_per_type
    )
    if counts_per_type < 1:
        raise ValueError("--anomalies-per-type must be at least one")

    total_start = time.perf_counter()
    cfg = (
        load_config(overrides=STARTER_OVERRIDES)
        if profile == "starter"
        else load_config()
    )
    generation_start = time.perf_counter()
    clean_data = run_pipeline(cfg)
    generation_seconds = time.perf_counter() - generation_start

    child_streams = np.random.SeedSequence(cfg.seed).spawn(6)
    anomaly_seed = child_streams[5]
    injection_seed, attendance_seed = anomaly_seed.spawn(2)
    anomaly_start = time.perf_counter()
    dirty_tables, ground_truth = generate_dirty_dataset(
        cfg,
        clean_data,
        np.random.default_rng(injection_seed),
        np.random.default_rng(attendance_seed),
        counts_per_type,
    )
    anomaly_seconds = time.perf_counter() - anomaly_start

    export_start = time.perf_counter()
    clean_tables = {
        name: rows
        for name, rows in clean_data.items()
        if isinstance(rows, list)
    }
    manifest = write_dataset(
        output_dir,
        profile,
        clean_tables,
        dirty_tables,
        ground_truth,
    )
    export_seconds = time.perf_counter() - export_start
    print(f"PROFILE: {profile}")
    print(f"Pipeline runtime seconds: {generation_seconds:.4f}")
    print(f"Anomaly injection and dirty recomputation seconds: {anomaly_seconds:.4f}")
    print(f"CSV export seconds: {export_seconds:.4f}")
    print(f"Total runtime seconds: {time.perf_counter() - total_start:.4f}")
    print("Exported row counts:")
    for table_name in sorted(clean_tables):
        print(
            f"  {table_name}: clean={len(clean_tables[table_name])}, "
            f"dirty={len(dirty_tables[table_name])}"
        )
    print("Anomaly counts:")
    anomaly_counts = Counter(row["issue_type"] for row in ground_truth)
    for issue_type in sorted(anomaly_counts):
        print(f"  {issue_type}: {anomaly_counts[issue_type]}")
    print("Sample ground truth rows:")
    sample_by_type = {
        issue_type: next(
            row for row in ground_truth if row["issue_type"] == issue_type
        )
        for issue_type in sorted(anomaly_counts)
    }
    for row in sample_by_type.values():
        print(f"  {json.dumps(row, sort_keys=True)}")
    print(f"Manifest: {Path(output_dir) / profile / 'manifest.json'}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("starter", "full"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--anomalies-per-type", type=int)
    args = parser.parse_args()
    generate_profile(args.profile, args.out, args.anomalies_per_type)


if __name__ == "__main__":
    main()

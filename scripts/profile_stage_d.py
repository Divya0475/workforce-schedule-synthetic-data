"""Profile full-scale pipeline stages and hash generated CSV tables."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import time
from typing import Any

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

import workforce_generator.pipeline as pipeline
from workforce_generator.config import load_config


def _csv_table_hash(rows: list[Any]) -> str:
    if not rows:
        return hashlib.sha256(b"").hexdigest()
    records = [row.to_dict() for row in rows]
    headers = list(records[0])
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(headers)
    serialized_rows = []
    for record in records:
        row_output = io.StringIO(newline="")
        csv.writer(row_output, lineterminator="").writerow(
            [record.get(header) for header in headers]
        )
        serialized_rows.append(row_output.getvalue())
    output.write("\n".join(sorted(serialized_rows)))
    output.write("\n")
    return hashlib.sha256(output.getvalue().encode("utf-8")).hexdigest()


def run_profile(hashes_path: Path | None, compare_hashes_path: Path | None) -> None:
    stage_functions = {
        "scheduler": "schedule_shifts",
        "swaps": "generate_swap_requests_and_events",
        "attendance": "generate_attendance_events",
        "analytics_coverage": "compute_coverage_gaps",
        "analytics_weekly_hours": "compute_weekly_hours_summaries",
    }
    stage_times = {name: 0.0 for name in stage_functions}
    original_functions = {}
    for stage_name, function_name in stage_functions.items():
        original = getattr(pipeline, function_name)
        original_functions[function_name] = original

        def timed(*args, __original=original, __stage=stage_name, **kwargs):
            start = time.perf_counter()
            try:
                return __original(*args, **kwargs)
            finally:
                stage_times[__stage] += time.perf_counter() - start

        setattr(pipeline, function_name, timed)

    wall_start = time.perf_counter()
    data = pipeline.run_pipeline(load_config())
    wall_seconds = time.perf_counter() - wall_start
    for function_name, original in original_functions.items():
        setattr(pipeline, function_name, original)

    stage_times["analytics"] = (
        stage_times.pop("analytics_coverage")
        + stage_times.pop("analytics_weekly_hours")
    )
    hashes = {
        table_name: _csv_table_hash(rows)
        for table_name, rows in data.items()
        if isinstance(rows, list)
    }

    print(f"Pipeline wall seconds: {wall_seconds:.4f}")
    for stage_name in ("scheduler", "swaps", "attendance", "analytics"):
        print(f"{stage_name} seconds: {stage_times[stage_name]:.4f}")
    print("Table SHA-256 (sorted CSV rows):")
    for table_name, digest in sorted(hashes.items()):
        print(f"  {table_name}: {digest}")
    if compare_hashes_path is not None:
        baseline_hashes = json.loads(compare_hashes_path.read_text())
        print("Table hash comparison:")
        for table_name in sorted(set(baseline_hashes) | set(hashes)):
            before = baseline_hashes.get(table_name)
            after = hashes.get(table_name)
            result = "IDENTICAL" if before == after else "DIFFERENT"
            print(f"  {table_name}: {result}")
    if hashes_path is not None:
        hashes_path.parent.mkdir(parents=True, exist_ok=True)
        hashes_path.write_text(json.dumps(hashes, indent=2, sort_keys=True) + "\n")
        print(f"Hash file: {hashes_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-hashes", type=Path)
    parser.add_argument("--compare-hashes", type=Path)
    args = parser.parse_args()
    run_profile(args.save_hashes, args.compare_hashes)

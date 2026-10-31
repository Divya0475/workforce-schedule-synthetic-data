#!/usr/bin/env python3
"""
evaluate_validator.py - match validator output against ground truth.

Usage:
    python scripts/evaluate_validator.py \\
        --ground-truth  output/starter/ground_truth/ground_truth.csv \\
        --dirty-dir     output/starter/dirty \\
        --clean-dir     output/starter/clean

Exit 0 when clean has zero violations and dirty recall is 100% for all types.
Exit 1 otherwise.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
import time
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Load validate module from the repo root (not as a package).
# ---------------------------------------------------------------------------
def _load_validator() -> object:
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("validate", root / "validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_ids(raw: str) -> set[str]:
    """Parse related_record_ids from CSV cell (JSON array or Python repr)."""
    if not raw or raw in ("[]", ""):
        return set()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return set(parsed)
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, list):
            return set(parsed)
    except (ValueError, SyntaxError):
        pass
    return set()


# ---------------------------------------------------------------------------
# Ground truth helpers
# ---------------------------------------------------------------------------
def _load_ground_truth(path: Path) -> list[dict]:
    import csv
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["_related_set"] = _parse_ids(row.get("related_record_ids", "[]"))
            row["_also_triggers"] = _parse_ids(row.get("also_triggers", "[]"))
            rows.append(row)
    return rows


def _expected_collateral_types(gt_rows: list[dict]) -> set[str]:
    """Collect rule names that appear only in also_triggers across all GT rows."""
    primary = {gt["issue_type"] for gt in gt_rows}
    collateral: set[str] = set()
    for gt in gt_rows:
        collateral.update(gt["_also_triggers"])
    return collateral - primary


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------
def _match(
    gt_rows: list[dict], violations: list[dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Return (true_positives, false_negatives, false_positives).

    A violation matches a GT row when:
      - violation["rule"] == gt["issue_type"]  AND
      - violation["record_id"] is in {gt["record_id"]} | gt["_related_set"]
    """
    matched_viol_indices: set[int] = set()
    matched_gt_indices: set[int] = set()

    gt_by_type: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for i, gt in enumerate(gt_rows):
        gt_by_type[gt["issue_type"]].append((i, gt))

    for vi, viol in enumerate(violations):
        rule = viol.get("rule", "")
        record_id = viol.get("record_id", "")
        for gi, gt in gt_by_type.get(rule, []):
            if gi in matched_gt_indices:
                continue
            gt_ids = {gt["record_id"]} | gt["_related_set"]
            if record_id in gt_ids:
                matched_viol_indices.add(vi)
                matched_gt_indices.add(gi)
                break

    true_positives = [violations[i] for i in sorted(matched_viol_indices)]
    false_positives = [
        violations[i] for i in range(len(violations)) if i not in matched_viol_indices
    ]
    false_negatives = [
        gt_rows[i] for i in range(len(gt_rows)) if i not in matched_gt_indices
    ]
    return true_positives, false_negatives, false_positives


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
TYPES = [
    "DOUBLE_BOOKED",
    "LEAVE_OVERLAP",
    "OVERTIME_CAP_EXCEEDED",
    "SKILL_ABSENT",
    "SWAP_WITHOUT_APPROVAL",
]


def _print_table(
    gt_rows: list[dict],
    violations: list[dict],
    tp: list[dict],
    fn: list[dict],
    fp: list[dict],
    collateral_types: set[str],
) -> bool:
    """Print precision/recall table; return True if all-types recall == 100%."""
    gt_count: dict[str, int] = defaultdict(int)
    for gt in gt_rows:
        gt_count[gt["issue_type"]] += 1

    tp_count: dict[str, int] = defaultdict(int)
    for v in tp:
        tp_count[v["rule"]] += 1

    fp_count: dict[str, int] = defaultdict(int)
    for v in fp:
        fp_count[v["rule"]] += 1

    fn_count: dict[str, int] = defaultdict(int)
    for gt in fn:
        fn_count[gt["issue_type"]] += 1

    print(f"\n{'Type':<30} {'GT':>4} {'TP':>4} {'FP':>4} {'FN':>4} {'Prec':>7} {'Rec':>7}")
    print("-" * 65)
    all_tp = all_fp = all_fn = 0
    all_perfect = True
    for t in TYPES:
        g = gt_count.get(t, 0)
        tp_n = tp_count.get(t, 0)
        fp_n = fp_count.get(t, 0)
        fn_n = fn_count.get(t, 0)
        prec = tp_n / (tp_n + fp_n) if (tp_n + fp_n) else float("nan")
        rec = tp_n / (tp_n + fn_n) if (tp_n + fn_n) else float("nan")
        flag = "" if (rec == 1.0 or g == 0) else " FAIL"
        if rec != 1.0 and g > 0:
            all_perfect = False
        prec_str = f"{prec:.1%}" if prec == prec else "  n/a "
        rec_str = f"{rec:.1%}" if rec == rec else "  n/a "
        print(
            f"{t:<30} {g:>4} {tp_n:>4} {fp_n:>4} {fn_n:>4} "
            f"{prec_str:>7} {rec_str:>7}{flag}"
        )
        all_tp += tp_n
        all_fp += fp_n
        all_fn += fn_n

    # Collateral/other rules not in the 5 primary types
    extra_rules = set(v["rule"] for v in violations) - set(TYPES)
    for t in sorted(extra_rules):
        col_flag = " (expected collateral)" if t in collateral_types else " UNEXPECTED"
        fp_n = fp_count.get(t, 0)
        print(f"  [{t:<27}] {'':>4} {'':>4} {fp_n:>4} {'':>4} {col_flag}")
        if t not in collateral_types:
            all_fp += fp_n
            all_perfect = False

    print("-" * 65)
    g = sum(gt_count.values())
    # Exclude collateral from overall FP so overall recall/precision reflect primary types
    primary_fp = sum(
        fp_count.get(t, 0) for t in set(fp_count) - collateral_types
    )
    overall_prec = all_tp / (all_tp + primary_fp) if (all_tp + primary_fp) else float("nan")
    overall_rec = all_tp / (all_tp + all_fn) if (all_tp + all_fn) else float("nan")
    prec_str = f"{overall_prec:.1%}" if overall_prec == overall_prec else "  n/a "
    rec_str = f"{overall_rec:.1%}" if overall_rec == overall_rec else "  n/a "
    print(
        f"{'OVERALL (primary types)':<30} {g:>4} {all_tp:>4} {primary_fp:>4} {all_fn:>4} "
        f"{prec_str:>7} {rec_str:>7}"
    )
    return all_perfect


def _print_unmatched(fp: list[dict], fn: list[dict], collateral_types: set[str]) -> None:
    primary_fp = [v for v in fp if v.get("rule") not in collateral_types]
    collateral_fp = [v for v in fp if v.get("rule") in collateral_types]

    if primary_fp:
        print(f"\nUnmatched primary violations (unexpected FP: {len(primary_fp)}):")
        for v in primary_fp:
            print(
                f"  rule={v.get('rule')} record_id={v.get('record_id')} "
                f"employee={v.get('employee_id')}"
            )
    else:
        print("\nNo unexpected false positives")

    if collateral_fp:
        print(f"Expected collateral violations (also_triggers, {len(collateral_fp)}):")
        for v in collateral_fp[:5]:
            print(f"  rule={v.get('rule')} record_id={v.get('record_id')}")
        if len(collateral_fp) > 5:
            print(f"  ... and {len(collateral_fp) - 5} more")

    if fn:
        print(f"\nMissed GT rows (false negatives: {len(fn)}):")
        for gt in fn:
            print(
                f"  type={gt['issue_type']} record_id={gt['record_id']} "
                f"employee={gt['employee_id']}"
            )
    else:
        print("No missed GT rows (false negatives: 0)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate validator precision/recall.")
    ap.add_argument("--ground-truth", required=True, help="Path to ground_truth.csv")
    ap.add_argument("--dirty-dir", required=True, help="Dirty dataset directory")
    ap.add_argument("--clean-dir", required=True, help="Clean dataset directory")
    args = ap.parse_args()

    validate = _load_validator()

    print("=== Clean dataset ===")
    t0 = time.time()
    clean_viols = validate.validate_dataset(args.clean_dir)
    clean_time = time.time() - t0
    print(f"Violations: {len(clean_viols)}  Runtime: {clean_time:.2f}s")

    print("\n=== Dirty dataset ===")
    t0 = time.time()
    dirty_viols = validate.validate_dataset(args.dirty_dir)
    dirty_time = time.time() - t0
    print(f"Violations: {len(dirty_viols)}  Runtime: {dirty_time:.2f}s")

    gt_rows = _load_ground_truth(Path(args.ground_truth))
    print(f"Ground truth rows: {len(gt_rows)}")

    collateral_types = _expected_collateral_types(gt_rows)
    if collateral_types:
        print(f"Expected collateral rule types: {sorted(collateral_types)}")

    tp, fn, fp = _match(gt_rows, dirty_viols)

    print("\n=== Precision / Recall table (dirty) ===")
    all_perfect = _print_table(gt_rows, dirty_viols, tp, fn, fp, collateral_types)
    _print_unmatched(fp, fn, collateral_types)

    # Acceptance criteria
    ok = True
    if len(clean_viols) != 0:
        print(f"\nFAIL: clean dataset has {len(clean_viols)} violations (expected 0)")
        ok = False
    else:
        print("\nPASS: Clean dataset has 0 violations")

    if not all_perfect:
        print("FAIL: dirty recall is not 100% for all five primary types")
        ok = False
    else:
        print("PASS: Dirty recall is 100% for all five primary types")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

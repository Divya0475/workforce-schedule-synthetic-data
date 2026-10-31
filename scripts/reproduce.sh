#!/usr/bin/env bash
# Reproducibility script for POSIX / Bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"
echo "==> Project root: $ROOT"

# 1. Virtual Environment Setup
VENV_PYTHON="$ROOT/.venv/bin/python"
if [ ! -f "$VENV_PYTHON" ]; then
    # Fallback to Windows virtualenv path if running under Git Bash / WSL
    if [ -f "$ROOT/.venv/Scripts/python.exe" ]; then
        VENV_PYTHON="$ROOT/.venv/Scripts/python.exe"
    else
        echo "==> Creating virtual environment..."
        python3 -m venv .venv
    fi
fi
echo "==> Using Python: $VENV_PYTHON"

# 2. Install dependencies
echo "==> Installing requirements..."
"$VENV_PYTHON" -m pip install --quiet --upgrade pip
"$VENV_PYTHON" -m pip install --quiet -r requirements.txt

# 3. Run test suite
echo "==> Running pytest suite..."
"$VENV_PYTHON" -m pytest -q

# 4. Generate starter and full datasets with fixed seed
echo "==> Generating starter dataset..."
"$VENV_PYTHON" run_generator.py --profile starter --out output

echo "==> Generating full dataset..."
"$VENV_PYTHON" run_generator.py --profile full --out output

# 5. Run validate.py on all four directories
VAL_TMP="$ROOT/output/tmp_val"
mkdir -p "$VAL_TMP"

validate_dir() {
    local data_dir="$1"
    local expected_zero="$2"
    echo "==> Validating $data_dir..."
    local out_dir="$VAL_TMP/$(basename "$data_dir")_$(date +%s)"
    set +e
    "$VENV_PYTHON" validate.py --data-dir "$data_dir" --out "$out_dir"
    local exit_code=$?
    set -e
    if [ "$expected_zero" -eq 1 ]; then
        if [ "$exit_code" -ne 0 ]; then
            echo "Expected 0 violations on $data_dir, but validate.py exited with $exit_code" >&2
            exit 1
        fi
    else
        if [ "$exit_code" -ne 1 ]; then
            echo "Expected violations on $data_dir (exit code 1), but got $exit_code" >&2
            exit 1
        fi
    fi
}

validate_dir "output/starter/clean" 1
validate_dir "output/starter/dirty" 0
validate_dir "output/full/clean" 1
validate_dir "output/full/dirty" 0

rm -rf "$VAL_TMP"

# 6. Run evaluate_validator.py on starter and full
echo "==> Evaluating starter validator results..."
"$VENV_PYTHON" scripts/evaluate_validator.py --ground-truth output/starter/ground_truth/ground_truth.csv --dirty-dir output/starter/dirty --clean-dir output/starter/clean

echo "==> Evaluating full validator results..."
"$VENV_PYTHON" scripts/evaluate_validator.py --ground-truth output/full/ground_truth/ground_truth.csv --dirty-dir output/full/dirty --clean-dir output/full/clean

echo ""
echo "==> ALL REPRODUCIBILITY CHECKS PASSED!"


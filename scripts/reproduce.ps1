# Reproducibility script for PowerShell
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "==> Project root: $Root"

# 1. Virtual Environment Setup
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "==> Creating virtual environment..."
    python -m venv .venv
}
Write-Host "==> Using Python: $VenvPython"

# 2. Install dependencies
Write-Host "==> Installing requirements..."
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet -r requirements.txt

# 3. Run test suite
Write-Host "==> Running pytest suite..."
& $VenvPython -m pytest -q
if ($LASTEXITCODE -ne 0) {
    Write-Error "pytest failed with exit code $LASTEXITCODE"
    exit 1
}

# 4. Generate starter and full datasets with fixed seed
Write-Host "==> Generating starter dataset..."
& $VenvPython run_generator.py --profile starter --out output
if ($LASTEXITCODE -ne 0) {
    Write-Error "run_generator starter failed with exit code $LASTEXITCODE"
    exit 1
}

Write-Host "==> Generating full dataset..."
& $VenvPython run_generator.py --profile full --out output
if ($LASTEXITCODE -ne 0) {
    Write-Error "run_generator full failed with exit code $LASTEXITCODE"
    exit 1
}

# 5. Run validate.py on all four directories
# Note: validate.py exits 0 on clean dirs (0 violations), exits 1 on dirty dirs (expected violations > 0)
$ValTmp = Join-Path $Root "output\tmp_val"
New-Item -ItemType Directory -Force -Path $ValTmp | Out-Null

function Validate-Dir($dataDir, $expectedViolations) {
    Write-Host "==> Validating $dataDir..."
    $outDir = Join-Path $ValTmp ([System.IO.Path]::GetRandomFileName())
    & $VenvPython validate.py --data-dir $dataDir --out $outDir
    $exitCode = $LASTEXITCODE
    if ($expectedViolations -eq 0) {
        if ($exitCode -ne 0) {
            Write-Error "Expected 0 violations on $dataDir, but validate.py exited with $exitCode"
            exit 1
        }
    } else {
        if ($exitCode -ne 1) {
            Write-Error "Expected violations on $dataDir (exit code 1), but got $exitCode"
            exit 1
        }
    }
}

Validate-Dir "output/starter/clean" 0
Validate-Dir "output/starter/dirty" 18
Validate-Dir "output/full/clean" 0
Validate-Dir "output/full/dirty" 18

Remove-Item -Recurse -Force $ValTmp -ErrorAction SilentlyContinue

# 6. Run evaluate_validator.py on starter and full
Write-Host "==> Evaluating starter validator results..."
& $VenvPython scripts/evaluate_validator.py --ground-truth output/starter/ground_truth/ground_truth.csv --dirty-dir output/starter/dirty --clean-dir output/starter/clean
if ($LASTEXITCODE -ne 0) {
    Write-Error "Starter evaluation failed with exit code $LASTEXITCODE"
    exit 1
}

Write-Host "==> Evaluating full validator results..."
& $VenvPython scripts/evaluate_validator.py --ground-truth output/full/ground_truth/ground_truth.csv --dirty-dir output/full/dirty --clean-dir output/full/clean
if ($LASTEXITCODE -ne 0) {
    Write-Error "Full evaluation failed with exit code $LASTEXITCODE"
    exit 1
}

Write-Host "`n==> ALL REPRODUCIBILITY CHECKS PASSED!"


# Full Dataset Regeneration & Checksums

The full scale dataset (`output/full`) is approximately **86 MB**, exceeding the 50 MB threshold for direct repository inclusion.

Instead, the starter profile (`output/starter`, ~567 KB) is checked into version control, and `output/full` can be regenerated deterministically at any time using the fixed generator seed.

## SHA-256 Checksums

The cryptographic SHA-256 checksums of all 37 files in `output/full` (including CSV tables, ground truth files, and manifest) are recorded in `output/full/SHA256SUMS`.

## How to Regenerate

To regenerate `output/full` with byte-identical output:

```bash
# Using PowerShell
.\.venv\Scripts\python.exe run_generator.py --profile full --out output

# Or using Bash
./.venv/bin/python run_generator.py --profile full --out output
```

To verify the regenerated dataset against the checksums:

```powershell
# In PowerShell:
Get-Content output/full/SHA256SUMS | ForEach-Object {
    $parts = $_ -split '  '
    $expected = $parts[0]
    $file = $parts[1]
    $actual = (Get-FileHash -Path $file -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) {
        Write-Error "Mismatch for $file"
    } else {
        Write-Host "OK: $file"
    }
}
```

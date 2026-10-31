# AI Usage Disclosure

This project was built with LLM assistance. This document explains which tools were used, where AI sped up the work, where I relied on it heavily, and what I reviewed, changed or validated myself. The step-by-step prompt log is in [PROMPTS.md](PROMPTS.md).

## Tools and workflow

I used two AI tools in two roles:

1. **Claude (chat, claude.ai): planner and reviewer.** I used it to break the project into stages, write the prompts I sent to the coding assistant, interpret the command output I pasted back, and decide what to do next. It did not have access to my files. It saw only what I pasted.
2. **VS Code coding assistant: implementer.** It wrote and edited the code, tests, scripts and docs in the repository, and ran commands in my environment. Each stage used a prompt with the same rules: run Python from `.venv`, commit specific paths only (never `git add -A`), do not weaken tests to get green, and report raw output.

Loop for every stage: write a prompt, the assistant implements it, I run the commands myself in PowerShell, I paste the raw output, and the result decides the next prompt.

## Where AI accelerated the work

- Boilerplate and repetitive code: dataclass models, CSV/JSON export, manifest and SHA-256 generation, `scripts/generate_sql.py`.
- Test scaffolding: fixtures, counterexample and "twin" tests (a dataset that must be flagged and one that must pass).
- Vectorising the validator (replacing per-row loops with pandas operations).
- Reproducibility scripts (`reproduce.ps1`, `reproduce.sh`).
- First drafts of README.md and DESIGN.md sections.

## Where I relied on it heavily

- The generator and scheduler logic (stages A, B1, B2, C), and the anomaly injector.
- The validator rule implementations in `validate.py`.
- The evaluation script (`scripts/evaluate_validator.py`).
- The wording of most documentation.

## What I reviewed, changed or validated

These are things I caught or checked, not things the AI volunteered:

| Area | What happened |
|---|---|
| `requirements.txt` encoding | Found the file was UTF-16 with a BOM, which breaks `pip`. Converted it to plain ASCII. |
| Timezones | Found local-timezone behaviour in datetime code. Required UTC-naive arithmetic and a test that hashes output under different `TZ` values. |
| First validator run | The validator crashed on real data (a `KeyError` after a pandas merge). The 73 existing tests had stayed green because none ran `validate.py` on exported data. I required tests that run it on freshly exported starter clean and dirty data. |
| Leave status vocabulary | The clean dataset showed 308 violations. I traced them to the validator using `REQUESTED` while the generator writes `SUBMITTED`. The generator was correct, so I had the validator fixed instead of changing the data. |
| Manager-actor rule | After the status fix, 8 violations remained. From the counts and the design rule I judged they were valid cases (a requester cancelling their own leave, and a manager of another team deciding a manager's leave), so I had the validator's rule corrected instead of changing the generator or skipping the check. Clean data then reached 0 violations. I did not inspect each of the 8 rows individually. |
| Validator hang | Starter runs hung and I interrupted them. The traceback pointed at a per-row `pd.to_datetime` inside `_check_attendance`. After vectorising, starter runs in about 0.3 s and full in about 19-22 s. |
| Anomaly collateral | The dirty dataset had unintended extra violations (`DUPLICATE_ACTIVE_ASSIGNMENT` 6, `REST_VIOLATION` 5, `MAX_CONSECUTIVE_DAYS` 10, `DOUBLE_BOOKED` 4). I required each injected anomaly to be checked with the rule engine so it triggers only the intended rule. The result is 3 of each type, plus 3 `MAX_CONSECUTIVE_DAYS` that I accepted and recorded as `also_triggers`, because a 7-shift week unavoidably breaks the 6-day rule. |
| Validator vs ground truth | Precision and recall are 100% for all five types at starter (15 anomalies) and full (75 anomalies), with 0 violations on both clean datasets. Only `scripts/evaluate_validator.py` reads the ground truth. |
| Independence | A test fails if `validate.py` imports `workforce_generator`, so the validator cannot silently reuse the generator's logic. |
| Reproducibility | I ran `reproduce.ps1` end to end. `git status` stayed clean after regeneration, and a fresh clone passed 93 tests. |
| Line endings | Git warned about LF/CRLF. I added `.gitattributes` so CSV, JSON and `SHA256SUMS` stay byte-identical and the checksums stay valid. |

## What I did not verify

- `scripts/reproduce.sh` was written but **never executed** (I work on Windows). It mirrors `reproduce.ps1`, which was run.
- The AI-written documentation was checked against the CSV headers and row counts, but I did not independently re-derive every statement in DESIGN.md.
- The full dataset (367 MB) is not committed. It is regenerated and verified with `SHA256SUMS`.

## Limits of this disclosure

The early-stage prompts (A, B1) were logged at the time. Later prompts were reconstructed from my chat history with Claude and from commit messages. Where wording is reconstructed, PROMPTS.md says so.

# EVAL.md

## Dataset
`eval/cases.jsonl` contains a small JSONL dataset for prompt regression checks.

Each row:
```json
{"id":"C-001","mode":"support","lang":"auto|ru|en","input":"ticket text","expect_ok":true}
```

## Runner
Command:
```bash
python eval/run_eval.py
```

What it does:
1. Loads JSONL cases.
2. Runs `run_pipeline(...)` for each case using `StubLLMClient`.
3. Tracks per-case `ok`, `attempts`, and errors.
4. Writes a timestamped report into `eval/results/`.

## Current summary fields
- `cases`
- `expectation_match_rate`
- `success_rate`
- `avg_attempts`

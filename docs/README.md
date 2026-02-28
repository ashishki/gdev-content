# gdev-content MVP

## Stack
- Python 3.12
- CLI (`python -m app.run`)
- Optional FastAPI webhook (`python -m app.run --serve`)
- Prompt templates in `prompts/`
- JSON validation with Pydantic
- Eval dataset/runner in `eval/`

## Run
```bash
python -m app.run --input eval/sample.txt --lang auto --mode support
python eval/run_eval.py
```

## Files
```text
prompts/
  system.txt
  user_template.j2
  guidelines.md
app/
  render.py
  run.py
  validators.py
eval/
  sample.txt
  cases.jsonl
  run_eval.py
docs/
  README.md
  PROMPTS.md
  EVAL.md
```


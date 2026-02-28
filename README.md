# gdev-content

MVP implementation for prompt-driven support content generation.

## Quick start
```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install jinja2 pydantic fastapi uvicorn
```

```bash
cp .env.example .env
python -m app.run --input eval/sample.txt --lang auto --mode support
python eval/run_eval.py
```

Docs:
- `docs/README.md`
- `docs/PROMPTS.md`
- `docs/EVAL.md`

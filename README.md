# gdev-content

A multi-stage LLM pipeline that turns incoming support tickets into three structured outputs — a customer-facing reply, an internal team summary, and a prioritised action-item checklist — then routes the result through automated quality control and human approval before delivery.

Built as a portfolio project demonstrating production-grade prompt engineering practices: versioned prompts, schema-enforced output, multi-layer guardrails, adversarial eval coverage, and n8n-native webhook integration.

Status: secondary portfolio asset. Maintenance roadmap: `docs/PROJECT_PLAN.md`.

---

## What it produces

Given a support ticket, the pipeline returns:

```json
{
  "user_reply": {
    "subject": "Кристаллы не зачислены — мы разбираемся",
    "body": "Здравствуйте!  Спасибо, что написали нам. ..."
  },
  "team_summary": [
    "Billing: crystals not credited after purchase in Raid: Shadow Legends.",
    "Transaction reported ~2 hours ago. No PII in ticket."
  ],
  "action_items": [
    {"id": 1, "text": "Look up transaction in billing system by ticket_id", "priority": "P1", "assignee": "[ASSIGNEE]", "due": "[DUE]"}
  ]
}
```

---

## Pipeline

```
INPUT (text, lang, mode)
        │
        ▼
WEBHOOK AUTH + SIZE CHECK
X-Webhook-Secret required · max 8 000 chars
        │
        ▼
PII PRE-MASK
mask_pii() strips emails and phone numbers before any LLM call
        │
        ▼
CLASSIFIER  ──  claude-haiku-4-5  ·  classifier_v1.0.txt  ·  temp=0
        │
        ├── pii_detected=true ──► HARD STOP  (pipeline halted, PII alert sent)
        │
        ├── sensitive_topic=true ──► HARD STOP  (T&S escalation, no reply sent)
        │
        ▼
GENERATOR  ──  claude-sonnet-4-6  ·  generator_v1.0.txt  ·  temp=0
        │
        ▼
POST-GENERATION VALIDATOR
Pydantic schema + banned-pattern regex (EN + RU) + tone check
        │
        ▼
QA JUDGE  ──  claude-sonnet-4-6  ·  quality_gate_v1.0.txt  ·  temp=0
8-point checklist: FORMAT · TONE · STRUCTURE · FACTUALITY
GUARDRAILS · LANGUAGE · LENGTH · COMPLETENESS
score ≥ 0.875 → APPROVE
        │
        ├── APPROVE ──► HUMAN APPROVAL (Telegram inline keyboard ✅/❌)
        │
        ├── REJECT ──► REWRITER (max 2 auto attempts, then ESCALATE)
        │
        └── ESCALATE ──► Human review queue
```

Models by stage:

| Stage | Model | Rationale |
|-------|-------|-----------|
| Classifier | `claude-haiku-4-5-20251001` | Fast, cheap; 5-field schema fits in 256 tokens |
| Generator | `claude-sonnet-4-6` | Quality-sensitive; produces the customer reply |
| QA Judge | `claude-sonnet-4-6` | Same capability level needed to catch subtle failures |
| Rewriter | `claude-sonnet-4-6` | Fixes only flagged issues, preserving passing sections |

---

## Repository structure

```
gdev-content/
├── app/
│   ├── run.py           # Pipeline stages: classify / generate / evaluate / rewrite
│   ├── validators.py    # Pydantic models, banned patterns, PII masking, tone checks
│   └── render.py        # Prompt file loader; Jinja2 for structural parts only
├── prompts/
│   ├── classifier_v1.0.txt
│   ├── generator_v1.0.txt
│   ├── quality_gate_v1.0.txt
│   ├── rewriter_v1.0.txt
│   ├── user_template.j2       # User-turn template (structural fields only)
│   └── guidelines.md          # Human reference; not injected at runtime
├── eval/
│   ├── cases.jsonl            # Stub track: 6 cases (schema + validator tests)
│   ├── cases/                 # LLM track: TC-001 .. TC-020 (full pipeline tests)
│   ├── run_eval.py            # Two-track eval harness
│   └── sample.txt             # Sample ticket for manual testing
├── workflows/
│   └── ticket-to-content-v1.json   # n8n workflow (ready to import)
├── docs/
│   ├── ARCHITECTURE.md
│   ├── PROMPTS.md             # Authoritative prompt spec with examples
│   ├── EVAL.md                # Eval methodology, metrics, thresholds
│   └── REVIEW_NOTES.md        # Senior-level self-review with findings and fixes
└── .env.example
```

---

## Quick start — no API key (stub mode)

The stub client runs the full pipeline logic (classifier, generator, QA judge, rewriter) with deterministic local responses. Use this to confirm plumbing, Pydantic validation, and the eval harness work without spending API tokens.

```bash
python -m venv .venv
source .venv/bin/activate
pip install pydantic jinja2 fastapi uvicorn
```

```bash
cp .env.example .env

# Run a single ticket
python -m app.run --input eval/sample.txt --lang auto --mode support

# Run the stub eval (6 cases, no API calls)
python eval/run_eval.py --provider stub --cases eval/cases.jsonl --out-dir eval/results/
```

Exit code `0` = pipeline produced an approved output. Exit code `2` = pipeline failed or halted.

---

## Setup — real Anthropic API

```bash
pip install pydantic jinja2 fastapi uvicorn lingua-py
```

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export LLM_PROVIDER=anthropic
export PROMPT_VERSION=v1.0
export WEBHOOK_SECRET=your-secret-here

# Run a single ticket
python -m app.run --input eval/sample.txt --lang ru --mode billing
```

Output is JSON on stdout:

```json
{
  "mode": "billing",
  "lang": "ru",
  "error_flag": false,
  "user_reply": { "subject": "...", "body": "..." },
  "team_summary": ["..."],
  "action_items": [{ "id": 1, "text": "...", "priority": "P1", "assignee": "[ASSIGNEE]", "due": "[DUE]" }],
  "metadata": { "provider": "claude-sonnet-4-6", "version": "generator_v1.0", "temperature": "0", "latency_ms": "1340" }
}
```

---

## Running the eval

### Stub track — validates schema and validator logic

```bash
python eval/run_eval.py \
  --provider stub \
  --cases eval/cases.jsonl \
  --out-dir eval/results/
```

Runs 6 cases including one negative case (`C-NEG-001`) that exercises the invalid-JSON retry path and the `ok=false` terminal state. No API calls.

### LLM track — validates actual prompt quality

```bash
ANTHROPIC_API_KEY=sk-ant-... \
LLM_PROVIDER=anthropic \
PROMPT_VERSION=v1.0 \
python eval/run_eval.py \
  --provider anthropic \
  --cases eval/cases/ \
  --out-dir eval/results/
```

Runs all 20 `TC-*.json` cases against the live API. Each case has structured `expected` fields that the harness evaluates per-field (not just pass/fail). Output:

```json
{
  "cases": 20,
  "schema_pass_rate": 1.0,
  "format_pass_rate": 1.0,
  "tone_pass_rate": 0.95,
  "guardrail_pass_rate": 1.0,
  "language_accuracy": 1.0,
  "expectation_match_rate": 0.92,
  "avg_attempts": 1.1,
  "latency_p50_ms": 1340,
  "latency_p95_ms": 3200
}
```

The full report (per-case assertions) is written to `eval/results/eval_report_{timestamp}.json`.

### MVP quality thresholds

| Metric | Threshold |
|--------|-----------|
| `format_pass_rate` | ≥ 95% |
| `tone_pass_rate` | ≥ 85% |
| `guardrail_pass_rate` | **100%** |
| `avg_latency_ms` | ≤ 8 000 ms |
| `cost_per_ticket_usd` | ≤ $0.05 |

`guardrail_pass_rate` must remain 100% across every prompt version bump. Any regression is a blocker.

### Eval test case coverage

| Category | Cases |
|----------|-------|
| Normal billing / support / bug tickets (RU + EN) | TC-001 – TC-007 |
| Hard stop: explicit threats and abuse | TC-008 |
| Feature request (no commitment guardrail) | TC-009 |
| Multilingual (Turkish) | TC-010 |
| Internal escalation | TC-011 |
| PII detection (email in ticket) | TC-012 |
| Angry but not abusive | TC-013 |
| Vague tickets | TC-014 – TC-016 |
| Multi-issue tickets | TC-017 – TC-019 |
| Prompt injection attempt (EN) | TC-020 |

---

## Serving the webhook

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export LLM_PROVIDER=anthropic
export PROMPT_VERSION=v1.0
export WEBHOOK_SECRET=local-dev-secret

python -m app.run --serve --port 8000
```

Send a ticket:

```bash
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: local-dev-secret" \
  -d '{
    "text": "My in-game purchase did not arrive. Please help.",
    "lang": "en",
    "mode": "billing"
  }'
```

A request without the correct `X-Webhook-Secret` header returns HTTP 401.

---

## n8n integration

Import `workflows/ticket-to-content-v1.json` into a self-hosted n8n instance. The workflow:

1. Webhook trigger node — receives the ticket payload
2. HTTP Request node — `POST /webhook` with `X-Webhook-Secret` header
3. IF node — branches on `ok` and `escalated` fields
4. Telegram node — sends approval request with ✅/❌ inline keyboard
5. Delivery stub — webhook / Slack / email output node

The Python webhook response shape matches what n8n expects directly; no transformation node is needed.

For Make (formerly Integromat), use an HTTP module pointed at the same `/webhook` endpoint with the same header and payload structure.

---

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes (for `anthropic` provider) | — | Anthropic API key |
| `LLM_PROVIDER` | No | `stub` | `stub` or `anthropic` |
| `PROMPT_VERSION` | No | `v1.0` | Active prompt version. Resolves `classifier_v1.0.txt`, etc. |
| `WEBHOOK_SECRET` | Yes (for webhook server) | — | Shared secret for `X-Webhook-Secret` header |
| `PIPELINE_MAX_REWRITES` | No | `2` | Maximum automatic rewrite attempts before ESCALATE |
| `FASTAPI_PORT` | No | `8000` | Webhook server port (when using `--serve`) |

---

## Guardrails

Three independent layers prevent unsafe or low-quality output from reaching users.

### Layer 1 — Classifier hard stops (pre-generation)

The classifier runs on every ticket before the generator is called. If either flag is set, the generator is never invoked.

| Signal | Effect |
|--------|--------|
| `pii_detected=true` | Pipeline halted; PII alert logged |
| `sensitive_topic=true` | Pipeline halted; Trust & Safety escalation |

### Layer 2 — Generator prompt instructions (in-generation)

The generator system prompt explicitly prohibits:
- PII echo (emails, phone numbers, real full names)
- Specific currency amounts or refund guarantees
- SLA commitments ("we will resolve this in 24 hours")
- Legal advice or competitor mentions
- NSFW content, discriminatory language
- Hallucinated game titles, dates, or policy statements not present in the original ticket

Injection resistance: the generator prompt instructs the model to treat content between `---TICKET--- / ---END TICKET---` delimiters as opaque user data.

### Layer 3 — Post-generation validator (post-generation, pre-QA)

`validators.py` scans every output field using regex patterns before the QA judge sees the result.

| Check | Pattern |
|-------|---------|
| Email in output | `EMAIL_RE` applied to all output fields |
| Specific amount in `user_reply.body` | `MONEY_RE` applied to `user_reply.body` only |
| Injection artifacts (EN) | `"ignore all previous instructions"`, `"system prompt"`, `"INJECTION SUCCESSFUL"` |
| Injection artifacts (RU) | `"игнорируй все инструкции"`, `"системный промпт"`, `"ты теперь другой"` |
| Blame language | `"your fault"`, `"you should have"`, `"это ваша вина"`, `"вы должны были"` |
| Missing empathy signal | Checks for `"sorry"` / `"understand"` / `"спасибо"` / `"понима"` |

A validator failure triggers the rewrite path with specific error messages for the rewriter prompt.

---

## Prompt versioning

Prompt files are immutable once deployed. Every change produces a new file.

```
prompts/generator_v1.0.txt   ← current production
prompts/generator_v1.1.txt   ← under review
```

The active version is pinned by `PROMPT_VERSION` in `.env`. Old versions are retained for at least 30 days after promotion.

### Bump rules

| Change | Bump |
|--------|------|
| Typo fix, sentence reorder (no semantic change) | Minor: `v1.0` → `v1.1` |
| Clarify a rule, add an example | Minor |
| Add or remove a guardrail (schema unchanged) | Minor |
| Add, remove, or rename a JSON output field | **Major: `v1.x` → `v2.0`** |
| Change scoring threshold in QA judge | Minor (same fields) / Major (verdict logic changes) |

### Required eval gate before promotion

```bash
# Run LLM track against all 20 cases
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... PROMPT_VERSION=v1.1 \
python eval/run_eval.py --provider anthropic --cases eval/cases/ --out-dir eval/results/

# Commit new prompt + eval report + delta metrics together
git add prompts/generator_v1.1.txt eval/results/eval_report_*.json
git commit -m "prompt(generator): v1.0 → v1.1 — soften apology wording

expectation_match_rate: 90% → 92% (+2pp)
guardrail_pass_rate: 100% → 100%
avg_latency_ms: 1340 → 1290"
```

`guardrail_pass_rate` must remain 100%. `expectation_match_rate` must not drop by more than 2 percentage points.

---

## Documentation

| File | Contents |
|------|----------|
| `docs/PROMPTS.md` | Full prompt text for all four stages, output schemas, three end-to-end examples (billing RU, abuse hard stop, prompt injection), versioning process |
| `docs/ARCHITECTURE.md` | Pipeline flow diagram, component descriptions, database schema, roadmap |
| `docs/EVAL.md` | Dataset formats, metric definitions, MVP thresholds, full test case catalog (TC-001–TC-020), instructions for adding new cases |
| `docs/REVIEW_NOTES.md` | Senior-level self-review: 4 Critical and 6 Medium findings, acceptance criteria for each, recommended fix order |

# REVIEW_NOTES.md — gdev-content Pipeline

> Scope: prompt structure, output validation, injection/PII risks, evaluation methodology, reproducibility.
> Reviewer role: Senior Prompt Engineer + Platform Engineer.
> Date: 2026-02-28

---

## Executive Summary

The pipeline architecture is well-conceived: a classifier → generator → QA gate → rewriter → human-approval loop is the right shape for this problem. The adversarial test case design (threats, PII, prompt injection) shows correct security instinct.

Two structural problems undermine the current state.

First, the Python application and the documented architecture describe different systems. `app/run.py` makes a single LLM call with one retry. The multi-stage pipeline (classifier, QA judge, rewriter as separate calls) exists only in `ARCHITECTURE.md` and the `prompts/` files, which are never loaded by the Python code.

Second, the eval harness tests almost nothing real. It runs a hardcoded stub that ignores input text and always returns a valid response. The 20 detailed test cases in `eval/cases/` are never executed by anything.

These two gaps, combined with an exploitable Jinja2 template injection and schema mismatches between the prompt files, validators, and test expectations, bring the production readiness to approximately 4/10.

The items below are ordered by impact and grouped into three tiers.

---

## Findings

---

### CRITICAL

---

#### C-1 — Jinja2 SSTI: user input rendered as a template

**Symptom**
`app/render.py` configures Jinja2 with `autoescape=False`. The raw ticket text is injected into `user_template.j2` as a template variable:

```jinja
Ticket text:
{{ input_text }}
```

A ticket body containing `{{ 7*7 }}` renders to `49` before the LLM ever sees it. With `FileSystemLoader`, expressions like `{% include '/etc/passwd' %}` are syntactically valid at this layer.

**Risk**
Server-side template injection at the Python layer. An attacker controlling `ticket_text` can execute arbitrary Jinja2 expressions in the server process before the LLM receives any input. This is independent of prompt injection defenses.

**Proposed Fix**
Do not render user-supplied data through the Jinja2 engine. Pass `input_text` as a pre-escaped string outside the template context, or replace the template with a plain format string for the user turn and keep Jinja2 only for structural prompt construction.

Minimal change: wrap `input_text` in explicit delimiters inside `user_template.j2` and ensure Jinja2 treats it as data, not executable template:

```jinja
---TICKET---
{{ input_text }}
---END TICKET---
```

Longer-term: consider rendering the structural parts of the user prompt with Jinja2, then appending the user content as a separate string concatenation outside the template engine entirely.

**Files impacted**
- `app/render.py`
- `prompts/user_template.j2`

**Acceptance criteria**
- A ticket containing `{{ 7*7 }}` renders the literal string `{{ 7*7 }}` in the outgoing LLM user message, not `49`.
- A ticket containing `{% include 'system.txt' %}` raises no Jinja2 exception and does not include file contents in the outgoing prompt.
- Existing test cases continue to pass.

---

#### C-2 — Python pipeline does not implement the documented multi-stage architecture

**Symptom**
`app/run.py:run_pipeline()` calls the LLM once (plus one schema-failure retry). There is no classifier call, no separate QA judge call, and no rewriter call. The four stage-specific prompts (`classifier_v1.txt`, `generator_v1.txt`, `quality_gate_v1.txt`, `rewriter_v1.txt`) are never loaded by the Python code. `render_messages()` loads `system.txt` and `user_template.j2` only.

**Risk**
The classifier-based hard stops (PII, threats, sensitive topics) do not execute in any Python-reachable code path, including the FastAPI webhook endpoint. PII tickets and abuse tickets go through the full generator without any triage.

**Proposed Fix**
Implement each pipeline stage as a discrete function: `classify()`, `generate()`, `evaluate()`, `rewrite()`. Wire them in `run_pipeline()` sequentially. Each function loads its own versioned prompt file.

Suggested call sequence:
1. `classify(text)` → calls Haiku with `classifier_v1.txt`; returns `ClassifierResult`
2. Gate on `pii_detected` and `sensitive_topic` before proceeding
3. `generate(text, classifier_result)` → calls Sonnet with `generator_v1.txt`
4. `evaluate(generated, original_ticket)` → calls Sonnet with `quality_gate_v1.txt`; pass the original ticket text so FACTUALITY checks have a reference
5. `rewrite(generated, issues)` → calls Sonnet with `rewriter_v1.txt` if verdict is REJECT

**Files impacted**
- `app/run.py`
- `prompts/classifier_v1.txt`, `prompts/generator_v1.txt`, `prompts/quality_gate_v1.txt`, `prompts/rewriter_v1.txt`
- `app/validators.py` (add `ClassifierResult` model)

**Acceptance criteria**
- A ticket containing a known email address causes `classify()` to return `pii_detected=true` and `run_pipeline()` to return without calling `generate()`.
- A threat ticket (TC-008 content) causes `sensitive_topic=true` and pipeline halt before generation.
- TC-001 produces a `user_reply` in Russian via the full four-stage call sequence.
- All LLM calls are logged with stage name and attempt number.

---

#### C-3 — Eval harness tests the validator, not the prompts

**Symptom**
`eval/run_eval.py` runs exclusively against `StubLLMClient`, which ignores `system_prompt` and `user_prompt` entirely and returns a hardcoded valid JSON string. The 5 cases in `eval/cases.jsonl` all have `expect_ok: true`. A system that always returns `ok=True` achieves 100% `expectation_match_rate`. The 20 detailed test cases in `eval/cases/` are never loaded.

**Risk**
Prompt regressions — any change to `system.txt`, `generator_v1.txt`, or any other prompt file — cannot be detected by running the eval. The eval provides false confidence.

**Proposed Fix**
Two-track eval:

- **Stub track** (current): keep as a unit test of `validate_payload()`. Add at least one `expect_ok: false` case using a constructed invalid payload.
- **LLM track** (new): add a `--provider anthropic` flag to `run_eval.py`. When set, load `eval/cases/TC-*.json`, call the real pipeline, and evaluate each result against the structured `expected` fields using a dedicated assertion function that traverses dot-notation paths (`classifier.type`, `generator.user_reply.body.language`, `qa.verdict`).

**Files impacted**
- `eval/run_eval.py`
- `eval/cases.jsonl` (add one `expect_ok: false` entry)
- `app/run.py` (expose per-stage outputs in `RunResult`)

**Acceptance criteria**
- `python eval/run_eval.py --provider stub` passes with no real API calls and validates schema correctness.
- `python eval/run_eval.py --provider anthropic` executes all 20 TC-*.json cases against the live API and reports per-case pass/fail for each `expected` field.
- TC-008 (hard stop on threats, `expect_ok: false`) is represented in `cases.jsonl` and correctly matched.
- Eval output includes a `per_check` breakdown (format_pass_rate, tone_pass_rate, guardrail_pass_rate) in addition to the current summary fields.

---

#### C-4 — Schema fragmentation: ContentOutput, system.txt, and generator_v1.txt define different shapes

**Symptom**
Three sources describe the generator output shape and they disagree:

- `app/validators.py:ContentOutput` — `user_reply: str` (flat string)
- `prompts/system.txt` — `user_reply: string` (flat)
- `prompts/generator_v1.txt` — implies `user_reply.subject` and `user_reply.body` (nested)
- `eval/cases/TC-001.json:expected` — checks `generator.user_reply.subject` and `generator.user_reply.body.language`

Additionally, `generator_v1.txt` references `error_flag`, `skip_user_reply`, and `translation_en` fields that do not exist in `ContentOutput`. Because `extra="forbid"` is set, any real LLM response following `generator_v1.txt` fails Pydantic validation on first call. Guardrail fields (`error_flag`, `skip_user_reply`) are silently unchecked.

**Risk**
The real LLM following `generator_v1.txt` produces output that fails `ContentOutput` validation on every call. The rewriter preserves a schema that is itself wrong. Hard-stop signals (`error_flag=true`) are never surfaced.

**Proposed Fix**
Designate the Pydantic model as the single source of truth. Extend `ContentOutput` to cover all intended fields, then update the prompt files to show an identical JSON schema:

```python
class UserReply(BaseModel):
    subject: str = Field(max_length=120)
    body: str = Field(min_length=10, max_length=2000)

class ContentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str
    lang: Literal["ru", "en"]
    error_flag: bool = False
    skip_user_reply: bool = False
    user_reply: UserReply
    team_summary: list[str] = Field(min_length=1, max_length=5)
    action_items: list[ActionItem] = Field(min_length=1, max_length=10)
    translation_en: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
```

Update `system.txt` and `generator_v1.txt` to show this schema verbatim. Update all TC-*.json expected field paths to match.

**Files impacted**
- `app/validators.py`
- `prompts/system.txt`
- `prompts/generator_v1.txt`
- `eval/cases/TC-001.json` through `TC-020.json`

**Acceptance criteria**
- The JSON schema comment in `system.txt`, `generator_v1.txt`, and `ContentOutput` are identical.
- A generator response with `error_flag=true` and no `user_reply` passes Pydantic validation (make `user_reply` optional when `error_flag=true`).
- A generator response containing an unexpected field raises a `ValidationError`.
- `lang: Literal["ru", "en"]` limitation is documented explicitly as a known constraint in `ARCHITECTURE.md` with a roadmap entry for multilingual expansion.

---

### MEDIUM

---

#### M-1 — Temperature not set: outputs are non-deterministic by default

**Symptom**
No LLM call in `app/run.py`, the n8n workflow node bodies, or any prompt file specifies `temperature`. Claude defaults to 1.0. The same ticket produces different outputs on consecutive runs.

**Risk**
A ticket that passes QA on run 1 may fail on run 2 with identical input. Eval results are not reproducible. Regression detection between prompt versions requires many samples to be statistically meaningful rather than a simple before/after comparison.

**Proposed Fix**
Set `temperature=0` in all LLM call payloads for classifier, generator, QA judge, and rewriter. Document the chosen value in each stage's prompt file header comment. If the generator produces unacceptably formulaic output at `temperature=0`, raise it to `0.3` and document the tradeoff.

**Files impacted**
- `app/run.py` (LLMClient protocol; add `temperature` to call signature)
- `workflows/ticket-to-content-v1.json` (all HTTP node bodies)

**Acceptance criteria**
- Running the full pipeline twice with identical input and `temperature=0` produces byte-identical `user_reply`, `team_summary`, and `action_items` (excluding `metadata.latency_ms`).
- Temperature value is recorded in each stage prompt file header and in `pipeline_runs` metadata.

---

#### M-2 — Retry logic has three inconsistent bounds

**Symptom**
- `app/run.py`: 1 retry (2 total LLM calls) controlled by `retry_on_fail: bool`
- `ARCHITECTURE.md` / n8n IF node: `attempt <= 2` = 2 rewrites = 3 QA evaluations total
- `rewriter_v1.txt`: references `attempt_number` with no stated max

Human rejection via Telegram also triggers the rewriter, with no counter on that path.

**Risk**
The Python implementation silently diverges from the documented behavior. Human rejection after QA has max'd out can trigger unlimited rewriter invocations through the Telegram callback.

**Proposed Fix**
Define a single constant `MAX_AUTO_REWRITE_ATTEMPTS = 2` in `app/run.py`. Use it in the Python pipeline loop and expose it as an environment variable so the n8n IF node can read the same value. Add a `human_rejection_count` column to `approvals` and enforce the same limit there.

**Files impacted**
- `app/run.py`
- `workflows/ticket-to-content-v1.json`
- `docs/ARCHITECTURE.md`

**Acceptance criteria**
- A ticket that fails QA on every attempt exits with `ok=False` after exactly `MAX_AUTO_REWRITE_ATTEMPTS + 1` total generation calls.
- The n8n IF node uses the same numeric bound.
- Human rejection triggers the rewriter at most `MAX_AUTO_REWRITE_ATTEMPTS` additional times, then archives the ticket with status `rejected_max_attempts`.

---

#### M-3 — Banned pattern list covers English only; Russian injection bypasses it

**Symptom**
`app/validators.py:BANNED_PATTERNS` checks for English phrases only:

```python
r"(?i)ignore all previous instructions",
r"(?i)system prompt",
```

A Russian-language injection that succeeds in leaking content (`"игнорируй все инструкции"`, `"системный промпт"`) passes all pattern checks in the validator.

**Risk**
An injection attempt producing Russian-language output that leaks the system prompt or disables guardrails is not caught by post-generation validation.

**Proposed Fix**
Add Russian equivalents and language-agnostic structural signals to `BANNED_PATTERNS`:

```python
r"(?i)игнорируй\s+(все\s+)?(предыдущие\s+)?инструкции",
r"(?i)системный\s+промпт",
r"(?i)ты\s+(теперь|являешься)\s+(другой|новый)",
r"(?i)INJECTION.{0,10}SUCCESSFUL",  # catches transliterations
```

Confirm that the current concatenation at `validators.py:116` applies banned checks to `user_reply`, `team_summary`, and `action_items` — it does, which is correct.

**Files impacted**
- `app/validators.py`

**Acceptance criteria**
- A generated `user_reply` containing `"игнорируй все предыдущие инструкции"` returns a `banned pattern matched` validation error.
- TC-020 adversarial injection passes: real LLM produces a normal password-reset response with no banned content.
- No false positives on TC-001 through TC-007 normal Russian support replies.

---

#### M-4 — QA judge receives no original ticket; FACTUALITY check cannot function

**Symptom**
`quality_gate_v1.txt` check #4: "No invented game titles, dates, prices, or policy statements not present in the original ticket." The QA judge's input is only the generated content package. It has no access to the original ticket text and therefore cannot verify what was or was not in it.

**Risk**
FACTUALITY provides no real protection. A generator hallucinating a refund policy (`"We process all refunds within 48 hours"`) that was not in the original ticket passes FACTUALITY because the judge cannot detect the invention.

**Proposed Fix**
Pass the original ticket text to the QA judge. In `quality_gate_v1.txt`, add an explicit reference section:

```
ORIGINAL TICKET (ground truth for FACTUALITY check):
---
{{ original_ticket }}
---

GENERATED OUTPUT TO EVALUATE:
---
{{ generated_json }}
---
```

**Files impacted**
- `prompts/quality_gate_v1.txt`
- `app/run.py` (thread original ticket text through to the evaluate stage)
- `workflows/ticket-to-content-v1.json` (Quality Gate node body)

**Acceptance criteria**
- A generated reply claiming a refund SLA not present in the original ticket results in FACTUALITY=fail and verdict=REJECT.
- TC-001 still produces verdict=APPROVE when the reply correctly avoids inventing policies.
- The original ticket text appears in the QA judge's user message under a clearly labelled section.

---

#### M-5 — PII masking is documented but not implemented

**Symptom**
`ARCHITECTURE.md` states: "pipeline_runs.input_json маскирует PII поля." No masking code exists anywhere in the Python application. `pipeline_runs.input_json` would store raw ticket text including PII.

**Risk**
Data minimization violation. A ticket containing an email address (TC-012 scenario) is stored verbatim in the audit log.

**Proposed Fix**
Add a `mask_pii(text: str) -> str` function to `app/validators.py` that substitutes matches of `EMAIL_RE` with `[EMAIL]` and `PHONE_RE` (to be added) with `[PHONE]` before any text is written to storage. Apply `mask_pii()` in `run_pipeline()` before the `pipeline_runs` write. The unmasked text should not appear in any log line.

**Files impacted**
- `app/validators.py` (add `mask_pii()` and `PHONE_RE`)
- `app/run.py` (apply before storage calls)
- `docs/ARCHITECTURE.md` (clarify: `raw_text` column is masked; original is not retained)

**Acceptance criteria**
- A ticket body containing `sergey.petrov@gmail.com` has the address replaced with `[EMAIL]` in `pipeline_runs.input_json`.
- The original unmasked email does not appear in any log line.
- TC-012 `pipeline.pii_alert_triggered=true` and no email in any output field.

---

#### M-6 — Webhook endpoint has no authentication

**Symptom**
The FastAPI `POST /webhook` endpoint in `app/run.py` and the n8n webhook node accept any request with no authentication header, API key, or IP allowlist.

**Risk**
Anyone who can reach the endpoint can submit arbitrary tickets, exhaust Anthropic API quota, trigger Telegram approval messages, and consume storage.

**Proposed Fix**
Add a shared-secret check as a FastAPI dependency on the route. For n8n, enable built-in webhook header authentication. Load the secret from `WEBHOOK_SECRET` environment variable. Document in `.env.example`.

**Files impacted**
- `app/run.py`
- `workflows/ticket-to-content-v1.json`
- `.env.example`

**Acceptance criteria**
- A request without the correct `X-Webhook-Secret` header returns HTTP 401.
- A request with the correct header proceeds normally.
- The secret value is loaded from environment, never hardcoded.

---

### NICE-TO-HAVE

---

#### N-1 — Guidelines injected into user turn duplicates system turn content

**Symptom**
`render_messages()` loads `guidelines.md` and injects it verbatim into the user message. `system.txt` already contains equivalent rules. The model receives the same constraints in both turns.

**Proposed Fix**
Remove the `{{ guidelines }}` block from `user_template.j2`. Keep all behavioral rules in `system.txt`. Repurpose `guidelines.md` as human-readable documentation only — not a runtime artifact.

**Files impacted**
- `prompts/user_template.j2`, `app/render.py`, `prompts/guidelines.md`

**Acceptance criteria**
- `render_messages()` returns a user prompt that does not contain text from `guidelines.md`.
- Token count per generator call decreases by approximately the length of `guidelines.md`.

---

#### N-2 — Language detection uses character counting; misclassifies short and mixed text

**Symptom**
`detect_language()` counts Cyrillic vs Latin characters. Any short or mixed-language reply is unreliable. A reply of `"OK, принято"` has 2 Latin and 7 Cyrillic → `ru`. A reply of `"Yes спасибо"` is also mis-scored. This triggers false language-mismatch validation errors and unnecessary retries.

**Proposed Fix**
Replace character counting with `langdetect` or `lingua-py`. Fall back to character counting only for strings shorter than 10 characters.

**Files impacted**
- `app/validators.py`, `requirements.txt` / `pyproject.toml`

**Acceptance criteria**
- `detect_language("Спасибо за обращение. We are working on it.")` returns `ru`.
- `detect_language("Thank you for contacting us.")` returns `en`.
- `detect_language("OK")` returns `en` without error.

---

#### N-3 — QA scoring: all-NA case produces score=1.0 and APPROVE

**Symptom**
If all 8 checks are `na`, `total_applicable=8`, `passed=8`, `overall_score=1.0` → APPROVE. This is reachable for empty or trivially short outputs.

**Proposed Fix**
Add a floor: if `total_applicable < 4`, the verdict is ESCALATE rather than APPROVE or REJECT.

**Files impacted**
- `prompts/quality_gate_v1.txt`

**Acceptance criteria**
- A generated output where FORMAT=pass and all other checks are NA produces verdict=ESCALATE, not APPROVE.

---

#### N-4 — MONEY_RE fires on internal fields, producing false positives

**Symptom**
`_banned_content_errors()` applies `MONEY_RE` to the concatenated string of `user_reply + team_summary + action_items`. A team summary bullet like `"User reported missing USD payment"` can trigger a false positive if digits appear nearby.

**Proposed Fix**
Apply `MONEY_RE` only to `user_reply.body`. Team-internal fields (`team_summary`, `action_items`) may legitimately reference amounts for investigative accuracy.

**Files impacted**
- `app/validators.py`

**Acceptance criteria**
- A `team_summary` bullet containing `"Transaction: $49.99 per payment record"` does not trigger a banned content error.
- A `user_reply` body containing `"We will refund $49.99"` still triggers a banned content error.

---

#### N-5 — Prompt files are mutable at runtime; edits take effect immediately

**Symptom**
`render.py:load_text()` reads prompt files from disk at call time. A change to `system.txt` affects all future requests immediately with no rollout boundary or audit trail.

**Proposed Fix**
Pin the active prompt version in configuration (e.g. `PROMPT_VERSION=v1.0` in `.env`). `load_text()` resolves the versioned filename (`system_v1.0.txt`) rather than a mutable `system.txt`. Promotion to a new version requires a config change. Old and new versions coexist in `prompts/`.

**Files impacted**
- `app/render.py`, `prompts/` (versioned filenames), `.env.example`

**Acceptance criteria**
- Editing a prompt file does not affect active requests until `PROMPT_VERSION` is updated.
- The active version is recorded in `pipeline_runs.input_json` metadata for every request.

---

## Summary Table

| ID | Tier | Area | Effort |
|----|------|------|--------|
| C-1 | Critical | Security / Injection | Small |
| C-2 | Critical | Architecture / Pipeline | Large |
| C-3 | Critical | Evaluation | Medium |
| C-4 | Critical | Schema / Prompts | Medium |
| M-1 | Medium | Reproducibility | Trivial |
| M-2 | Medium | Retry Logic | Small |
| M-3 | Medium | Security / Injection | Small |
| M-4 | Medium | Prompt Design / QA | Small |
| M-5 | Medium | PII / Compliance | Small |
| M-6 | Medium | Security / Auth | Small |
| N-1 | Nice-to-have | Token Efficiency | Trivial |
| N-2 | Nice-to-have | Validation Quality | Small |
| N-3 | Nice-to-have | QA Scoring | Trivial |
| N-4 | Nice-to-have | Validation Quality | Trivial |
| N-5 | Nice-to-have | Reproducibility | Small |

Effort key: Trivial = < 1h · Small = 1–4h · Medium = half day · Large = 1–2 days

---

## Recommended Fix Order

Address items in this sequence to avoid rework:

1. **C-1** — Fix Jinja2 SSTI before any external exposure of the app
2. **C-4** — Align `ContentOutput` schema with `generator_v1.txt`; all subsequent work depends on a stable schema
3. **C-2** — Implement the multi-stage pipeline in Python; this is a prerequisite for a meaningful eval
4. **M-1** — Set `temperature=0`; this is a prerequisite for reproducible eval results
5. **C-3** — Wire TC-*.json cases into the eval harness with real LLM; now meaningful with C-2 and M-1 done
6. **M-4** — Pass original ticket to QA judge; easy add once C-2 is done
7. **M-5** — Add PII masking before any real data flows through the pipeline
8. **M-3** — Extend banned patterns to Russian
9. **M-2** — Consolidate retry bounds
10. **M-6** — Add webhook authentication before exposing to any external network

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.render import load_stage_prompt, render_messages
from app.validators import (
    ClassifierResult,
    QAResult,
    ValidationResult,
    detect_language,
    mask_pii,
    validate_payload,
)


CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"
GENERATOR_MODEL = "claude-sonnet-4-6"
QUALITY_GATE_MODEL = "claude-sonnet-4-6"
REWRITER_MODEL = "claude-sonnet-4-6"

CLASSIFIER_MAX_TOKENS = 256
GENERATOR_MAX_TOKENS = 2048
QUALITY_GATE_MAX_TOKENS = 512
REWRITER_MAX_TOKENS = 2048

TEMPERATURE = 0.0
MAX_SCHEMA_RETRIES = 1
MAX_AUTO_REWRITE_ATTEMPTS = int(os.getenv("PIPELINE_MAX_REWRITES", "2"))


@dataclass
class LLMResponse:
    text: str
    model: str
    usage_input_tokens: int = 0
    usage_output_tokens: int = 0


class LLMClient(Protocol):
    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        stage: str,
    ) -> LLMResponse:
        ...


class StubLLMClient:
    """Deterministic local stub for development and stub eval track."""

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        stage: str,
    ) -> LLMResponse:
        lower = user_prompt.lower()

        if stage == "classifier":
            payload = {
                "type": "support",
                "urgency": "medium",
                "language": "ru" if re.search(r"[а-яё]", user_prompt, re.I) else "en",
                "pii_detected": bool(re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", user_prompt)),
                "sensitive_topic": bool(re.search(r"(взломаю|i know where you are|kill|угроза|threat)", lower)),
            }
            if "charged" in lower or "payment" in lower or "списал" in lower:
                payload["type"] = "billing"
                payload["urgency"] = "high"
            if "bug" in lower or "crash" in lower:
                payload["type"] = "bug"
            if "feature" in lower or "add" in lower:
                payload["type"] = "feature_request"
            if "scam" in lower or "phishing" in lower:
                payload["type"] = "abuse"
            return LLMResponse(text=json.dumps(payload, ensure_ascii=False), model=model)

        if stage in {"generator", "rewriter"}:
            if "[stub_force_invalid_json]" in lower:
                return LLMResponse(text="{invalid-json", model=model)

            lang_match = re.search(r'- lang:\s*"(?P<lang>ru|en)"', user_prompt)
            mode_match = re.search(r'- mode:\s*"(?P<mode>[a-z_]+)"', user_prompt)
            lang = lang_match.group("lang") if lang_match else "en"
            mode = mode_match.group("mode") if mode_match else "support"

            if "sensitive_topic=true" in lower:
                payload = {
                    "mode": mode,
                    "lang": lang,
                    "error_flag": True,
                    "skip_user_reply": True,
                    "user_reply": None,
                    "team_summary": ["Sensitive topic detected. Escalate to Trust & Safety."],
                    "action_items": [
                        {
                            "id": 1,
                            "text": "Escalate to Trust & Safety queue",
                            "priority": "P1",
                            "assignee": "[ASSIGNEE]",
                            "due": "[DUE]",
                        }
                    ],
                    "translation_en": None,
                    "metadata": {"provider": "stub", "version": "v1", "temperature": str(TEMPERATURE)},
                }
                return LLMResponse(text=json.dumps(payload, ensure_ascii=False), model=model)

            if lang == "ru":
                subject = "Мы получили ваше обращение"
                body = (
                    "Здравствуйте! Спасибо за обращение. Нам жаль, что вы столкнулись с этой проблемой. "
                    "Мы уже передали запрос в профильную команду и вернемся с обновлением."
                )
            else:
                subject = "We received your request"
                body = (
                    "Hello! Thank you for reaching out. We are sorry you encountered this issue. "
                    "We have escalated your case to the responsible team and will follow up with an update."
                )

            payload = {
                "mode": mode,
                "lang": lang,
                "error_flag": False,
                "skip_user_reply": False,
                "user_reply": {"subject": subject, "body": body},
                "team_summary": [
                    "User reported an issue that requires investigation.",
                    "No prohibited content detected in the generated response.",
                ],
                "action_items": [
                    {
                        "id": 1,
                        "text": "Collect logs and account context",
                        "priority": "P1",
                        "assignee": "[ASSIGNEE]",
                        "due": "[DUE]",
                    },
                    {
                        "id": 2,
                        "text": "Assign investigation owner",
                        "priority": "P2",
                        "assignee": "[ASSIGNEE]",
                        "due": "[DUE]",
                    },
                ],
                "translation_en": None,
                "metadata": {"provider": "stub", "version": "v1", "temperature": str(TEMPERATURE)},
            }
            return LLMResponse(text=json.dumps(payload, ensure_ascii=False), model=model)

        if stage == "quality_gate":
            verdict = "APPROVE"
            payload = {
                "verdict": verdict,
                "overall_score": 1.0,
                "checks": {
                    "format": "pass",
                    "tone": "pass",
                    "structure": "pass",
                    "factuality": "pass",
                    "guardrails": "pass",
                    "language": "pass",
                    "length": "pass",
                    "completeness": "pass",
                },
                "issues": [],
                "rewrite_needed": False,
            }
            return LLMResponse(text=json.dumps(payload, ensure_ascii=False), model=model)

        raise ValueError(f"Unsupported stage: {stage}")


class AnthropicLLMClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
        stage: str,
    ) -> LLMResponse:
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            data=json.dumps(payload).encode("utf-8"),
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anthropic API HTTP error at stage={stage}: {exc.code} {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"Anthropic API error at stage={stage}: {exc}") from exc

        parts = body.get("content", [])
        text = "".join(part.get("text", "") for part in parts if part.get("type") == "text")
        usage = body.get("usage", {})
        return LLMResponse(
            text=text,
            model=body.get("model", model),
            usage_input_tokens=int(usage.get("input_tokens", 0)),
            usage_output_tokens=int(usage.get("output_tokens", 0)),
        )


@dataclass
class RunResult:
    ok: bool
    attempts: int
    output: dict | None
    errors: list[str]
    stage_outputs: dict[str, Any] = field(default_factory=dict)
    escalated: bool = False
    pii_halt: bool = False
    sensitive_halt: bool = False
    pii_alert_triggered: bool = False
    auto_reply_blocked: bool = False
    latency_ms: int = 0


def _log_stage(stage: str, attempt: int, event: str, **fields: Any) -> None:
    payload = {"stage": stage, "attempt": attempt, "event": event, **fields}
    print(json.dumps(payload, ensure_ascii=False))


def _rewrite_prompt(original_prompt: str, errors: list[str]) -> str:
    return (
        f"{original_prompt}\n\n"
        "REWRITE PASS:\n"
        "The previous JSON failed validation.\n"
        f"Errors: {json.dumps(errors, ensure_ascii=False)}\n"
        "Return corrected JSON only."
    )


def _call_with_schema_retry(
    *,
    llm: LLMClient,
    stage: str,
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_tokens: int,
    parser,
    retries: int = MAX_SCHEMA_RETRIES,
) -> tuple[Any | None, list[str], int, str]:
    attempts = 0
    errors: list[str] = []
    raw = ""
    current_prompt = user_prompt

    for idx in range(retries + 1):
        attempts += 1
        _log_stage(stage, attempts, "stage_start")
        resp = llm.generate(
            system_prompt=system_prompt,
            user_prompt=current_prompt,
            model=model,
            temperature=TEMPERATURE,
            max_tokens=max_tokens,
            stage=stage,
        )
        raw = resp.text
        try:
            parsed = parser(raw)
            _log_stage(stage, attempts, "stage_complete")
            return parsed, [], attempts, raw
        except Exception as exc:
            error = f"schema: {exc}"
            errors.append(error)
            _log_stage(stage, attempts, "validation_error", error=error)
            if idx < retries:
                current_prompt = _rewrite_prompt(user_prompt, errors)

    return None, errors, attempts, raw


def _classifier_user_prompt(text: str) -> str:
    return f"---TICKET---\n{text}\n---END TICKET---"


def classify(*, text: str, llm: LLMClient) -> tuple[ClassifierResult | None, list[str], int, str]:
    system_prompt = load_stage_prompt("classifier")

    def parse(raw: str) -> ClassifierResult:
        return ClassifierResult.model_validate_json(raw)

    return _call_with_schema_retry(
        llm=llm,
        stage="classifier",
        system_prompt=system_prompt,
        user_prompt=_classifier_user_prompt(text),
        model=CLASSIFIER_MODEL,
        max_tokens=CLASSIFIER_MAX_TOKENS,
        parser=parse,
    )


def generate(
    *,
    text: str,
    mode: str,
    lang: str,
    classifier: ClassifierResult,
    llm: LLMClient,
) -> tuple[ValidationResult, int, str]:
    system_prompt = load_stage_prompt("generator")
    structural = (
        f'Generate structured output for ticket.\n- mode: "{mode}"\n- lang: "{lang}"\n'
        f"- classifier_type: \"{classifier.type}\"\n"
        f"- classifier_urgency: \"{classifier.urgency}\"\n"
        f"- pii_detected={str(classifier.pii_detected).lower()}\n"
        f"- sensitive_topic={str(classifier.sensitive_topic).lower()}"
    )
    user_prompt = f"{structural}\n\n---TICKET---\n{text}\n---END TICKET---"

    attempts = 0
    last_validation = ValidationResult(data=None, errors=["schema: no attempts made"])
    raw = ""

    for idx in range(MAX_SCHEMA_RETRIES + 1):
        attempts += 1
        _log_stage("generator", attempts, "stage_start")
        resp = llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=GENERATOR_MODEL,
            temperature=TEMPERATURE,
            max_tokens=GENERATOR_MAX_TOKENS,
            stage="generator",
        )
        raw = resp.text
        last_validation = validate_payload(raw, expected_lang=lang)
        if last_validation.ok:
            _log_stage("generator", attempts, "stage_complete")
            return last_validation, attempts, raw
        _log_stage("generator", attempts, "validation_error", errors=last_validation.errors)
        if idx < MAX_SCHEMA_RETRIES:
            user_prompt = _rewrite_prompt(user_prompt, last_validation.errors)

    return last_validation, attempts, raw


def evaluate(*, generated_json: str, original_ticket: str, llm: LLMClient) -> tuple[QAResult | None, list[str], int, str]:
    system_prompt = load_stage_prompt("quality_gate")
    user_prompt = (
        "ORIGINAL TICKET (ground truth for FACTUALITY check):\n"
        "---\n"
        f"{original_ticket}\n"
        "---\n\n"
        "GENERATED OUTPUT TO EVALUATE:\n"
        "---\n"
        f"{generated_json}\n"
        "---"
    )

    def parse(raw: str) -> QAResult:
        return QAResult.model_validate_json(raw)

    return _call_with_schema_retry(
        llm=llm,
        stage="quality_gate",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=QUALITY_GATE_MODEL,
        max_tokens=QUALITY_GATE_MAX_TOKENS,
        parser=parse,
    )


def rewrite(
    *,
    original_ticket: str,
    generated_json: str,
    issues: list[str],
    attempt_number: int,
    llm: LLMClient,
) -> tuple[ValidationResult, int, str]:
    system_prompt = load_stage_prompt("rewriter")
    user_prompt = (
        f"attempt_number={attempt_number}\n"
        "ORIGINAL TICKET:\n"
        "---\n"
        f"{original_ticket}\n"
        "---\n\n"
        "FAILED OUTPUT:\n"
        "---\n"
        f"{generated_json}\n"
        "---\n\n"
        "ISSUES:\n"
        f"{json.dumps(issues, ensure_ascii=False)}"
    )

    attempts = 0
    last_validation = ValidationResult(data=None, errors=["schema: no attempts made"])
    raw = ""

    for idx in range(MAX_SCHEMA_RETRIES + 1):
        attempts += 1
        _log_stage("rewriter", attempts, "stage_start")
        resp = llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=REWRITER_MODEL,
            temperature=TEMPERATURE,
            max_tokens=REWRITER_MAX_TOKENS,
            stage="rewriter",
        )
        raw = resp.text
        last_validation = validate_payload(raw, expected_lang="ru" if re.search(r"[а-яё]", original_ticket, re.I) else "en")
        if last_validation.ok:
            _log_stage("rewriter", attempts, "stage_complete")
            return last_validation, attempts, raw
        _log_stage("rewriter", attempts, "validation_error", errors=last_validation.errors)
        if idx < MAX_SCHEMA_RETRIES:
            user_prompt = _rewrite_prompt(user_prompt, last_validation.errors)

    return last_validation, attempts, raw


def run_pipeline(
    *,
    text: str,
    mode: str,
    lang: str,
    llm: LLMClient,
) -> RunResult:
    run_id = str(uuid.uuid4())
    start = time.perf_counter()
    masked_text = mask_pii(text)

    total_generate_attempts = 0
    stage_outputs: dict[str, Any] = {"run_id": run_id}

    classifier_result, classifier_errors, classifier_attempts, classifier_raw = classify(text=masked_text, llm=llm)
    stage_outputs["classifier_raw"] = classifier_raw
    total_generate_attempts += 0

    if classifier_result is None:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return RunResult(
            ok=False,
            attempts=0,
            output=None,
            errors=classifier_errors,
            stage_outputs=stage_outputs,
            latency_ms=latency_ms,
        )

    stage_outputs["classifier"] = classifier_result.model_dump()

    if classifier_result.pii_detected:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return RunResult(
            ok=False,
            attempts=0,
            output=None,
            errors=["pii_detected=true: pipeline halted before generation"],
            stage_outputs=stage_outputs,
            pii_halt=True,
            pii_alert_triggered=True,
            auto_reply_blocked=True,
            latency_ms=latency_ms,
        )

    if classifier_result.sensitive_topic:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return RunResult(
            ok=False,
            attempts=0,
            output=None,
            errors=["sensitive_topic=true: pipeline halted before generation"],
            stage_outputs=stage_outputs,
            sensitive_halt=True,
            auto_reply_blocked=True,
            escalated=True,
            latency_ms=latency_ms,
        )

    generated_validation, gen_attempts, generated_raw = generate(
        text=masked_text,
        mode=mode,
        lang=lang,
        classifier=classifier_result,
        llm=llm,
    )
    total_generate_attempts += gen_attempts
    stage_outputs["generator_raw"] = generated_raw

    if not generated_validation.ok or generated_validation.data is None:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return RunResult(
            ok=False,
            attempts=total_generate_attempts,
            output=None,
            errors=generated_validation.errors,
            stage_outputs=stage_outputs,
            latency_ms=latency_ms,
        )

    generated = generated_validation.data
    stage_outputs["generator"] = generated.model_dump()

    qa_result, qa_errors, qa_attempts, qa_raw = evaluate(
        generated_json=generated.model_dump_json(ensure_ascii=False),
        original_ticket=masked_text,
        llm=llm,
    )
    stage_outputs["qa_raw"] = qa_raw

    if qa_result is None:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return RunResult(
            ok=False,
            attempts=total_generate_attempts,
            output=generated.model_dump(),
            errors=qa_errors,
            stage_outputs=stage_outputs,
            latency_ms=latency_ms,
        )

    stage_outputs["qa"] = qa_result.model_dump()

    if qa_result.verdict == "APPROVE":
        latency_ms = int((time.perf_counter() - start) * 1000)
        output = generated.model_dump()
        output.setdefault("metadata", {})
        output["metadata"]["latency_ms"] = str(latency_ms)
        output["metadata"]["temperature"] = str(TEMPERATURE)
        return RunResult(
            ok=True,
            attempts=total_generate_attempts,
            output=output,
            errors=[],
            stage_outputs=stage_outputs,
            latency_ms=latency_ms,
            auto_reply_blocked=bool(output.get("skip_user_reply", False)),
        )

    if qa_result.verdict == "ESCALATE":
        latency_ms = int((time.perf_counter() - start) * 1000)
        return RunResult(
            ok=False,
            attempts=total_generate_attempts,
            output=generated.model_dump(),
            errors=["qa verdict ESCALATE"],
            stage_outputs=stage_outputs,
            escalated=True,
            latency_ms=latency_ms,
        )

    current_generated_raw = generated.model_dump_json(ensure_ascii=False)
    current_generated = generated

    for rewrite_idx in range(1, MAX_AUTO_REWRITE_ATTEMPTS + 1):
        rewritten_validation, rewrite_attempts, rewritten_raw = rewrite(
            original_ticket=masked_text,
            generated_json=current_generated_raw,
            issues=qa_result.issues,
            attempt_number=rewrite_idx,
            llm=llm,
        )
        total_generate_attempts += rewrite_attempts
        stage_outputs.setdefault("rewriter", []).append({"attempt": rewrite_idx, "raw": rewritten_raw})

        if not rewritten_validation.ok or rewritten_validation.data is None:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return RunResult(
                ok=False,
                attempts=total_generate_attempts,
                output=None,
                errors=rewritten_validation.errors,
                stage_outputs=stage_outputs,
                latency_ms=latency_ms,
            )

        current_generated = rewritten_validation.data
        current_generated_raw = current_generated.model_dump_json(ensure_ascii=False)
        stage_outputs["generator"] = current_generated.model_dump()

        qa_result, qa_errors, qa_attempts, qa_raw = evaluate(
            generated_json=current_generated_raw,
            original_ticket=masked_text,
            llm=llm,
        )
        stage_outputs["qa_raw"] = qa_raw

        if qa_result is None:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return RunResult(
                ok=False,
                attempts=total_generate_attempts,
                output=current_generated.model_dump(),
                errors=qa_errors,
                stage_outputs=stage_outputs,
                latency_ms=latency_ms,
            )
        stage_outputs["qa"] = qa_result.model_dump()
        if qa_result.verdict == "APPROVE":
            latency_ms = int((time.perf_counter() - start) * 1000)
            output = current_generated.model_dump()
            output.setdefault("metadata", {})
            output["metadata"]["latency_ms"] = str(latency_ms)
            output["metadata"]["temperature"] = str(TEMPERATURE)
            return RunResult(
                ok=True,
                attempts=total_generate_attempts,
                output=output,
                errors=[],
                stage_outputs=stage_outputs,
                latency_ms=latency_ms,
                auto_reply_blocked=bool(output.get("skip_user_reply", False)),
            )

    latency_ms = int((time.perf_counter() - start) * 1000)
    return RunResult(
        ok=False,
        attempts=total_generate_attempts,
        output=current_generated.model_dump() if current_generated else None,
        errors=[f"qa rejected after max rewrites ({MAX_AUTO_REWRITE_ATTEMPTS})"],
        stage_outputs=stage_outputs,
        escalated=True,
        latency_ms=latency_ms,
    )


def _resolve_lang(requested_lang: str, text: str) -> str:
    if requested_lang == "auto":
        return detect_language(text)
    return requested_lang


def _build_client(provider_override: str | None = None) -> LLMClient:
    provider = (provider_override or os.getenv("LLM_PROVIDER", "stub")).lower()
    if provider == "stub":
        return StubLLMClient()
    if provider == "anthropic":
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for LLM_PROVIDER=anthropic")
        return AnthropicLLMClient(api_key=api_key)
    raise RuntimeError(f"Unsupported LLM provider: {provider}")


def _run_cli(args: argparse.Namespace) -> int:
    input_text = Path(args.input).read_text(encoding="utf-8")
    lang = _resolve_lang(args.lang, input_text)
    llm = _build_client()
    result = run_pipeline(text=input_text, mode=args.mode, lang=lang, llm=llm)

    if result.ok and result.output is not None:
        print(json.dumps(result.output, ensure_ascii=False, indent=2))
        return 0

    failure = {
        "ok": False,
        "attempts": result.attempts,
        "errors": result.errors,
        "escalated": result.escalated,
        "pii_halt": result.pii_halt,
        "sensitive_halt": result.sensitive_halt,
        "latency_ms": result.latency_ms,
    }
    print(json.dumps(failure, ensure_ascii=False, indent=2))
    return 2


def _run_api(port: int) -> int:
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException
        from pydantic import BaseModel
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("FastAPI mode requires `fastapi` and `uvicorn` installed.") from exc

    class WebhookRequest(BaseModel):
        text: str
        lang: str = "auto"
        mode: str = "support"

    def _verify_webhook_secret(x_webhook_secret: str | None = Header(default=None)) -> None:
        expected = os.getenv("WEBHOOK_SECRET", "")
        if not expected or x_webhook_secret != expected:
            raise HTTPException(status_code=401, detail="unauthorized")

    app = FastAPI(title="gdev-content")
    llm = _build_client()

    @app.post("/webhook", dependencies=[Depends(_verify_webhook_secret)])
    def webhook(payload: WebhookRequest) -> dict:
        resolved_lang = _resolve_lang(payload.lang, payload.text)
        result = run_pipeline(text=payload.text, mode=payload.mode, lang=resolved_lang, llm=llm)
        return {
            "ok": result.ok,
            "attempts": result.attempts,
            "errors": result.errors,
            "output": result.output,
            "escalated": result.escalated,
            "pii_halt": result.pii_halt,
            "sensitive_halt": result.sensitive_halt,
        }

    uvicorn.run(app, host="0.0.0.0", port=port)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="gdev-content pipeline runner")
    parser.add_argument("--input", default="eval/sample.txt", help="Path to input ticket text file")
    parser.add_argument("--lang", default="auto", choices=["auto", "ru", "en"])
    parser.add_argument("--mode", default="support", help="Support mode label for prompt context")
    parser.add_argument("--serve", action="store_true", help="Run FastAPI webhook server")
    parser.add_argument("--port", type=int, default=8000, help="Webhook server port")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.serve:
        return _run_api(args.port)
    return _run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())

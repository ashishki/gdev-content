from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.render import render_messages
from app.validators import ValidationResult, detect_language, validate_payload


class LLMClient(Protocol):
    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        ...


class StubLLMClient:
    """Deterministic local stub for MVP development and eval."""

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        lang_match = re.search(r'lang:\s*"(?P<lang>ru|en)"', user_prompt)
        mode_match = re.search(r'mode:\s*"(?P<mode>[a-z_]+)"', user_prompt)
        lang = lang_match.group("lang") if lang_match else "en"
        mode = mode_match.group("mode") if mode_match else "support"
        if lang == "ru":
            user_reply = (
                "Спасибо за обращение. Нам очень жаль, что вы столкнулись с этой проблемой. "
                "Мы уже передали запрос в профильную команду и сообщим о результате."
            )
            team_summary = [
                "User reported an issue via support intake.",
                "Impact exists and needs triage.",
                "Escalation to responsible queue is required.",
            ]
        else:
            user_reply = (
                "Thank you for reaching out. We are sorry you faced this issue. "
                "We have escalated it to the responsible team and will share an update soon."
            )
            team_summary = [
                "User reported a support issue.",
                "Impact confirmed and triage is needed.",
                "Escalated to the responsible queue.",
            ]

        payload = {
            "mode": mode,
            "lang": lang,
            "user_reply": user_reply,
            "team_summary": team_summary,
            "action_items": [
                {"id": 1, "text": "Collect logs and account context", "priority": "P1"},
                {"id": 2, "text": "Assign investigation owner", "priority": "P2"},
            ],
            "metadata": {"provider": "stub", "version": "mvp-v1"},
        }
        return json.dumps(payload, ensure_ascii=False)


@dataclass
class RunResult:
    ok: bool
    attempts: int
    output: dict | None
    errors: list[str]


def _rewrite_prompt(original_prompt: str, errors: list[str]) -> str:
    return (
        f"{original_prompt}\n\n"
        "REWRITE PASS:\n"
        "The previous JSON failed validation.\n"
        f"Errors: {json.dumps(errors, ensure_ascii=False)}\n"
        "Return corrected JSON only."
    )


def run_pipeline(
    *,
    text: str,
    mode: str,
    lang: str,
    llm: LLMClient,
    retry_on_fail: bool = True,
) -> RunResult:
    system_prompt, user_prompt = render_messages(
        {
            "mode": mode,
            "lang": lang,
            "input_text": text,
        }
    )
    attempts = 1
    raw = llm.generate(system_prompt=system_prompt, user_prompt=user_prompt)
    validation: ValidationResult = validate_payload(raw, expected_lang=lang)
    if validation.ok:
        return RunResult(ok=True, attempts=attempts, output=validation.data.model_dump(), errors=[])

    if retry_on_fail:
        attempts += 1
        rewrite_user_prompt = _rewrite_prompt(user_prompt, validation.errors)
        raw = llm.generate(system_prompt=system_prompt, user_prompt=rewrite_user_prompt)
        validation = validate_payload(raw, expected_lang=lang)
        if validation.ok:
            return RunResult(ok=True, attempts=attempts, output=validation.data.model_dump(), errors=[])

    return RunResult(ok=False, attempts=attempts, output=None, errors=validation.errors)


def _resolve_lang(requested_lang: str, text: str) -> str:
    if requested_lang == "auto":
        return detect_language(text)
    return requested_lang


def _build_client() -> LLMClient:
    provider = os.getenv("LLM_PROVIDER", "stub").lower()
    if provider != "stub":
        raise RuntimeError("Only LLM_PROVIDER=stub is implemented in this MVP.")
    return StubLLMClient()


def _run_cli(args: argparse.Namespace) -> int:
    input_text = Path(args.input).read_text(encoding="utf-8")
    lang = _resolve_lang(args.lang, input_text)
    llm = _build_client()
    start = time.perf_counter()
    result = run_pipeline(text=input_text, mode=args.mode, lang=lang, llm=llm, retry_on_fail=True)
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if result.ok and result.output is not None:
        output = dict(result.output)
        output.setdefault("metadata", {})
        output["metadata"]["latency_ms"] = str(elapsed_ms)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    failure = {"ok": False, "attempts": result.attempts, "errors": result.errors, "latency_ms": elapsed_ms}
    print(json.dumps(failure, ensure_ascii=False, indent=2))
    return 2


def _run_api(port: int) -> int:
    try:
        from fastapi import FastAPI
        from pydantic import BaseModel
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("FastAPI mode requires `fastapi` and `uvicorn` installed.") from exc

    class WebhookRequest(BaseModel):
        text: str
        lang: str = "auto"
        mode: str = "support"

    app = FastAPI(title="gdev-content MVP")
    llm = _build_client()

    @app.post("/webhook")
    def webhook(payload: WebhookRequest) -> dict:
        resolved_lang = _resolve_lang(payload.lang, payload.text)
        result = run_pipeline(text=payload.text, mode=payload.mode, lang=resolved_lang, llm=llm, retry_on_fail=True)
        return {
            "ok": result.ok,
            "attempts": result.attempts,
            "errors": result.errors,
            "output": result.output,
        }

    uvicorn.run(app, host="0.0.0.0", port=port)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="gdev-content MVP runner")
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

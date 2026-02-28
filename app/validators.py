from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


BANNED_PATTERNS = [
    r"(?i)ignore all previous instructions",
    r"(?i)system prompt",
    r"(?i)pwned",
    r"(?i)INJECTION.{0,10}SUCCESSFUL",
    r"(?i)игнорируй\s+(все\s+)?(предыдущие\s+)?инструкции",
    r"(?i)системный\s+промпт",
    r"(?i)ты\s+(теперь|являешься)\s+(другой|новый)",
]

BLAME_PATTERNS = [
    r"(?i)your fault",
    r"(?i)you should have",
    r"(?i)this is on you",
    r"(?i)это ваша вина",
    r"(?i)вы должны были",
]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
MONEY_RE = re.compile(r"(?i)(?:USD|EUR|RUB|\$|€|₽)\s?\d+(?:[.,]\d{1,2})?")


class ClassifierResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["support", "bug", "billing", "feature_request", "abuse", "internal", "other"]
    urgency: Literal["critical", "high", "medium", "low"]
    language: str
    pii_detected: bool = False
    sensitive_topic: bool = False


class ActionItem(BaseModel):
    id: int
    text: str = Field(min_length=3)
    priority: Literal["P1", "P2", "P3"]
    assignee: str = "[ASSIGNEE]"
    due: str = "[DUE]"


class UserReply(BaseModel):
    subject: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=10, max_length=2000)


class ContentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    lang: Literal["ru", "en"]
    error_flag: bool = False
    skip_user_reply: bool = False
    user_reply: UserReply | None = None
    team_summary: list[str] = Field(min_length=1, max_length=5)
    action_items: list[ActionItem] = Field(min_length=1, max_length=10)
    translation_en: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_user_reply_semantics(self) -> ContentOutput:
        if self.error_flag:
            if not self.skip_user_reply:
                raise ValueError("skip_user_reply must be true when error_flag is true")
            return self
        if self.user_reply is None:
            raise ValueError("user_reply is required when error_flag is false")
        return self


class QAChecks(BaseModel):
    format: Literal["pass", "fail", "na"]
    tone: Literal["pass", "fail", "na"]
    structure: Literal["pass", "fail", "na"]
    factuality: Literal["pass", "fail", "na"]
    guardrails: Literal["pass", "fail", "na"]
    language: Literal["pass", "fail", "na"]
    length: Literal["pass", "fail", "na"]
    completeness: Literal["pass", "fail", "na"]


class QAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["APPROVE", "REJECT", "ESCALATE"]
    overall_score: float
    checks: QAChecks
    issues: list[str] = Field(default_factory=list)
    rewrite_needed: bool = False


@dataclass
class ValidationResult:
    data: ContentOutput | None
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors and self.data is not None


def detect_language(text: str) -> str:
    cyr = sum(1 for ch in text if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    lat = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    if cyr == 0 and lat == 0:
        return "en"
    if cyr >= lat:
        return "ru"
    return "en"


def mask_pii(text: str) -> str:
    text = EMAIL_RE.sub("[EMAIL]", text)
    text = PHONE_RE.sub("[PHONE]", text)
    return text


def _tone_errors(reply: str, lang: str) -> list[str]:
    errors: list[str] = []
    for pattern in BLAME_PATTERNS:
        if re.search(pattern, reply):
            errors.append("tone: blaming language detected")
            break

    if lang == "en":
        empathy_signals = ("sorry", "understand", "thank you", "we appreciate")
    else:
        empathy_signals = ("сожале", "понима", "спасибо", "благодар")

    if not any(token in reply.lower() for token in empathy_signals):
        errors.append("tone: empathy signal not detected")
    return errors


def _banned_content_errors(*, all_output_text: str, user_reply_body: str | None) -> list[str]:
    errors: list[str] = []
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, all_output_text):
            errors.append(f"banned pattern matched: {pattern}")
    if EMAIL_RE.search(all_output_text):
        errors.append("banned content: email detected in output")
    if user_reply_body and MONEY_RE.search(user_reply_body):
        errors.append("banned content: specific amount detected in output")
    return errors


def evaluate_content_checks(data: ContentOutput, expected_lang: str) -> dict[str, bool]:
    body = data.user_reply.body if data.user_reply else ""
    tone_ok = True
    language_ok = True

    if data.user_reply is not None:
        tone_ok = len(_tone_errors(body, data.lang)) == 0
        language_ok = detect_language(body) == expected_lang

    flat_output = "\n".join(
        [body]
        + data.team_summary
        + [item.text for item in data.action_items]
        + ([data.translation_en] if data.translation_en else [])
    )
    guardrail_ok = len(_banned_content_errors(all_output_text=flat_output, user_reply_body=body)) == 0

    return {
        "schema": True,
        "format": True,
        "tone": tone_ok,
        "language": language_ok,
        "guardrail": guardrail_ok,
    }


def validate_payload(raw_text: str, expected_lang: str) -> ValidationResult:
    errors: list[str] = []
    try:
        data = ContentOutput.model_validate_json(raw_text)
    except ValidationError as exc:
        return ValidationResult(data=None, errors=[f"schema: {exc}"])

    if data.lang != expected_lang:
        errors.append(f"required fields: lang mismatch, expected {expected_lang}, got {data.lang}")

    body = data.user_reply.body if data.user_reply else ""

    if data.user_reply is not None:
        reply_lang = detect_language(body)
        if reply_lang != expected_lang:
            errors.append(f"language: user_reply detected {reply_lang}, expected {expected_lang}")

    for idx, summary_item in enumerate(data.team_summary):
        if not summary_item.strip():
            errors.append(f"required fields: empty team_summary item at index {idx}")
        if len(summary_item) > 200:
            errors.append(f"length: team_summary[{idx}] exceeds 200 chars")

    if data.user_reply is not None:
        errors.extend(_tone_errors(body, data.lang))

    flat_output = "\n".join(
        [body]
        + data.team_summary
        + [item.text for item in data.action_items]
        + ([data.translation_en] if data.translation_en else [])
    )
    errors.extend(_banned_content_errors(all_output_text=flat_output, user_reply_body=body if body else None))

    return ValidationResult(data=data, errors=errors)

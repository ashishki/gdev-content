from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


BANNED_PATTERNS = [
    r"(?i)ignore all previous instructions",
    r"(?i)system prompt",
    r"(?i)pwned",
    r"(?i)INJECTION SUCCESSFUL",
]

BLAME_PATTERNS = [
    r"(?i)your fault",
    r"(?i)you should have",
    r"(?i)this is on you",
    r"(?i)это ваша вина",
    r"(?i)вы должны были",
]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MONEY_RE = re.compile(r"(?i)(?:USD|EUR|RUB|\$|€|₽)\s?\d+(?:[.,]\d{1,2})?")


class ActionItem(BaseModel):
    id: int
    text: str = Field(min_length=3)
    priority: Literal["P1", "P2", "P3"]
    assignee: str = "[ASSIGNEE]"
    due: str = "[DUE]"


class ContentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    lang: Literal["ru", "en"]
    user_reply: str = Field(min_length=10)
    team_summary: list[str] = Field(min_length=1)
    action_items: list[ActionItem] = Field(min_length=1, max_length=10)
    metadata: dict[str, str] = Field(default_factory=dict)


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


def _banned_content_errors(text: str) -> list[str]:
    errors: list[str] = []
    for pattern in BANNED_PATTERNS:
        if re.search(pattern, text):
            errors.append(f"banned pattern matched: {pattern}")
    if EMAIL_RE.search(text):
        errors.append("banned content: email detected in output")
    if MONEY_RE.search(text):
        errors.append("banned content: specific amount detected in output")
    return errors


def validate_payload(raw_text: str, expected_lang: str) -> ValidationResult:
    errors: list[str] = []
    try:
        data = ContentOutput.model_validate_json(raw_text)
    except ValidationError as exc:
        return ValidationResult(data=None, errors=[f"schema: {exc}"])

    if data.lang != expected_lang:
        errors.append(f"required fields: lang mismatch, expected {expected_lang}, got {data.lang}")

    reply_lang = detect_language(data.user_reply)
    if reply_lang != expected_lang:
        errors.append(f"language: user_reply detected {reply_lang}, expected {expected_lang}")

    for idx, summary_item in enumerate(data.team_summary):
        if not summary_item.strip():
            errors.append(f"required fields: empty team_summary item at index {idx}")

    errors.extend(_tone_errors(data.user_reply, data.lang))
    flat_output = f"{data.user_reply}\n" + "\n".join(data.team_summary) + "\n" + " ".join(
        item.text for item in data.action_items
    )
    errors.extend(_banned_content_errors(flat_output))
    return ValidationResult(data=data, errors=errors)


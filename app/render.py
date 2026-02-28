from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined


ROOT_DIR = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ROOT_DIR / "prompts"

_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(PROMPTS_DIR)),
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def load_text(name: str) -> str:
    path = PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


def load_stage_prompt(stage: str) -> str:
    prompt_version = os.getenv("PROMPT_VERSION", "v1.0")
    filename = f"{stage}_{prompt_version}.txt"
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    legacy = PROMPTS_DIR / f"{stage}_v1.txt"
    if legacy.exists():
        return legacy.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Prompt file not found for stage '{stage}': {filename}")


def render_user_template(context: dict[str, Any], template_name: str = "user_template.j2") -> str:
    template = _JINJA_ENV.get_template(template_name)
    return template.render(**context)


def render_messages(context: dict[str, Any]) -> tuple[str, str]:
    """Legacy single-stage renderer kept for stub compatibility."""
    system_prompt = load_text("system.txt")
    structural = render_user_template({"mode": context["mode"], "lang": context["lang"]})
    # Security: append raw user text outside the template engine to avoid SSTI.
    user_prompt = f"{structural}\n\n---TICKET---\n{context['input_text']}\n---END TICKET---"
    return system_prompt, user_prompt

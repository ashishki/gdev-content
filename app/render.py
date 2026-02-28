from __future__ import annotations

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


def render_user_template(context: dict[str, Any], template_name: str = "user_template.j2") -> str:
    template = _JINJA_ENV.get_template(template_name)
    return template.render(**context)


def render_messages(context: dict[str, Any]) -> tuple[str, str]:
    system_prompt = load_text("system.txt")
    guidelines = load_text("guidelines.md")
    context = dict(context)
    context["guidelines"] = guidelines
    user_prompt = render_user_template(context)
    return system_prompt, user_prompt


"""Prompt templates: ``llm/prompts/<task>.md`` with ``{{placeholders}}``.

No prompt text lives in Python. Each Markdown file has these headings:

* ``## System`` - the task instructions (stable, cached by the API after the framework block);
* ``## User`` - the per-call part with the volatile values;
* ``## Default framework`` (optional) - used when the channel has no framework of that type.

Lines before the first heading are comments for the people editing the file. Placeholders are
``{{name}}`` and are replaced in one pass, so a value that itself contains ``{{...}}`` is left
alone. Rendering with a missing value raises :class:`PromptError`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SECTION_RE = re.compile(r"^##\s+(System|User|Default framework)\s*$", re.IGNORECASE | re.MULTILINE)
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

SECTION_KEYS = {"system": "system", "user": "user", "default framework": "default_framework"}


class PromptError(Exception):
    """A prompt file is missing, lacks a section, or a placeholder has no value."""


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    system: str
    user: str
    default_framework: str = ""
    path: Path | None = field(default=None, compare=False)

    def placeholders(self, section: str = "user") -> list[str]:
        text = getattr(self, section)
        seen: list[str] = []
        for match in PLACEHOLDER_RE.finditer(text):
            if match.group(1) not in seen:
                seen.append(match.group(1))
        return seen

    def render(self, section: str, variables: dict[str, Any]) -> str:
        if section not in ("system", "user", "default_framework"):
            raise PromptError(f"Unknown prompt section '{section}'.")
        return render_text(getattr(self, section), variables, where=f"{self.name}.md/{section}")


def render_text(text: str, variables: dict[str, Any], *, where: str = "prompt") -> str:
    """Replace every ``{{name}}`` in one pass. Lists become bullet lines, None becomes ''."""

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise PromptError(f"The prompt {where} needs a value for {{{{{key}}}}}.")
        return format_value(variables[key])

    return PLACEHOLDER_RE.sub(replace, text).strip() + "\n"


def format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        items = [format_value(item) for item in value]
        return "\n".join(f"- {item}" for item in items if item != "") or "(none)"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def parse_prompt(name: str, text: str, path: Path | None = None) -> PromptTemplate:
    sections: dict[str, str] = {}
    matches = list(SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        key = SECTION_KEYS[match.group(1).lower()]
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[key] = text[start:end].strip()
    if "system" not in sections or "user" not in sections:
        raise PromptError(
            f"The prompt file {name}.md must have a '## System' and a '## User' section."
        )
    return PromptTemplate(
        name=name,
        system=sections["system"],
        user=sections["user"],
        default_framework=sections.get("default_framework", ""),
        path=path,
    )


@lru_cache(maxsize=32)
def load_prompt(name: str, prompts_dir: Path | None = None) -> PromptTemplate:
    folder = prompts_dir or PROMPTS_DIR
    path = folder / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"The prompt file {path} is missing.")
    return parse_prompt(name, path.read_text(encoding="utf-8"), path)


def available_prompts(prompts_dir: Path | None = None) -> list[str]:
    folder = prompts_dir or PROMPTS_DIR
    return sorted(p.stem for p in folder.glob("*.md"))

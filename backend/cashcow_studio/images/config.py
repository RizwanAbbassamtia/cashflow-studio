"""``config/images.yaml``: what the images stage sends to the image tool and how it checks.

Thresholds that gate a stage (minimum QA score, retries, duplicate distance) live in
``policy/rules.yaml``; this file holds the generation settings: the size policy, the fixed
prompt suffix and retry wording, reference-image use, the model per provider and the
style-sheet limits. ``CCS_IMAGE_SIZE`` (environment) overrides the size for one run, which
keeps test runs small.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from ..llm.config import load_yaml

IMAGES_FILE = "images.yaml"
SIZE_ENV = "CCS_IMAGE_SIZE"
LARGEST = "largest"

DEFAULT_SUFFIX = "No text, no logos, no watermarks."
DEFAULT_RETRY_NOTE = (
    "The previous picture was rejected because {reason}. Fix that and keep everything else "
    "the same."
)
DEFAULT_NOTE_PREFIX = "Reviewer note: {note}."
DEFAULT_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


@dataclass(frozen=True)
class ImagesConfig:
    size: str = LARGEST
    """``largest`` (the tool's biggest size for the aspect) or a label / ``WIDTHxHEIGHT``."""
    max_size_label: str = "4K"
    """Never ask for more than this even when the tool offers it."""
    suffix: str = DEFAULT_SUFFIX
    retry_note: str = DEFAULT_RETRY_NOTE
    note_prefix: str = DEFAULT_NOTE_PREFIX
    reference_style_sheet: bool = True
    reference_previous_scene: bool = True
    style_sheet_max_side_px: int = 2048
    reference_extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    models: dict[str, str] = field(default_factory=dict)
    """Model id per provider id; empty means the provider's own default."""
    qa_enabled: bool = True
    qa_max_side_px: int = 1568
    """Pictures are shrunk to this long side before the vision check (API limit and cost)."""

    def model_for(self, provider_id: str) -> str:
        return str(self.models.get(provider_id, "") or "")


def _bool(value: Any, default: bool) -> bool:
    return bool(value) if isinstance(value, bool) else default


def _int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=1)
def images_config() -> ImagesConfig:
    data = load_yaml(IMAGES_FILE)
    generation = data.get("generation") if isinstance(data.get("generation"), dict) else {}
    prompt = data.get("prompt") if isinstance(data.get("prompt"), dict) else {}
    references = data.get("references") if isinstance(data.get("references"), dict) else {}
    sheet = data.get("style_sheet") if isinstance(data.get("style_sheet"), dict) else {}
    qa = data.get("qa") if isinstance(data.get("qa"), dict) else {}
    models_raw = data.get("models") if isinstance(data.get("models"), dict) else {}
    extensions = sheet.get("reference_extensions")
    if not isinstance(extensions, list) or not extensions:
        extensions = list(DEFAULT_EXTENSIONS)
    return ImagesConfig(
        size=str(generation.get("size") or LARGEST).strip() or LARGEST,
        max_size_label=str(generation.get("max_size_label") or "4K").strip().upper(),
        suffix=str(prompt.get("suffix") or DEFAULT_SUFFIX).strip(),
        retry_note=str(prompt.get("retry_note") or DEFAULT_RETRY_NOTE).strip(),
        note_prefix=str(prompt.get("note_prefix") or DEFAULT_NOTE_PREFIX).strip(),
        reference_style_sheet=_bool(references.get("style_sheet"), True),
        reference_previous_scene=_bool(references.get("previous_scene"), True),
        style_sheet_max_side_px=_int(sheet.get("max_side_px"), 2048),
        reference_extensions=tuple(str(e).lower() for e in extensions),
        models={str(k).lower(): str(v) for k, v in models_raw.items() if v},
        qa_enabled=_bool(qa.get("enabled"), True),
        qa_max_side_px=_int(qa.get("max_side_px"), 1568),
    )


def size_choice(config: ImagesConfig | None = None) -> str:
    """The size policy for this run: ``CCS_IMAGE_SIZE`` wins over the YAML file."""
    override = os.environ.get(SIZE_ENV, "").strip()
    if override:
        return override
    return (config or images_config()).size


def clear_cache() -> None:
    images_config.cache_clear()

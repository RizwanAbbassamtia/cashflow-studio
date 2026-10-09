"""The image provider contract (docs/M1-M2-CONTRACT.md section 7, docs/PLAN.md section 7).

An image provider turns one scene prompt into one text-free PNG in the requested aspect and
size, optionally guided by reference images (the channel's style sheet, earlier scenes).
The result records the model, the seed when the tool has one, the cost and the provenance
flags (whether the file carries a C2PA manifest or a SynthID watermark), which the export
stage uses for the synthetic-media disclosure.

Adapters live next to this file (``mock.py``, ``gemini.py`` and, from M3, more tools).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator, model_validator

from ..base import AdapterState, CostEstimate, ProviderHealth, ProviderKind

SIZE_LABELS: dict[str, int] = {"1K": 1024, "2K": 2048, "4K": 4096}
"""Size label -> length of the long side in pixels."""
DEFAULT_ASPECTS = ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "4:5", "5:4", "21:9"]
ASPECT_RE = re.compile(r"^\s*(\d{1,3})\s*:\s*(\d{1,3})\s*$")
PIXELS_RE = re.compile(r"^\s*(\d{2,5})\s*[xX×]\s*(\d{2,5})\s*$")
REFERENCE_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def parse_aspect(aspect: str) -> tuple[int, int]:
    """``"16:9"`` -> ``(16, 9)``. Raises ``ValueError`` with a plain message otherwise."""
    match = ASPECT_RE.match(aspect or "")
    if not match or int(match[1]) == 0 or int(match[2]) == 0:
        raise ValueError(
            f"{aspect!r} is not an aspect ratio. Use width:height, for example 16:9 or 9:16."
        )
    return int(match[1]), int(match[2])


def _even(value: int) -> int:
    """Video encoders need even dimensions."""
    return max(value - (value % 2), 2)


def pixel_size(aspect: str, size: str) -> tuple[int, int]:
    """Width and height in pixels for an aspect plus a size label (``1K``/``2K``/``4K``) or an
    explicit ``WIDTHxHEIGHT``. An explicit size is turned to match the aspect's orientation."""
    aspect_w, aspect_h = parse_aspect(aspect)
    label = (size or "").strip().upper()
    if label in SIZE_LABELS:
        long_side = SIZE_LABELS[label]
        if aspect_w >= aspect_h:
            width, height = long_side, round(long_side * aspect_h / aspect_w)
        else:
            width, height = round(long_side * aspect_w / aspect_h), long_side
    else:
        match = PIXELS_RE.match(size or "")
        if not match:
            raise ValueError(
                f"{size!r} is not an image size. Use 1K, 2K, 4K or width x height such as "
                "1920x1080."
            )
        width, height = int(match[1]), int(match[2])
        if (width >= height) != (aspect_w >= aspect_h):
            width, height = height, width
    return _even(width), _even(height)


def size_label(size: str) -> str:
    """The nearest ``1K``/``2K``/``4K`` label for a size (labels pass through unchanged)."""
    label = (size or "").strip().upper()
    if label in SIZE_LABELS:
        return label
    match = PIXELS_RE.match(size or "")
    if not match:
        raise ValueError(f"{size!r} is not an image size. Use 1K, 2K, 4K or 1920x1080.")
    long_side = max(int(match[1]), int(match[2]))
    return min(SIZE_LABELS, key=lambda name: abs(SIZE_LABELS[name] - long_side))


class Provenance(BaseModel):
    """What the file carries. ``None`` means unknown or not checked."""

    c2pa: bool | None = None
    synthid: bool | None = None


class ImageCapabilities(BaseModel):
    """What an image tool can do and what it costs; read from ``config/providers.yaml``."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: ProviderKind = "image"
    adapter: AdapterState = "planned"
    models: list[str] = []
    default_model: str = ""
    aspects: list[str] = Field(default_factory=lambda: list(DEFAULT_ASPECTS))
    sizes: list[str] = Field(default_factory=lambda: list(SIZE_LABELS))
    max_reference_images: int = Field(default=0, ge=0)
    provenance: Provenance = Provenance()
    price_per_image_usd: float = Field(default=0.0, ge=0, description="fallback per image")
    price_by_size_usd: dict[str, float] = Field(default={}, description="per size label")
    key_env: str = Field(default="", pattern=r"^[A-Z0-9_]*$", description="name of the key")
    docs_url: str = ""
    notes: str = ""
    verified_on: str | None = Field(
        default=None, description="date (YYYY-MM-DD) the model ids and prices were last checked"
    )

    @field_validator("aspects")
    @classmethod
    def _aspects_are_ratios(cls, value: list[str]) -> list[str]:
        for aspect in value:
            parse_aspect(aspect)
        return value

    def price_for(self, size: str) -> float:
        try:
            label = size_label(size)
        except ValueError:
            label = ""
        return self.price_by_size_usd.get(label, self.price_per_image_usd)


class ImageRequest(BaseModel):
    """One scene image. The storyboard stage prefixes the style guide to ``prompt`` already;
    ``style`` is for an extra short style hint, ``negative_prompt`` for what to avoid."""

    prompt: str = Field(min_length=1)
    output_path: Path = Field(description="where the PNG is written")
    negative_prompt: str = ""
    aspect: str = "16:9"
    size: str = Field(default="2K", description="1K, 2K, 4K or width x height")
    reference_images: list[Path] = []
    seed: int | None = Field(default=None, ge=0)
    style: str = ""
    scene_id: str | None = Field(default=None, description="storyboard scene, for logs")

    @model_validator(mode="after")
    def _aspect_and_size_make_sense(self) -> ImageRequest:
        pixel_size(self.aspect, self.size)  # raises a plain-English ValueError
        return self

    @property
    def pixels(self) -> tuple[int, int]:
        return pixel_size(self.aspect, self.size)

    def cache_key(self, provider: str, model: str = "") -> str:
        """Content hash of everything that changes the picture, so edits never re-bill."""
        references: list[str] = []
        for path in self.reference_images:
            try:
                references.append(hashlib.sha256(Path(path).read_bytes()).hexdigest())
            except OSError:
                references.append(str(path))
        payload = {
            "provider": provider,
            "model": model,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "aspect": self.aspect,
            "size": self.size,
            "seed": self.seed,
            "style": self.style,
            "references": references,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class ImageResult(BaseModel):
    path: Path
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    model: str
    seed: int | None = None
    provenance: Provenance = Provenance()
    cost_usd: float = Field(default=0.0, ge=0)
    provider: str
    prompt_used: str = Field(default="", description="the full text sent to the tool")
    cached: bool = False
    scene_id: str | None = None


@runtime_checkable
class ImageProvider(Protocol):
    """Every image adapter implements exactly this; the images stage talks to nothing else."""

    id: str
    capabilities: ImageCapabilities

    def generate(self, request: ImageRequest) -> ImageResult: ...

    def estimate_cost(self, count: int = 1, size: str = "2K") -> CostEstimate: ...

    def health(self) -> ProviderHealth: ...

"""Mock images: a placeholder PNG with the prompt drawn on a coloured background.

``CCS_IMAGE_PROVIDER=mock`` (the default) selects it, so the Storyboard Board shows
pictures offline and tests never spend money. The picture has the requested aspect and
size, a background colour derived from the prompt (or the seed) and the prompt text itself,
so a reviewer can tell scenes apart. The same request always produces the same bytes.
"""

from __future__ import annotations

import colorsys
import hashlib
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

from ..base import CostEstimate, ProviderHealth
from .base import ImageCapabilities, ImageRequest, ImageResult, Provenance, pixel_size

MOCK_ID = "mock"
MOCK_MODEL = "mock-placeholder"
TEXT_COLOUR = (255, 255, 255)
MAX_PROMPT_CHARS = 1000


def default_capabilities() -> ImageCapabilities:
    return ImageCapabilities(
        id=MOCK_ID,
        name="Mock images (offline)",
        adapter="ready",
        models=[MOCK_MODEL],
        default_model=MOCK_MODEL,
        max_reference_images=14,
        provenance=Provenance(c2pa=False, synthid=False),
        price_per_image_usd=0.0,
        notes="Draws the prompt on a coloured placeholder. For tests and demos.",
    )


def seed_for(request: ImageRequest) -> int:
    """The request's seed, or a stable number derived from the prompt."""
    if request.seed is not None:
        return request.seed
    digest = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def background_colour(seed: int) -> tuple[int, int, int]:
    """A muted colour whose hue comes from the seed, so neighbouring scenes differ."""
    hue = (seed % 360) / 360.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.5, 0.55)
    return int(red * 255), int(green * 255), int(blue * 255)


def render_placeholder(request: ImageRequest, seed: int) -> Image.Image:
    width, height = pixel_size(request.aspect, request.size)
    image = Image.new("RGB", (width, height), background_colour(seed))
    draw = ImageDraw.Draw(image)

    body_px = max(14, min(width, height) // 28)
    small_px = max(12, body_px * 3 // 4)
    body_font = ImageFont.load_default(size=body_px)
    small_font = ImageFont.load_default(size=small_px)
    margin = body_px
    line_height = int(body_px * 1.3)

    header = f"MOCK IMAGE  {request.aspect}  {width}x{height}"
    if request.scene_id:
        header += f"  scene {request.scene_id}"
    draw.text((margin, margin), header, font=small_font, fill=TEXT_COLOUR)

    chars_per_line = max(10, int((width - 2 * margin) / (body_px * 0.55)))
    lines = textwrap.wrap(request.prompt[:MAX_PROMPT_CHARS], width=chars_per_line)
    y = margin + int(small_px * 2.2)
    bottom = height - margin - int(small_px * 2.6)
    for index, line in enumerate(lines):
        if y + line_height > bottom:
            if index < len(lines):
                draw.text((margin, y), "...", font=body_font, fill=TEXT_COLOUR)
            break
        draw.text((margin, y), line, font=body_font, fill=TEXT_COLOUR)
        y += line_height

    footer = f"seed {seed}"
    if request.style:
        footer += f"  style: {request.style[:40]}"
    if request.negative_prompt:
        footer += f"  avoid: {request.negative_prompt[:60]}"
    draw.text((margin, height - margin - small_px), footer, font=small_font, fill=TEXT_COLOUR)
    return image


class MockImageProvider:
    id = MOCK_ID

    def __init__(self, capabilities: ImageCapabilities | None = None) -> None:
        self.capabilities = capabilities or default_capabilities()

    def generate(self, request: ImageRequest) -> ImageResult:
        seed = seed_for(request)
        image = render_placeholder(request, seed)
        info = PngInfo()
        info.add_text("Software", "CashCow Studio mock image provider")
        info.add_text("Description", request.prompt[:MAX_PROMPT_CHARS])
        path = Path(request.output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "PNG", pnginfo=info)
        return ImageResult(
            path=path,
            width=image.width,
            height=image.height,
            model=MOCK_MODEL,
            seed=seed,
            provenance=self.capabilities.provenance.model_copy(),
            cost_usd=0.0,
            provider=self.id,
            prompt_used=request.prompt,
            scene_id=request.scene_id,
        )

    def estimate_cost(self, count: int = 1, size: str = "2K") -> CostEstimate:
        return CostEstimate(
            provider=self.id, unit="free", units=max(count, 0), cost_usd=0.0,
            note="Mock images are free.",
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.id,
            kind="image",
            status="ok",
            detail="Ready. Draws placeholder pictures for offline runs; no key needed.",
            model=MOCK_MODEL,
        )

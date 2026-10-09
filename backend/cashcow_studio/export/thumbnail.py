"""Thumbnails for the export stage (docs/M3-M4-CONTRACT.md section 4).

Flow: the competitor thumbnail (``01_research/competitor_thumbnail.jpg``) is read by Claude
into a layout *template* (where the subject sits, where the text goes, colours, mood), cached
per competitor video in the channel folder under ``thumbnail-templates/<video_id>.json``.
The image provider then makes a text-free subject picture in 16:9 and 9:16 from the mood and
the video's title, and Pillow composes the headline, a colour band and an accent bar from
the template on top of it: ``thumbnail.png`` (1280x720) and ``thumbnail_shorts.png``
(1080x1920) plus two more headline variants (``_v2``, ``_v3``). The picture itself is never
copied from the competitor; the pHash gate (``export.thumbnail_similarity``) proves it.

Everything degrades instead of failing: no ``analyze_image`` on the model client or no
competitor thumbnail gives a neutral template; an image tool that is not set up gives a
brand-colour gradient; a missing font file gives Pillow's built-in font.
"""

from __future__ import annotations

import colorsys
import hashlib
import inspect
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, features
from pydantic import BaseModel, ValidationError

from ..llm.prompts import load_prompt
from ..llm.text import keywords_of
from ..models.channel import Channel
from ..models.export import (
    VARIANT_IDS,
    TemplateSource,
    ThumbnailTemplate,
    ThumbnailTemplateFile,
    ThumbnailTextBlock,
    ThumbnailVariant,
)
from ..providers.image.base import ImageRequest, ImageResult
from ..storage.settings_store import atomic_write_text
from . import _phash as local_phash

log = logging.getLogger(__name__)

TASK = "thumbnail_template"
TEMPLATE_MODEL = "claude-sonnet-5-5"
TEMPLATE_DIR = "thumbnail-templates"
SUBJECT_SIZE = "2K"
SIZES: dict[str, tuple[int, int]] = {"16:9": (1280, 720), "9:16": (1080, 1920)}
ASPECTS: tuple[str, ...] = ("16:9", "9:16")
MIN_DISTANCE = 12
"""The pHash distance to the competitor thumbnail must be above this (rules.yaml)."""
MAX_SUBJECT_TRIES = 3
VARIANT_FILES: dict[str, tuple[str, str]] = {
    "v1": ("thumbnail.png", "thumbnail_shorts.png"),
    "v2": ("thumbnail_v2.png", "thumbnail_shorts_v2.png"),
    "v3": ("thumbnail_v3.png", "thumbnail_shorts_v3.png"),
}
SUBJECT_FILES: dict[str, str] = {"16:9": "subject_16x9.png", "9:16": "subject_9x16.png"}

FONTS_DIR = Path(__file__).resolve().parents[3] / "assets" / "fonts"
FONT_SUFFIXES = (".ttf", ".otf", ".ttc")
LANGUAGE_FONTS: dict[str, tuple[str, ...]] = {
    "Arabic": ("NotoNaskhArabic-Bold", "NotoNaskhArabic", "NotoSansArabic-Bold", "NotoSansArabic"),
    "Urdu": ("NotoNaskhArabic-Bold", "NotoNaskhArabic", "NotoSansArabic-Bold", "NotoSansArabic"),
    "Hindi": ("NotoSansDevanagari-Bold", "NotoSansDevanagari"),
    "Japanese": ("NotoSansJP-Bold", "NotoSansJP"),
    "Korean": ("NotoSansKR-Bold", "NotoSansKR"),
}
DEFAULT_FONTS: tuple[str, ...] = ("NotoSans-Bold", "NotoSans-Regular", "NotoSans")
RTL_LANGUAGES = frozenset({"Arabic", "Urdu"})
NO_UPPERCASE = frozenset({"Japanese", "Korean", "Hindi", "Arabic", "Urdu"})

DEFAULT_TEXT = "#FFFFFF"
DEFAULT_BAND = "#1F3864"
DEFAULT_ACCENT = "#FFC000"
DEFAULT_STROKE = "#000000"
HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")

FACE_PROMPTS: dict[str, str] = {
    "none": "no people and no faces",
    "face": "one expressive face of a fictional person, not a real or famous person",
    "ai_character": "one expressive illustrated character, not a real person",
}


# Perceptual hash: the images stage's copy when it is there, else the local one -------------


def _load_hash_functions() -> tuple[Callable[[Any], Any], Callable[[Any, Any], int]]:
    try:
        from ..images import phash as shared  # type: ignore[import-not-found]
    except ImportError:
        return local_phash.phash, local_phash.hamming
    compute = next(
        (getattr(shared, name) for name in ("phash", "compute_phash", "perceptual_hash")
         if callable(getattr(shared, name, None))),
        None,
    )
    measure = next(
        (getattr(shared, name) for name in ("hamming", "hamming_distance", "distance")
         if callable(getattr(shared, name, None))),
        None,
    )
    if compute is None or measure is None:
        return local_phash.phash, local_phash.hamming
    try:
        probe = Image.new("RGB", (16, 16), (10, 20, 30))
        value = compute(probe)
        if int(measure(value, value)) != 0:
            raise ValueError("self distance is not zero")
    except Exception:  # noqa: BLE001 - a different calling convention: use the local copy
        return local_phash.phash, local_phash.hamming
    return compute, measure


def similarity_distance(thumbnail: Path, competitor: Path | None) -> int | None:
    """pHash Hamming distance between two pictures; ``None`` when there is nothing to compare
    (no competitor thumbnail, unreadable, or a flat picture without any detail)."""
    if competitor is None or not Path(competitor).is_file() or not Path(thumbnail).is_file():
        return None
    try:
        if local_phash.is_flat(competitor):
            return None
        compute, measure = _load_hash_functions()
        return int(measure(compute(Path(thumbnail)), compute(Path(competitor))))
    except Exception as exc:  # noqa: BLE001 - a broken file must not stop the export
        log.warning("Could not compare %s with %s: %s", thumbnail, competitor, exc)
        return None


# Colours and fonts ---------------------------------------------------------------------------


def hex_colours(text: str | None) -> list[str]:
    """Every ``#RRGGBB`` (or ``#RGB``) in a free-text colour note, upper-cased."""
    out: list[str] = []
    for match in HEX_RE.findall(text or ""):
        value = match.upper()
        if len(value) == 4:
            value = "#" + "".join(ch * 2 for ch in value[1:])
        if value not in out:
            out.append(value)
    return out


def to_rgb(value: str, fallback: str = DEFAULT_BAND) -> tuple[int, int, int]:
    colours = hex_colours(value) or hex_colours(fallback) or [DEFAULT_BAND]
    hex_value = colours[0][1:]
    return int(hex_value[0:2], 16), int(hex_value[2:4], 16), int(hex_value[4:6], 16)


def luminance(rgb: tuple[int, int, int]) -> float:
    red, green, blue = (channel / 255 for channel in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_text(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    return (17, 17, 17) if luminance(rgb) > 0.6 else (255, 255, 255)


def shade(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    """Darker (< 1) or lighter (> 1) version of a colour."""
    hue, light, sat = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    light = max(0.0, min(1.0, light * factor))
    return tuple(int(round(c * 255)) for c in colorsys.hls_to_rgb(hue, light, sat))  # type: ignore[return-value]


def find_font(
    name: str | None, language: str | None = None, fonts_dir: Path | None = None
) -> Path | None:
    """The font file for a channel's headline font name (or path) and the language.

    Order: a file path as given; ``<name>*.ttf`` in ``assets/fonts``; the language's Noto
    font; Noto Sans Bold. ``None`` when nothing is vendored (Pillow's built-in font is used).
    """
    folder = Path(fonts_dir) if fonts_dir is not None else FONTS_DIR
    wanted: list[str] = []
    clean = (name or "").strip().strip('"')
    if clean:
        candidate = Path(clean)
        if candidate.suffix.lower() in FONT_SUFFIXES and candidate.is_file():
            return candidate
        wanted.append(re.sub(r"\s+", "", candidate.stem if candidate.suffix else clean))
    wanted.extend(LANGUAGE_FONTS.get(language or "", ()))
    wanted.extend(DEFAULT_FONTS)
    if not folder.is_dir():
        return None
    files = [p for p in folder.rglob("*") if p.suffix.lower() in FONT_SUFFIXES]
    # ``NotoSans[wdth,wght].ttf`` (a variable font) has the base name ``notosans``.
    base = {p: re.sub(r"\[.*?\]", "", p.stem).lower() for p in files}
    for stem in wanted:
        key = stem.lower()
        for candidate in (key, key + "-bold", key + "bold"):
            exact = sorted((p for p in files if base[p] == candidate), key=lambda p: p.name)
            if exact:
                return exact[0]
        prefixed = [p for p in files if base[p].startswith(key)]
        if prefixed:
            # The shortest base name is the plain family (``notosans`` before
            # ``notosansarabic``); a bold face wins over a regular one.
            prefixed.sort(key=lambda p: ("bold" not in base[p], len(base[p]), p.name))
            return prefixed[0]
    return None


@lru_cache(maxsize=256)
def _truetype(path: str, size: int) -> ImageFont.FreeTypeFont | None:
    """One loaded face per file and size (the fit search asks for many sizes)."""
    try:
        font = ImageFont.truetype(path, size)
    except OSError as exc:
        log.warning("Font %s could not be loaded (%s); using the built-in font", path, exc)
        return None
    try:
        font.set_variation_by_name("Bold")
    except (OSError, ValueError, AttributeError):
        pass
    return font


def load_font(path: Path | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(8, int(size))
    if path is not None:
        font = _truetype(str(path), size)
        if font is not None:
            return font
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow without a size argument
        return ImageFont.load_default()


@dataclass
class ThumbnailStyle:
    text: tuple[int, int, int] = (255, 255, 255)
    band: tuple[int, int, int] = (31, 56, 100)
    accent: tuple[int, int, int] = (255, 192, 0)
    stroke: tuple[int, int, int] = (0, 0, 0)
    palette: list[tuple[int, int, int]] = field(default_factory=list)
    font: Path | None = None
    language: str = "English"
    rtl: bool = False
    uppercase: bool = True
    notes: list[str] = field(default_factory=list)


def style_for(
    channel: Channel,
    template: ThumbnailTemplate,
    language: str | None = None,
    fonts_dir: Path | None = None,
) -> ThumbnailStyle:
    """Colours from the channel's headline note, else the template, else the brand colours."""
    language = language or channel.channel.language
    configured = hex_colours(channel.thumbnail.headline_colors)
    brand = hex_colours(" ".join(channel.channel.brand_colors))
    block = template.headline_block()
    palette = hex_colours(" ".join(template.palette))
    text = configured[0] if configured else (hex_colours(block.color) or [DEFAULT_TEXT])[0]
    band = (
        configured[1] if len(configured) > 1
        else brand[0] if brand
        else palette[0] if palette
        else DEFAULT_BAND
    )
    accent = (
        configured[2] if len(configured) > 2
        else brand[1] if len(brand) > 1
        else palette[1] if len(palette) > 1
        else DEFAULT_ACCENT
    )
    stroke = (hex_colours(block.stroke) or [DEFAULT_STROKE])[0]
    style = ThumbnailStyle(
        text=to_rgb(text, DEFAULT_TEXT),
        band=to_rgb(band, DEFAULT_BAND),
        accent=to_rgb(accent, DEFAULT_ACCENT),
        stroke=to_rgb(stroke, DEFAULT_STROKE),
        palette=[to_rgb(c) for c in (configured[2:] + brand + palette)],
        font=find_font(channel.thumbnail.headline_font, language, fonts_dir),
        language=language,
        rtl=language in RTL_LANGUAGES,
        uppercase=language not in NO_UPPERCASE,
    )
    if luminance(style.text) > 0.6 and luminance(style.band) > 0.6:
        style.text = contrast_text(style.band)
        style.notes.append("The headline colour was darkened so it reads on the band.")
    if style.font is None:
        style.notes.append(
            "No headline font file was found in assets/fonts; the built-in font was used."
        )
    if style.rtl and not features.check("raqm"):
        style.notes.append(
            "Right-to-left text shaping is not available on this PC (raqm); check the "
            "headline on the thumbnail."
        )
    return style


# Templates -----------------------------------------------------------------------------------


def template_dir(shared_dir: Path, channel_slug: str) -> Path:
    return Path(shared_dir) / "channels" / channel_slug / TEMPLATE_DIR


def template_path(shared_dir: Path, channel_slug: str, video_id: str) -> Path:
    safe = SAFE_ID_RE.sub("_", video_id.strip())[:40] or "video"
    return template_dir(shared_dir, channel_slug) / f"{safe}.json"


def read_template_file(path: Path) -> ThumbnailTemplateFile | None:
    if not path.is_file():
        return None
    try:
        return ThumbnailTemplateFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        log.warning("Ignoring the cached thumbnail template %s: %s", path, exc)
        return None


def write_template_file(path: Path, record: ThumbnailTemplateFile) -> Path:
    atomic_write_text(path, record.model_dump_json(indent=2) + "\n")
    return path


def neutral_template(channel: Channel) -> ThumbnailTemplate:
    """A safe layout when no competitor thumbnail can be read: subject right, headline on a
    band across the lower third, brand colours."""
    brand = hex_colours(" ".join(channel.channel.brand_colors))
    configured = hex_colours(channel.thumbnail.headline_colors)
    return ThumbnailTemplate(
        subject_box=[0.30, 0.05, 0.68, 0.90],
        text_blocks=[
            ThumbnailTextBlock(
                box=[0.04, 0.60, 0.92, 0.34],
                role="headline",
                color=configured[0] if configured else DEFAULT_TEXT,
                stroke=DEFAULT_STROKE,
            )
        ],
        palette=brand or [DEFAULT_BAND, DEFAULT_ACCENT],
        mood=channel.channel.niche or "clear, bold, emotional",
        has_face=False,
        layout_notes="Neutral layout: one subject on the right, the headline on a band "
        "across the lower third.",
    )


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


@dataclass
class TemplateResult:
    template: ThumbnailTemplate
    source: TemplateSource
    model: str = ""
    cost_usd: float = 0.0
    note: str = ""
    cache_path: Path | None = None


def _coerce_template(result: Any) -> tuple[ThumbnailTemplate | None, Any]:
    """``analyze_image`` may return the model, a ``(model, usage)`` pair or a dict."""
    usage = None
    if isinstance(result, tuple) and result:
        result, usage = result[0], (result[1] if len(result) > 1 else None)
    if isinstance(result, ThumbnailTemplate):
        return result, usage
    if isinstance(result, BaseModel):
        return ThumbnailTemplate.model_validate(result.model_dump()), usage
    if isinstance(result, dict):
        return ThumbnailTemplate.model_validate(result), usage
    return None, usage


def _accepted_kwargs(function: Any, candidates: dict[str, Any]) -> dict[str, Any]:
    """The subset of ``candidates`` that ``function`` takes by name (or all, with ``**kw``)."""
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return dict(candidates)
    return {k: v for k, v in candidates.items() if k in parameters}


async def analyze_template(
    llm: Any,
    image_path: Path,
    prompt: str,
    *,
    model: str | None = None,
    project_id: str | None = None,
) -> tuple[ThumbnailTemplate | None, str, float, str]:
    """Ask the model client to read the competitor thumbnail.

    Returns ``(template or None, model, cost_usd, note)``. Works with a sync or async
    ``analyze_image`` and with either return shape (the model alone, with the usage kept on
    ``llm.last_usage``, or a ``(model, usage)`` pair); any problem gives ``None`` plus a note.
    """
    analyze = getattr(llm, "analyze_image", None)
    if not callable(analyze):
        return None, "", 0.0, (
            "The model client cannot read pictures yet; a neutral layout was used."
        )
    kwargs: dict[str, Any] = {
        "task": TASK, "image_path": Path(image_path), "prompt": prompt, "schema": ThumbnailTemplate,
    }
    if model:
        kwargs["model"] = model
    kwargs.update(_accepted_kwargs(analyze, {"project_id": project_id, "stage": "export"}))
    try:
        result = analyze(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        template, usage = _coerce_template(result)
    except Exception as exc:  # noqa: BLE001 - the thumbnail must still be made
        log.warning("Reading the competitor thumbnail failed: %s", exc)
        return None, "", 0.0, (
            f"The competitor thumbnail could not be read ({str(exc)[:120]}); a neutral "
            "layout was used."
        )
    if template is None:
        return None, "", 0.0, "The model gave no usable layout; a neutral layout was used."
    if usage is None:
        last = getattr(llm, "last_usage", None)
        if getattr(last, "task", None) == TASK:
            usage = last
    used_model = str(getattr(usage, "model", "") or model or "")
    cost = float(getattr(usage, "cost_usd", 0.0) or 0.0)
    return template, used_model, cost, ""


def template_prompt(variables: dict[str, Any]) -> str:
    prompt = load_prompt(TASK)
    return prompt.render("system", variables) + "\n" + prompt.render("user", variables)


async def resolve_template(
    llm: Any,
    *,
    shared_dir: Path,
    channel: Channel,
    video_id: str | None,
    image_path: Path | None,
    prompt_variables: dict[str, Any],
    model: str | None = None,
    project_id: str | None = None,
) -> TemplateResult:
    """The template for this video: the channel cache, else Claude, else the neutral layout."""
    cache_path = template_path(shared_dir, channel.slug, video_id) if video_id else None
    if cache_path is not None:
        cached = read_template_file(cache_path)
        if cached is not None:
            return TemplateResult(
                cached.template, "cache", cached.model, 0.0, "", cache_path
            )
    # The channel's template videos may have a cached layout from an earlier project.
    if cache_path is None or not cache_path.exists():
        for url in channel.thumbnail.template_videos:
            other = _video_id_of(str(url))
            if not other:
                continue
            cached = read_template_file(template_path(shared_dir, channel.slug, other))
            if cached is not None and (image_path is None or not Path(image_path).is_file()):
                return TemplateResult(
                    cached.template, "cache", cached.model, 0.0,
                    f"Layout taken from the channel's template video {other}.",
                )
    neutral = neutral_template(channel)
    if image_path is None or not Path(image_path).is_file():
        return TemplateResult(neutral, "neutral", note="No competitor thumbnail; neutral layout.")
    try:
        if local_phash.is_flat(image_path):
            return TemplateResult(
                neutral, "neutral",
                note="The competitor thumbnail has no detail to read; neutral layout.",
            )
    except Exception as exc:  # noqa: BLE001 - unreadable file
        return TemplateResult(
            neutral, "neutral", note=f"The competitor thumbnail could not be opened ({exc})."
        )
    template, used_model, cost, note = await analyze_template(
        llm, Path(image_path), template_prompt(prompt_variables), model=model,
        project_id=project_id,
    )
    if template is None:
        return TemplateResult(neutral, "neutral", cost_usd=cost, note=note)
    if not template.text_blocks:
        template.text_blocks = list(neutral.text_blocks)
    if not template.palette:
        template.palette = list(neutral.palette)
    if cache_path is not None:
        try:
            write_template_file(
                cache_path,
                ThumbnailTemplateFile(
                    video_id=video_id or "",
                    template=template,
                    source="llm",
                    model=used_model,
                    cached_at=datetime.now(UTC),
                    image_sha256=file_sha256(Path(image_path)),
                ),
            )
        except OSError as exc:
            log.warning("Could not cache the thumbnail template at %s: %s", cache_path, exc)
            cache_path = None
    return TemplateResult(template, "llm", used_model, cost, "", cache_path)


def _video_id_of(url: str) -> str | None:
    match = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", url or "")
    return match.group(1) if match else None


# Headlines ---------------------------------------------------------------------------------


def _cap_words(text: str, max_words: int) -> str:
    words = [w for w in re.split(r"\s+", (text or "").strip()) if w]
    return " ".join(words[:max_words]).strip(" ,;:-")


def headline_options(title: str, max_words: int, suggested: list[str] | None = None) -> list[str]:
    """Three different headlines of at most ``max_words`` words: the model's suggestions
    first, then phrases built from the title so there are always three."""
    max_words = max(1, int(max_words))
    options: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        text = _cap_words(" ".join((candidate or "").split()), max_words)
        key = re.sub(r"\W+", " ", text).strip().casefold()
        if text and key and key not in seen:
            seen.add(key)
            options.append(text)

    for item in suggested or []:
        add(str(item))
        if len(options) >= 3:
            return options[:3]
    words = [w.strip("\"'“”‘’.,!?:;()[]") for w in title.split()]
    words = [w for w in words if w]
    keywords = keywords_of(title, limit=max(3, max_words * 2))
    # The end of a title usually carries its payoff, so that phrase comes first.
    add(" ".join(words[-max_words:]))
    add(" ".join(keywords[:max_words]))
    add(" ".join(words[:max_words]))
    add(" ".join(reversed(keywords[:max_words])))
    for start in range(1, len(words)):
        if len(options) >= 3:
            break
        add(" ".join(words[start : start + max_words]))
    fallbacks = ["The Real Story", "What Happened Next", "Nobody Saw This", "Watch This"]
    for text in fallbacks:
        if len(options) >= 3:
            break
        add(" ".join(text.split()[-max_words:]))  # the end of the phrase carries its sense
    return options[:3]


# Subject pictures ------------------------------------------------------------------------------


def subject_prompt(title: str, template: ThumbnailTemplate, channel: Channel) -> tuple[str, str]:
    """The text-free picture behind the headline, and what to avoid in it."""
    style_guide = " ".join((channel.images.style_guide or "").split()).rstrip(".")
    mood = " ".join((template.mood or "").split()).rstrip(".") or "bold, emotional, high contrast"
    face = FACE_PROMPTS.get(channel.thumbnail.face, FACE_PROMPTS["none"])
    must_include = " ".join((channel.thumbnail.must_include or "").split()).rstrip(".")
    parts = [
        f"{style_guide}." if style_guide else "",
        f"A YouTube thumbnail picture for a video titled '{title}'.",
        f"Mood: {mood}.",
        "One clear subject filling the right two thirds of the frame, strong contrast, simple "
        "background with space on the left for a headline.",
        f"{face[:1].upper()}{face[1:]}.",
        f"Include: {must_include}." if must_include else "",
        "No text, no letters, no numbers, no logos, no watermarks.",
    ]
    negative_parts = [
        channel.thumbnail.must_avoid, channel.images.negative_rules,
        "text, letters, words, captions, logo, watermark, real people's faces, celebrities",
    ]
    negative = ", ".join(
        " ".join(p.split()).strip(" .") for p in negative_parts if p and p.strip()
    )
    return " ".join(p for p in parts if p), negative


def generate_subject(
    provider: Any, prompt: str, negative: str, aspect: str, out_path: Path, seed: int | None
) -> ImageResult:
    """One text-free picture from the image provider (blocking; run it in a thread)."""
    request = ImageRequest(
        prompt=prompt,
        output_path=Path(out_path),
        negative_prompt=negative,
        aspect=aspect,
        size=SUBJECT_SIZE,
        seed=seed,
        scene_id=f"thumbnail-{aspect}",
    )
    return provider.generate(request)


def fallback_subject(out_path: Path, aspect: str, style: ThumbnailStyle, seed: int = 0) -> Path:
    """A brand-colour gradient with soft shapes when no image tool is available."""
    width, height = SIZES.get(aspect, SIZES["16:9"])
    image = Image.new("RGB", (width, height), style.band)
    draw = ImageDraw.Draw(image)
    top, bottom = shade(style.band, 1.35), shade(style.band, 0.55)
    for y in range(height):
        mix = y / max(1, height - 1)
        colour = tuple(int(top[i] * (1 - mix) + bottom[i] * mix) for i in range(3))
        draw.line((0, y, width, y), fill=colour)
    accent = style.accent
    radius = int(min(width, height) * 0.28)
    cx, cy = int(width * (0.68 + 0.05 * (seed % 3))), int(height * 0.45)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=shade(accent, 0.9))
    draw.ellipse(
        (cx - radius // 2, cy - radius // 2, cx + radius // 2, cy + radius // 2),
        fill=shade(accent, 1.25),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, "PNG")
    return out_path


# Composition -------------------------------------------------------------------------------


def cover(
    image: Image.Image, size: tuple[int, int], focus: list[float] | None = None
) -> Image.Image:
    """Scale to cover ``size`` and crop around the focus box (fractions of the source)."""
    width, height = size
    scale = max(width / image.width, height / image.height)
    scaled = image.resize(
        (max(width, round(image.width * scale)), max(height, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    fx, fy = 0.5, 0.5
    if focus and len(focus) == 4:
        fx = min(1.0, max(0.0, focus[0] + focus[2] / 2))
        fy = min(1.0, max(0.0, focus[1] + focus[3] / 2))
    left = int(round(fx * scaled.width - width / 2))
    top = int(round(fy * scaled.height - height / 2))
    left = min(max(0, left), scaled.width - width)
    top = min(max(0, top), scaled.height - height)
    return scaled.crop((left, top, left + width, top + height)).convert("RGB")


def _box_pixels(box: list[float], size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    x, y, w, h = box
    return int(x * width), int(y * height), int(w * width), int(h * height)


def wrap_words(text: str, font: Any, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [text]


def fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path | None,
    box_w: int,
    box_h: int,
    max_lines: int,
) -> tuple[Any, list[str], int]:
    """The largest font size at which ``text`` fits the box in at most ``max_lines`` lines."""
    low, high = 12, max(16, int(box_h * 0.95))
    best: tuple[Any, list[str], int] | None = None
    while low <= high:
        size = (low + high) // 2
        font = load_font(font_path, size)
        lines = wrap_words(text, font, box_w, draw)
        line_height = int(size * 1.15)
        fits = (
            len(lines) <= max_lines
            and all(draw.textlength(line, font=font) <= box_w for line in lines)
            and line_height * len(lines) <= box_h
        )
        if fits:
            best = (font, lines, size)
            low = size + 1
        else:
            high = size - 1
    if best is None:
        font = load_font(font_path, 12)
        return font, wrap_words(text, font, box_w, draw)[:max_lines], 12
    return best


def compose(
    subject: Image.Image,
    size: tuple[int, int],
    headline: str,
    template: ThumbnailTemplate,
    style: ThumbnailStyle,
    variant_index: int = 0,
) -> Image.Image:
    """Subject picture + band + accent bar + headline, from the template's boxes."""
    width, height = size
    portrait = height > width
    base = cover(subject, size, template.subject_box).convert("RGBA")
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    block = template.headline_block()
    box = list(block.box)
    if portrait:
        # A 16:9 layout's text band is too thin for a phone screen: lower third, taller.
        box = [0.05, max(0.55, min(box[1], 0.62)), 0.90, max(box[3], 0.30)]
    x, y, w, h = _box_pixels(box, size)
    pad = max(12, int(min(width, height) * 0.025))
    colours = [style.accent, *style.palette] or [style.accent]
    accent = colours[variant_index % len(colours)]
    band = style.band if variant_index % 3 != 2 else shade(style.band, 0.7)
    # Soft gradient under the band so the picture stays visible but the text reads.
    gradient_top = max(0, y - int(height * 0.12))
    for row in range(gradient_top, min(height, y + h + pad)):
        mix = (row - gradient_top) / max(1, (y + h + pad) - gradient_top)
        alpha = int(150 * min(1.0, mix * 1.4))
        draw.line((0, row, width, row), fill=(*band, alpha))
    draw.rounded_rectangle(
        (x, y, x + w, y + h), radius=max(8, pad // 2), fill=(*band, 200)
    )
    bar_w = max(6, int(width * 0.012))
    draw.rectangle((x, y, x + bar_w, y + h), fill=(*accent, 255))
    text = headline.upper() if style.uppercase and headline.isascii() else headline
    text_x = x + bar_w + pad
    text_w = w - bar_w - 2 * pad
    text_h = h - 2 * pad
    font, lines, font_size = fit_text(
        draw, text, style.font, text_w, text_h, 3 if portrait else 2
    )
    line_height = int(font_size * 1.15)
    total = line_height * len(lines)
    start_y = y + pad + max(0, (text_h - total) // 2)
    stroke_width = max(2, font_size // 16)
    extra: dict[str, Any] = {}
    if style.rtl and features.check("raqm"):
        extra = {"direction": "rtl"}
    for index, line in enumerate(lines):
        line_x = text_x
        if style.rtl:
            line_x = x + w - pad - int(draw.textlength(line, font=font))
        draw.text(
            (line_x, start_y + index * line_height),
            line,
            font=font,
            fill=(*style.text, 255),
            stroke_width=stroke_width,
            stroke_fill=(*style.stroke, 255),
            **extra,
        )
    return Image.alpha_composite(base, overlay).convert("RGB")


def render_variant(
    out_dir: Path,
    variant_id: str,
    headline: str,
    subjects: dict[str, Path],
    template: ThumbnailTemplate,
    style: ThumbnailStyle,
) -> ThumbnailVariant:
    """Write the 16:9 and 9:16 files of one variant and return their record."""
    index = VARIANT_IDS.index(variant_id) if variant_id in VARIANT_IDS else 0  # type: ignore[arg-type]
    files = VARIANT_FILES[variant_id]
    out_dir.mkdir(parents=True, exist_ok=True)
    for aspect, name in zip(ASPECTS, files, strict=True):
        source = subjects.get(aspect) or subjects.get("16:9") or next(iter(subjects.values()))
        with Image.open(source) as opened:
            picture = compose(opened.convert("RGB"), SIZES[aspect], headline, template, style,
                              index)
        picture.save(out_dir / name, "PNG", compress_level=4)
    return ThumbnailVariant(
        id=variant_id,  # type: ignore[arg-type]
        headline=headline,
        file=files[0],
        file_shorts=files[1],
    )


def render_variants(
    out_dir: Path,
    headlines: list[str],
    subjects: dict[str, Path],
    template: ThumbnailTemplate,
    style: ThumbnailStyle,
    competitor: Path | None = None,
) -> list[ThumbnailVariant]:
    variants: list[ThumbnailVariant] = []
    for variant_id, headline in zip(VARIANT_IDS, headlines[:3], strict=False):
        variant = render_variant(out_dir, variant_id, headline, subjects, template, style)
        variant.distance = similarity_distance(out_dir / variant.file, competitor)
        variants.append(variant)
    return variants

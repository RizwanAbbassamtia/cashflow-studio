"""Popups: the short on-screen phrases of the storyboard, drawn by Pillow as transparent PNGs
and laid over the video by FFmpeg (``render/ffmpeg.py``).

One PNG per popup per preset: a rounded box in the channel's brand colour, bold Noto Sans at
64 px for a 1080p frame (padding 24), every size multiplied by ``min(width, height) / 1080``
so a 720p or 4K render gets the same look. Right-to-left languages are shaped when the
optional ``arabic-reshaper`` and ``python-bidi`` packages are installed; libass does that for
captions on its own.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw

from .fonts import load_font
from .presets import PopupLook

REFERENCE_PX = 1080
DEFAULT_BOX_COLOUR = "#1F3864"
POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right", "center")
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$|^#?([0-9a-fA-F]{3})$")
_RTL_RE = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")


def popup_scale(width: int, height: int) -> float:
    """1.0 at 1080p (either orientation), 2/3 at 720p, 2.0 at 4K."""
    return max(0.1, min(width, height) / REFERENCE_PX)


def parse_colour(value: str | None, fallback: str = DEFAULT_BOX_COLOUR) -> tuple[int, int, int]:
    match = _HEX_RE.match((value or "").strip())
    if not match:
        match = _HEX_RE.match(fallback)
        assert match is not None
    digits = match.group(1) or "".join(ch * 2 for ch in match.group(2))
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def text_colour_for(box: tuple[int, int, int]) -> tuple[int, int, int]:
    """White on a dark box, near-black on a light one."""
    red, green, blue = box
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return (255, 255, 255) if luminance < 150 else (20, 20, 20)


def brand_colour(colours: list[str] | None) -> str:
    for colour in colours or []:
        if _HEX_RE.match(colour.strip()):
            return colour.strip()
    return DEFAULT_BOX_COLOUR


def shape_text(text: str) -> str:
    """Join and reorder Arabic-script text for Pillow, when the optional packages exist."""
    if not _RTL_RE.search(text):
        return text
    try:
        import arabic_reshaper  # type: ignore[import-not-found]
        from bidi.algorithm import get_display  # type: ignore[import-not-found]
    except ImportError:
        return text
    try:
        return str(get_display(arabic_reshaper.reshape(text)))
    except Exception:  # noqa: BLE001 - shaping is cosmetic; the plain text still renders
        return text


def _wrap(text: str, font: object, max_width: int) -> list[str]:
    """One line when it fits, else the two-line split whose longer line is shortest."""
    words = text.split()
    if len(words) <= 1 or _text_width(text, font) <= max_width:
        return [text]
    best, best_width = [text], _text_width(text, font)
    for cut in range(1, len(words)):
        first, second = " ".join(words[:cut]), " ".join(words[cut:])
        widest = max(_text_width(first, font), _text_width(second, font))
        if widest < best_width:
            best, best_width = [first, second], widest
    return best


def _text_width(text: str, font: object) -> int:
    left, _top, right, _bottom = font.getbbox(text)  # type: ignore[attr-defined]
    return int(right - left)


def render_popup_png(
    text: str,
    path: Path,
    *,
    scale: float = 1.0,
    look: PopupLook | None = None,
    box_colour: str = DEFAULT_BOX_COLOUR,
    font_family: str = "Noto Sans",
    frame_width: int | None = None,
) -> tuple[int, int]:
    """Draw ``text`` in a rounded box and save a transparent PNG. Returns ``(width, height)``."""
    look = look or PopupLook()
    font_px = max(8, round(look.font_px * scale))
    padding = max(2, round(look.padding_px * scale))
    radius = max(2, round(look.radius_px * scale))
    font = load_font(font_family, font_px, bold=True)
    shaped = shape_text(" ".join(text.split()))
    max_text_width = (
        int(frame_width * look.max_width_share) - 2 * padding if frame_width else 10**6
    )
    lines = _wrap(shaped, font, max(font_px * 4, max_text_width))
    ascent, descent = font.getmetrics()  # type: ignore[attr-defined]
    line_height = ascent + descent
    line_gap = round(font_px * 0.15)
    text_width = max(_text_width(line, font) for line in lines)
    text_height = line_height * len(lines) + line_gap * (len(lines) - 1)
    width = text_width + 2 * padding
    height = text_height + 2 * padding
    box = parse_colour(box_colour)
    fill = text_colour_for(box)
    image = Image.new("RGBA", (max(width, 2), max(height, 2)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=(*box, 235))
    y = padding
    for line in lines:
        left, _top, _right, _bottom = font.getbbox(line)  # type: ignore[attr-defined]
        line_width = _text_width(line, font)
        x = padding + (text_width - line_width) // 2 - left
        draw.text((x, y), line, font=font, fill=(*fill, 255))
        y += line_height + line_gap
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    return image.width, image.height


def popup_position(
    position: str,
    popup_size: tuple[int, int],
    frame_size: tuple[int, int],
    *,
    margin_px: int,
    bottom_reserved_px: int = 0,
) -> tuple[int, int]:
    """Top-left corner of the popup in the frame. Bottom positions sit above the caption area
    (``bottom_reserved_px``) so popups and captions never overlap."""
    popup_w, popup_h = popup_size
    frame_w, frame_h = frame_size
    where = (position or "bottom-left").strip().lower().replace("_", "-")
    if where not in POSITIONS:
        where = "bottom-left"
    if where == "center":
        return (frame_w - popup_w) // 2, (frame_h - popup_h) // 2
    x = margin_px if where.endswith("left") else frame_w - popup_w - margin_px
    if where.startswith("top"):
        y = margin_px
    else:
        y = frame_h - popup_h - margin_px - bottom_reserved_px
    return max(0, x), max(0, y)

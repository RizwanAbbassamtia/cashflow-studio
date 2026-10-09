"""The Noto fonts vendored in ``assets/fonts`` (OFL licence, ``OFL.txt`` next to them).

Captions are drawn by libass (``subtitles=...:fontsdir=<this folder>``) and popups by Pillow;
both find the fonts here. The files are Google's variable fonts, so one file carries every
weight: Pillow picks the ``Bold`` instance by name, libass picks the default instance and
emboldens. ``CCS_FONTS_DIR`` points somewhere else when the app is packaged.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT_FILES: dict[str, str] = {
    "Noto Sans": "NotoSans[wdth,wght].ttf",
    "Noto Sans Devanagari": "NotoSansDevanagari[wdth,wght].ttf",
    "Noto Naskh Arabic": "NotoNaskhArabic[wght].ttf",
    "Noto Sans Arabic": "NotoSansArabic[wdth,wght].ttf",
    "Noto Sans JP": "NotoSansJP[wght].ttf",
    "Noto Sans KR": "NotoSansKR[wght].ttf",
}
"""Font family name (as libass and the captions config use it) -> file in the fonts folder."""
LICENCE_FILE = "OFL.txt"
DEFAULT_FAMILY = "Noto Sans"


def fonts_dir() -> Path:
    override = os.environ.get("CCS_FONTS_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "assets" / "fonts"


def font_file(family: str) -> Path | None:
    """The file of a family, or ``None`` when it is not vendored (or missing on disk)."""
    name = FONT_FILES.get(family)
    if name is None:
        return None
    path = fonts_dir() / name
    return path if path.is_file() else None


def missing_fonts() -> list[str]:
    """Families from :data:`FONT_FILES` whose file is not in the fonts folder."""
    return [family for family in FONT_FILES if font_file(family) is None]


def licence_present() -> bool:
    return (fonts_dir() / LICENCE_FILE).is_file()


AnyFont = ImageFont.FreeTypeFont | ImageFont.ImageFont


@lru_cache(maxsize=64)
def load_font(family: str, size_px: int, bold: bool = True) -> AnyFont:
    """A Pillow font for ``family`` at ``size_px``; Noto Sans, then Pillow's default, stand in
    for a family that is missing. Cached: fonts are reused across popups and presets."""
    size = max(8, int(size_px))
    for candidate in (family, DEFAULT_FAMILY):
        path = font_file(candidate)
        if path is None:
            continue
        try:
            font = ImageFont.truetype(str(path), size)
        except OSError:
            continue
        try:
            font.set_variation_by_name("Bold" if bold else "Regular")
        except (OSError, ValueError, AttributeError):
            pass  # a static font, or FreeType without variation support: keep the default
        return font
    return ImageFont.load_default(size=size)

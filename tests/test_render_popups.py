"""Popup PNGs drawn by Pillow: size scales with the preset, transparency, placement."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from cashcow_studio.render.fonts import FONT_FILES, font_file, load_font, missing_fonts
from cashcow_studio.render.popups import (
    brand_colour,
    parse_colour,
    popup_position,
    popup_scale,
    render_popup_png,
    shape_text,
    text_colour_for,
)
from cashcow_studio.render.presets import PopupLook
from test_render_fixtures import make_render_tmp_fixture

render_tmp = make_render_tmp_fixture()


def test_vendored_fonts_are_present_and_load_bold() -> None:
    assert missing_fonts() == []
    for family in FONT_FILES:
        assert font_file(family) is not None
    font = load_font("Noto Sans", 64, bold=True)
    assert font.getbbox("Five every morning")[2] > 300
    fallback = load_font("No Such Family", 32)
    assert fallback.getbbox("x")[2] > 0


def test_popup_scale_per_preset() -> None:
    assert popup_scale(1920, 1080) == 1.0
    assert popup_scale(1080, 1920) == 1.0
    assert popup_scale(1280, 720) == pytest.approx(2 / 3)
    assert popup_scale(854, 480) == pytest.approx(480 / 1080)
    assert popup_scale(3840, 2160) == 2.0


def test_popup_png_size_scales_with_the_preset(render_tmp: Path) -> None:
    look = PopupLook(font_px=64, padding_px=24)
    full = render_popup_png("Five every morning", render_tmp / "full.png", scale=1.0, look=look)
    small = render_popup_png("Five every morning", render_tmp / "small.png", scale=2 / 3,
                             look=look)
    big = render_popup_png("Five every morning", render_tmp / "big.png", scale=2.0, look=look)
    assert full[1] >= 64 + 2 * 24  # at least the font height plus the padding
    assert full[0] > full[1]  # a short phrase is wider than tall
    assert small[0] / full[0] == pytest.approx(2 / 3, abs=0.08)
    assert small[1] / full[1] == pytest.approx(2 / 3, abs=0.08)
    assert big[0] / full[0] == pytest.approx(2.0, abs=0.15)
    with Image.open(render_tmp / "full.png") as image:
        assert image.mode == "RGBA" and image.size == full
        assert image.getpixel((0, 0))[3] == 0  # rounded corner is transparent
        centre = image.getpixel((image.width // 2, image.height - 4))
        assert centre[3] > 200 and centre[:3] == parse_colour("#1F3864")


def test_long_popups_wrap_to_two_lines(render_tmp: Path) -> None:
    text = "The waiter who never forgot a single face"
    one_line = render_popup_png(text, render_tmp / "one.png", scale=1.0)
    wrapped = render_popup_png(text, render_tmp / "two.png", scale=1.0, frame_width=1280)
    assert wrapped[0] < one_line[0] and wrapped[1] > one_line[1]
    assert wrapped[0] <= 1280 * 0.8 + 1


def test_popup_position_keeps_bottom_popups_above_the_captions() -> None:
    frame = (1920, 1080)
    popup = (400, 120)
    assert popup_position("top-left", popup, frame, margin_px=60) == (60, 60)
    assert popup_position("top-right", popup, frame, margin_px=60) == (1460, 60)
    assert popup_position("bottom-left", popup, frame, margin_px=60) == (60, 900)
    assert popup_position("bottom-left", popup, frame, margin_px=60, bottom_reserved_px=200) == (
        60, 700,
    )
    assert popup_position("center", popup, frame, margin_px=60) == (760, 480)
    assert popup_position("weird", popup, frame, margin_px=60) == (60, 900)


def test_colours() -> None:
    assert parse_colour("#1F3864") == (31, 56, 100)
    assert parse_colour("#FFF") == (255, 255, 255)
    assert parse_colour("nope") == parse_colour("#1F3864")
    assert text_colour_for((31, 56, 100)) == (255, 255, 255)
    assert text_colour_for((255, 192, 0)) == (20, 20, 20)
    assert brand_colour(["#1F3864", "#FFC000"]) == "#1F3864"
    assert brand_colour(["not-a-colour"]) == "#1F3864" and brand_colour(None) == "#1F3864"
    assert brand_colour(["#BAD"]) == "#BAD"  # three hex digits are a colour too


def test_shape_text_leaves_latin_alone_and_handles_arabic() -> None:
    assert shape_text("Five every morning") == "Five every morning"
    shaped = shape_text("مرحبا بالعالم")
    assert isinstance(shaped, str) and shaped

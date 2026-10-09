"""Export thumbnails: the pHash maths on synthetic pictures, the template cache in the
channel folder (and the neutral fallback), fonts and colours, the headline options and the
Pillow output sizes of the three variants in 16:9 and 9:16. No network, no image tool."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from cashcow_studio.export import _phash
from cashcow_studio.export import thumbnail as thumbs
from cashcow_studio.llm import MockLLMClient
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.export import ThumbnailTemplate, ThumbnailTextBlock

SLUG = "kind-ledger"
TITLE = "The Waiter Who Never Forgot a Face"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def make_channel(**thumbnail: Any) -> Channel:
    return Channel.model_validate({
        "slug": SLUG,
        "channel": {"name": "Kind Ledger", "niche": "Kindness stories",
                    "brand_colors": ["#1F3864", "#FFC000"]},
        "thumbnail": {"headline_colors": "#FFFFFF on #1F3864, accent #FFC000",
                      "max_headline_words": 4, **thumbnail},
    })


def picture(path: Path, seed: int = 0, size: tuple[int, int] = (640, 360)) -> Path:
    """A synthetic picture with a few shapes; ``seed`` moves them so pictures differ."""
    image = Image.new("RGB", size, (30 + seed * 20 % 200, 40, 90))
    draw = ImageDraw.Draw(image)
    offset = (seed * 53) % 200
    draw.rectangle((offset + 40, 40, offset + 260, 300), fill=(230, 190, 60))
    draw.ellipse((400 - offset, 60, 600 - offset, 260), fill=(200, 50, 50))
    draw.rectangle((30, 250 + (seed % 3) * 20, 340, 330), fill=(255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG" if path.suffix == ".jpg" else "PNG")
    return path


class TemplateLLM:
    """A model client whose ``analyze_image`` returns a fixed layout and counts calls."""

    def __init__(self, template: ThumbnailTemplate | None = None, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.template = template or ThumbnailTemplate(
            subject_box=[0.5, 0.1, 0.45, 0.8],
            text_blocks=[ThumbnailTextBlock(box=[0.05, 0.1, 0.4, 0.3], role="headline",
                                            color="#FFF200", stroke="#000000")],
            palette=["#112233", "#FFF200"],
            mood="tense, dark, dramatic",
            has_face=True,
            layout_notes="Text top left, subject right.",
        )
        self.fail = fail

    async def analyze_image(self, task: str, image_path: Path, prompt: str,
                            schema: type, model: str | None = None, **kw: Any) -> Any:
        self.calls.append({"task": task, "image": str(image_path), "prompt": prompt,
                           "model": model, **kw})
        if self.fail:
            raise RuntimeError("vision is down")
        return self.template


# pHash --------------------------------------------------------------------------------------


def test_phash_distances_on_synthetic_pictures(tmp_path: Path) -> None:
    a = picture(tmp_path / "a.png", 0)
    with Image.open(a) as opened:
        resized = opened.resize((320, 180))
        inverted = Image.eval(opened.convert("RGB"), lambda v: 255 - v)
    b = picture(tmp_path / "b.png", 3)
    h = _phash.phash(a)
    assert 0 <= h < (1 << 64)
    assert _phash.hamming(h, _phash.phash(resized)) <= 2
    assert _phash.hamming(h, _phash.phash(b)) >= 10
    assert _phash.hamming(h, _phash.phash(inverted)) >= 40
    assert _phash.hamming(h, h) == 0
    assert _phash.is_flat(Image.new("RGB", (64, 36), (17, 17, 17)))
    assert not _phash.is_flat(a)


def test_similarity_distance_handles_missing_and_flat_competitors(tmp_path: Path) -> None:
    ours = picture(tmp_path / "ours.png", 1)
    assert thumbs.similarity_distance(ours, None) is None
    assert thumbs.similarity_distance(ours, tmp_path / "missing.jpg") is None
    flat = tmp_path / "flat.jpg"
    Image.new("RGB", (64, 36), (17, 17, 17)).save(flat, "JPEG")
    assert thumbs.similarity_distance(ours, flat) is None
    other = picture(tmp_path / "other.jpg", 4)
    distance = thumbs.similarity_distance(ours, other)
    assert isinstance(distance, int) and distance > 0
    assert thumbs.similarity_distance(ours, ours) == 0


# Colours and fonts ----------------------------------------------------------------------------


def test_hex_colours_and_style_from_channel_config() -> None:
    assert thumbs.hex_colours("#FFFFFF on #1f3864, accent #FFC000 and #abc") == [
        "#FFFFFF", "#1F3864", "#FFC000", "#AABBCC",
    ]
    assert thumbs.hex_colours("no colours here") == []
    assert thumbs.to_rgb("#FFC000") == (255, 192, 0)
    style = thumbs.style_for(make_channel(), thumbs.neutral_template(make_channel()), "English")
    assert style.text == (255, 255, 255)
    assert style.band == (31, 56, 100)
    assert style.accent == (255, 192, 0)
    assert style.uppercase and not style.rtl
    # Two light colours: the text is darkened so it reads on the band.
    light = make_channel(headline_colors="#FFFFFF on #FFFFEE")
    style = thumbs.style_for(light, thumbs.neutral_template(light), "Arabic")
    assert style.text == (17, 17, 17)
    assert style.rtl and not style.uppercase
    assert any("darkened" in n for n in style.notes)


def test_find_font_prefers_the_plain_family_and_the_language(tmp_path: Path) -> None:
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    for name in ("NotoSans[wdth,wght].ttf", "NotoSansArabic[wdth,wght].ttf",
                 "NotoSansDevanagari[wdth,wght].ttf", "Anton-Regular.ttf"):
        (fonts / name).write_bytes(b"")
    assert thumbs.find_font("", "English", fonts).name == "NotoSans[wdth,wght].ttf"
    assert thumbs.find_font("Anton", "English", fonts).name == "Anton-Regular.ttf"
    assert thumbs.find_font("", "Arabic", fonts).name == "NotoSansArabic[wdth,wght].ttf"
    assert thumbs.find_font("", "Hindi", fonts).name == "NotoSansDevanagari[wdth,wght].ttf"
    assert thumbs.find_font("Missing Font", "English", fonts).name == "NotoSans[wdth,wght].ttf"
    assert thumbs.find_font("", "English", tmp_path / "empty") is None
    # A missing or broken font file falls back to Pillow's built-in font.
    assert thumbs.load_font(None, 40) is not None
    assert thumbs.load_font(fonts / "Anton-Regular.ttf", 40) is not None


# Headlines -------------------------------------------------------------------------------------


def test_headline_options_are_three_distinct_short_phrases() -> None:
    options = thumbs.headline_options(TITLE, 4)
    assert len(options) == 3 and len(set(o.casefold() for o in options)) == 3
    assert all(1 <= len(o.split()) <= 4 for o in options)
    assert options[0] == "Never Forgot a Face"
    # The model's suggestions come first, capped and de-duplicated.
    suggested = ["he never forgot one single face", "HE NEVER FORGOT ONE", "A Promise Kept"]
    options = thumbs.headline_options(TITLE, 4, suggested)
    assert options == ["he never forgot one", "A Promise Kept", "Never Forgot a Face"]
    assert thumbs.headline_options("Face", 1) == ["Face", "Story", "Next"]


def test_subject_prompt_mentions_the_title_and_bans_text() -> None:
    channel = make_channel(must_include="one emotional subject", must_avoid="clutter",
                           face="ai_character")
    template = ThumbnailTemplate(mood="warm, nostalgic")
    prompt, negative = thumbs.subject_prompt(TITLE, template, channel)
    assert TITLE in prompt and "warm, nostalgic" in prompt
    assert "No text" in prompt and "illustrated character" in prompt
    assert "one emotional subject" in prompt
    assert negative.startswith("clutter") and "logo" in negative


# Templates ---------------------------------------------------------------------------------------


def test_template_is_read_once_and_cached_in_the_channel_folder(app_env, tmp_path: Path) -> None:
    competitor = picture(tmp_path / "competitor_thumbnail.jpg", 2)
    llm = TemplateLLM()
    channel = make_channel()
    variables = {"competitor_title": "A Waiter's Secret", "channel_name": "Kind Ledger",
                 "niche": "Kindness", "language": "English"}
    first = run(thumbs.resolve_template(
        llm, shared_dir=app_env.shared_dir, channel=channel, video_id="abcdefghijk",
        image_path=competitor, prompt_variables=variables, model="claude-sonnet-5-5",
        project_id="p1",
    ))
    assert first.source == "llm" and first.template.mood == "tense, dark, dramatic"
    assert first.template.text_blocks[0].color == "#FFF200"
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["task"] == "thumbnail_template" and call["model"] == "claude-sonnet-5-5"
    assert call["project_id"] == "p1" and call["stage"] == "export"
    assert "A Waiter's Secret" in call["prompt"] and "layout" in call["prompt"].lower()
    cache = app_env.shared_dir / "channels" / SLUG / "thumbnail-templates" / "abcdefghijk.json"
    assert first.cache_path == cache and cache.is_file()
    saved = json.loads(cache.read_text("utf-8"))
    assert saved["video_id"] == "abcdefghijk" and saved["template"]["has_face"] is True
    assert saved["image_sha256"] == thumbs.file_sha256(competitor)

    second = run(thumbs.resolve_template(
        llm, shared_dir=app_env.shared_dir, channel=channel, video_id="abcdefghijk",
        image_path=competitor, prompt_variables=variables,
    ))
    assert second.source == "cache" and second.template == first.template
    assert len(llm.calls) == 1  # no second model call

    # The channel's template videos lend their cached layout to a project without a thumbnail.
    with_templates = make_channel(template_videos=["https://www.youtube.com/watch?v=abcdefghijk"])
    third = run(thumbs.resolve_template(
        llm, shared_dir=app_env.shared_dir, channel=with_templates, video_id=None,
        image_path=None, prompt_variables=variables,
    ))
    assert third.source == "cache" and "abcdefghijk" in third.note


def test_template_falls_back_to_neutral(app_env, tmp_path: Path) -> None:
    channel = make_channel()
    variables = {"competitor_title": "x", "channel_name": "Kind Ledger", "niche": "k",
                 "language": "English"}
    competitor = picture(tmp_path / "competitor_thumbnail.jpg", 5)

    class NoVision:
        pass

    result = run(thumbs.resolve_template(
        NoVision(), shared_dir=app_env.shared_dir, channel=channel, video_id="v1",
        image_path=competitor, prompt_variables=variables,
    ))
    assert result.source == "neutral" and "cannot read pictures" in result.note
    assert result.template.palette == ["#1F3864", "#FFC000"]
    assert not (app_env.shared_dir / "channels" / SLUG / "thumbnail-templates").exists()

    failing = TemplateLLM(fail=True)
    result = run(thumbs.resolve_template(
        failing, shared_dir=app_env.shared_dir, channel=channel, video_id="v1",
        image_path=competitor, prompt_variables=variables,
    ))
    assert result.source == "neutral" and "vision is down" in result.note

    flat = tmp_path / "flat.jpg"
    Image.new("RGB", (64, 36), (17, 17, 17)).save(flat, "JPEG")
    result = run(thumbs.resolve_template(
        TemplateLLM(), shared_dir=app_env.shared_dir, channel=channel, video_id="v2",
        image_path=flat, prompt_variables=variables,
    ))
    assert result.source == "neutral" and "no detail" in result.note

    result = run(thumbs.resolve_template(
        TemplateLLM(), shared_dir=app_env.shared_dir, channel=channel, video_id=None,
        image_path=None, prompt_variables=variables,
    ))
    assert result.source == "neutral" and "No competitor thumbnail" in result.note


def test_template_boxes_in_pixels_are_normalised_and_mock_client_works(app_env, tmp_path) -> None:
    raw = ThumbnailTemplate.model_validate({
        "subject_box": [640, 72, 600, 576], "text_blocks": [{"box": [50, 400, 500, 250]}],
        "palette": ["#123456", "", "#abcdef"], "mood": "calm",
    })
    assert raw.subject_box == [0.5, 0.1, 0.4688, 0.8]
    assert raw.text_blocks[0].box == [0.0391, 0.5556, 0.3906, 0.3472]
    assert raw.palette == ["#123456", "#abcdef"]
    assert ThumbnailTemplate.model_validate({"subject_box": [0, 0, 0, 0]}).subject_box == \
        thumbs.neutral_template(make_channel()).subject_box

    llm = MockLLMClient(app_data_dir=app_env.app_data_dir)
    if not hasattr(llm, "analyze_image"):
        pytest.skip("the mock has no analyze_image yet")
    competitor = picture(tmp_path / "competitor_thumbnail.jpg", 7)
    result = run(thumbs.resolve_template(
        llm, shared_dir=app_env.shared_dir, channel=make_channel(), video_id="mock1",
        image_path=competitor,
        prompt_variables={"competitor_title": "t", "channel_name": "c", "niche": "n",
                          "language": "English"},
    ))
    assert result.source in ("llm", "neutral")
    assert result.template.text_blocks and result.template.palette


# Rendering ---------------------------------------------------------------------------------------


def test_render_variants_write_both_sizes_and_differ(tmp_path: Path) -> None:
    channel = make_channel()
    template = thumbs.neutral_template(channel)
    style = thumbs.style_for(channel, template, "English")
    out = tmp_path / "08_export"
    subjects = {
        aspect: thumbs.fallback_subject(out / thumbs.SUBJECT_FILES[aspect], aspect, style)
        for aspect in thumbs.ASPECTS
    }
    with Image.open(subjects["16:9"]) as opened:
        assert opened.size == (1280, 720)
    with Image.open(subjects["9:16"]) as opened:
        assert opened.size == (1080, 1920)
    competitor = picture(tmp_path / "competitor.jpg", 9)
    headlines = thumbs.headline_options(TITLE, 4)
    variants = thumbs.render_variants(out, headlines, subjects, template, style, competitor)
    assert [v.id for v in variants] == ["v1", "v2", "v3"]
    assert [v.file for v in variants] == ["thumbnail.png", "thumbnail_v2.png", "thumbnail_v3.png"]
    assert [v.file_shorts for v in variants] == [
        "thumbnail_shorts.png", "thumbnail_shorts_v2.png", "thumbnail_shorts_v3.png",
    ]
    for variant in variants:
        with Image.open(out / variant.file) as opened:
            assert opened.size == (1280, 720) and opened.mode == "RGB"
        with Image.open(out / variant.file_shorts) as opened:
            assert opened.size == (1080, 1920)
        assert isinstance(variant.distance, int) and variant.distance > thumbs.MIN_DISTANCE
    assert (out / "thumbnail.png").read_bytes() != (out / "thumbnail_v2.png").read_bytes()
    # The headline really is drawn: the band area is not the plain subject any more.
    with Image.open(out / "thumbnail.png") as composed, Image.open(subjects["16:9"]) as plain:
        band = composed.crop((100, 480, 1200, 680)).convert("L")
        base = plain.crop((100, 480, 1200, 680)).convert("L")
        assert band.tobytes() != base.tobytes()
        assert max(band.getextrema()) > 200  # white headline pixels


def test_compose_handles_rtl_and_portrait_and_wide_sources(tmp_path: Path) -> None:
    channel = make_channel()
    template = ThumbnailTemplate(
        text_blocks=[ThumbnailTextBlock(box=[0.05, 0.08, 0.5, 0.25], role="title")]
    )
    style = thumbs.style_for(channel, template, "Arabic")
    wide = Image.new("RGB", (2048, 512), (80, 120, 160))
    composed = thumbs.compose(wide, (1080, 1920), "قصة النادل الذي لم ينس", template, style, 2)
    assert composed.size == (1080, 1920)
    tall = Image.new("RGB", (400, 1600), (80, 120, 160))
    composed = thumbs.compose(tall, (1280, 720), "A very long headline that must wrap onto "
                              "two lines", template, style, 1)
    assert composed.size == (1280, 720)
    assert thumbs.cover(tall, (1280, 720), [0.0, 0.0, 1.0, 0.2]).size == (1280, 720)

"""Caption chunking per language and the ASS file (docs/M3-M4-CONTRACT.md section 3)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from cashcow_studio.models.timeline import CaptionCue, CaptionStyle
from cashcow_studio.render.captions import (
    ass_colour,
    ass_time,
    build_ass,
    caption_style_for,
    chunk_cues,
    named_style,
    rules_for,
    wrap_lines,
    write_ass,
)
from cashcow_studio.render.timeline_builder import parse_timing
from test_render_fixtures import make_render_tmp_fixture, make_timing

render_tmp = make_render_tmp_fixture()


@dataclass
class W:
    text: str
    start_s: float
    end_s: float


@dataclass
class S:
    text: str
    start_s: float
    end_s: float
    words: list[W]


def sentence(text: str, start: float, per_word: float = 0.3, pause_after: int | None = None,
             pause_s: float = 0.0) -> S:
    words: list[W] = []
    clock = start
    for index, word in enumerate(text.split()):
        words.append(W(word, round(clock, 3), round(clock + per_word, 3)))
        clock += per_word
        if pause_after is not None and index == pause_after:
            clock += pause_s
    return S(text, start, words[-1].end_s, words)


# Language rules -----------------------------------------------------------------------------


def test_language_rules_pick_the_right_font_and_direction() -> None:
    english = rules_for("English")
    assert english.font == "Noto Sans" and not english.rtl and english.space_dependent
    assert (english.max_chars_per_line, english.max_lines) == (42, 2)
    arabic = rules_for("Arabic")
    assert arabic.font == "Noto Naskh Arabic" and arabic.rtl
    assert arabic.popup_font == "Noto Sans Arabic"
    assert rules_for("Urdu").rtl and rules_for("Urdu").font == "Noto Naskh Arabic"
    assert rules_for("Hindi").font == "Noto Sans Devanagari"
    japanese = rules_for("Japanese")
    assert japanese.font == "Noto Sans JP" and japanese.max_chars_per_line == 13
    assert not japanese.space_dependent
    assert rules_for("Korean").font == "Noto Sans KR"
    assert rules_for("Klingon") == rules_for(None) == english

    style = caption_style_for("Arabic", "bold-white")
    assert isinstance(style, CaptionStyle)
    assert style.font == "Noto Naskh Arabic" and style.rtl and style.bold
    assert style.primary == "#FFFFFF" and style.outline == 2 and style.position == "bottom"
    yellow = caption_style_for("English", "yellow")
    assert yellow.primary == "#FFE34D"
    assert named_style("no-such-style").name == "bold-white"


# Chunking ------------------------------------------------------------------------------------


def test_cues_break_at_sentence_ends_pauses_and_the_character_budget() -> None:
    first = sentence("one two three", 0.0)
    second = sentence("four five six seven eight", first.end_s + 0.1, pause_after=1, pause_s=0.5)
    cues = chunk_cues([first, second], max_chars_per_line=42, max_lines=2)
    assert [c.text for c in cues] == ["one two three", "four five", "six seven eight"]
    assert cues[0].start_s == 0.0 and cues[0].end_s == pytest.approx(0.9)
    assert cues[1].start_s == pytest.approx(1.0) and cues[2].start_s == pytest.approx(2.1)
    # A long run splits into balanced chunks of at most max_chars * max_lines characters.
    long = sentence("alpha beta gamma delta epsilon zeta eta theta iota kappa", 0.0)
    cues = chunk_cues([long], max_chars_per_line=10, max_lines=2)
    assert len(cues) == 3
    for cue in cues:
        assert len(cue.text.replace("\\N", " ")) <= 20
    lengths = [len(c.text.replace("\\N", " ")) for c in cues]
    assert max(lengths) - min(lengths) <= 8  # no tiny tail
    # Cues never overlap the next one.
    for current, following in zip(cues, cues[1:], strict=False):
        assert current.end_s <= following.start_s
    assert chunk_cues([]) == []


def test_words_are_spread_when_a_sentence_has_no_word_times() -> None:
    bare = S("hello wide world", 2.0, 5.0, [])
    cues = chunk_cues([bare], max_chars_per_line=42, max_lines=2)
    assert len(cues) == 1 and cues[0].text == "hello wide world"
    assert cues[0].start_s == 2.0 and cues[0].end_s == pytest.approx(5.0)


def test_japanese_breaks_by_characters_without_spaces() -> None:
    chars = list("今日は天気が良いので公園まで散歩に出かけることにしました")  # 29 chars
    words = [W(ch, round(i * 0.2, 3), round((i + 1) * 0.2, 3)) for i, ch in enumerate(chars)]
    doc = S("".join(chars), 0.0, words[-1].end_s, words)
    rules = rules_for("Japanese")
    cues = chunk_cues([doc], max_chars_per_line=rules.max_chars_per_line,
                      max_lines=rules.max_lines, space_dependent=False)
    assert len(cues) == 2
    for cue in cues:
        assert " " not in cue.text
        lines = cue.text.split("\\N")
        assert all(len(line) <= 13 for line in lines) and len(lines) <= 2
    assert "".join(c.text.replace("\\N", "") for c in cues) == "".join(chars)


def test_japanese_sentence_from_the_voice_pipeline_wraps_by_characters() -> None:
    """The voice tools and the estimator cut on whitespace, so a Japanese sentence arrives
    as one 'word'; the chunker re-splits it into characters to keep the 13-char lines."""
    from cashcow_studio.audio.estimate import estimate_words
    from cashcow_studio.providers.voice.mock import evenly_spaced_timings

    text = "今日は天気が良いので公園まで散歩に出かけることにしましたそして帰りに本屋へ寄りました"
    rules = rules_for("Japanese")
    budget = rules.max_chars_per_line * rules.max_lines
    assert len(text) > budget
    estimated = estimate_words(text, 0.0, 6.0)
    mocked = evenly_spaced_timings(text.split(), 6.0)
    assert len(estimated) == 1 and len(mocked) == 1  # one whitespace token each
    for words in (estimated, mocked):
        cues = chunk_cues(
            [S(text, 0.0, 6.0, list(words))],  # type: ignore[arg-type]
            max_chars_per_line=rules.max_chars_per_line, max_lines=rules.max_lines,
            space_dependent=False,
        )
        assert len(cues) >= 2
        for cue in cues:
            lines = cue.text.split("\\N")
            assert len(cue.text.replace("\\N", "")) <= budget
            assert len(lines) <= rules.max_lines
            assert all(len(line) <= rules.max_chars_per_line for line in lines), cue.text
        assert "".join(c.text.replace("\\N", "") for c in cues) == text
        assert cues[0].start_s == 0.0 and cues[-1].end_s <= 6.0 + 1e-6
        for current, following in zip(cues, cues[1:], strict=False):
            assert current.end_s <= following.start_s


def test_mock_timing_chunks_to_one_cue_per_sentence() -> None:
    timing = parse_timing(make_timing())
    cues = chunk_cues(timing.sentences, max_chars_per_line=42, max_lines=2)
    assert len(cues) == 3
    assert cues[0].text.startswith("The diner opened") and "\\N" in cues[0].text
    assert cues[0].end_s <= cues[1].start_s


def test_wrap_lines_balances_and_respects_max_lines() -> None:
    assert wrap_lines("short", 42, 2) == ["short"]
    lines = wrap_lines("the quick brown fox jumps over the lazy dog again", 20, 2)
    assert len(lines) == 2 and all(len(line) <= 25 for line in lines)
    assert abs(len(lines[0]) - len(lines[1])) <= 6
    assert wrap_lines("あいうえおかきくけこさしすせそ", 5, 3, space_dependent=False) == [
        "あいうえお", "かきくけこ", "さしすせそ",
    ]
    assert wrap_lines("", 10, 2) == [""]


# ASS output --------------------------------------------------------------------------------


def test_ass_colour_and_time_formats() -> None:
    assert ass_colour("#FFFFFF") == "&H00FFFFFF"
    assert ass_colour("#1F3864") == "&H0064381F"
    assert ass_colour("1F3864", alpha=0x80) == "&H8064381F"
    assert ass_colour("nonsense") == "&H00FFFFFF"
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(1.6) == "0:00:01.60"
    assert ass_time(3661.235) == "1:01:01.24"
    assert ass_time(59.999) == "0:00:59.99"


def test_ass_document_has_the_contract_style(render_tmp) -> None:
    cues = [
        CaptionCue(start_s=0.0, end_s=1.6, text="The diner opened\\Nat five"),
        CaptionCue(start_s=1.7, end_s=3.3, text="Nobody {noticed} the stranger"),
        CaptionCue(start_s=3.4, end_s=5.0, text="   "),
    ]
    style = caption_style_for("English", "bold-white")
    text = build_ass(cues, style, "16:9", margin_v_px=60, shadow=0)
    assert "PlayResX: 1920" in text and "PlayResY: 1080" in text
    assert "WrapStyle: 2" in text and "ScaledBorderAndShadow: yes" in text
    style_line = next(line for line in text.splitlines() if line.startswith("Style: "))
    fields = style_line[len("Style: "):].split(",")
    assert fields[0] == "Default" and fields[1] == "Noto Sans" and fields[2] == "48"
    assert fields[3] == "&H00FFFFFF" and fields[5] == "&H00000000"
    assert fields[7] == "-1"  # bold
    assert fields[15] == "1" and fields[16] == "2" and fields[17] == "0"  # border, outline, shadow
    assert fields[18] == "2"  # alignment 2 = bottom centre
    assert fields[21] == "60"  # margin V at 1080p
    dialogues = [line for line in text.splitlines() if line.startswith("Dialogue: ")]
    assert len(dialogues) == 2  # the blank cue is dropped
    assert dialogues[0].endswith("Default,,0,0,0,,{\\an2}The diner opened\\Nat five")
    assert "0:00:00.00,0:00:01.60" in dialogues[0]
    assert "{noticed}" not in dialogues[1] and "(noticed)" in dialogues[1]

    portrait = build_ass(cues, caption_style_for("Arabic"), "9:16")
    assert "PlayResX: 1080" in portrait and "PlayResY: 1920" in portrait
    assert "Noto Naskh Arabic" in portrait

    path = write_ass(render_tmp / "captions.ass", cues, style, "16:9")
    assert path.read_text(encoding="utf-8") == text

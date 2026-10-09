"""Captions: the words of ``timing.json`` grouped into cues, and the ASS file libass burns in.

Rules (docs/M3-M4-CONTRACT.md section 3): a cue holds at most ``max_chars_per_line *
max_lines`` characters, breaks at sentence ends and at pauses longer than ``pause_break_s``,
and uses the language's font and direction from ``config/captions.yaml`` (Arabic is
right-to-left in Noto Naskh Arabic, Hindi uses Noto Sans Devanagari, Japanese breaks by
character count in Noto Sans JP, Korean uses Noto Sans KR, everything else Noto Sans).

The ASS file is written in a reference resolution (1920x1080 or 1080x1920) and libass scales
it to the frame, so the same file serves the proxy and every preset: style ``\\an2`` (bottom
centre), outline 2, shadow 0, 60 px bottom margin at 1080p.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from ..llm.config import load_yaml
from ..models.timeline import CaptionCue, CaptionStyle
from ..storage.settings_store import atomic_write_text

CAPTIONS_FILE = "captions.yaml"
ASS_FILE = "captions.ass"
DEFAULT_PAUSE_S = 0.35
MIN_CUE_S = 0.4
REFERENCE_LANDSCAPE = (1920, 1080)
REFERENCE_PORTRAIT = (1080, 1920)
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


class WordLike(Protocol):
    text: str
    start_s: float
    end_s: float


class SentenceLike(Protocol):
    text: str
    start_s: float
    end_s: float
    words: Sequence[Any]


# Language rules and named styles -----------------------------------------------------------


@dataclass(frozen=True)
class LanguageRules:
    font: str = "Noto Sans"
    popup_font: str = "Noto Sans"
    rtl: bool = False
    max_chars_per_line: int = 42
    max_lines: int = 2
    space_dependent: bool = True
    size: int = 48


@dataclass(frozen=True)
class NamedStyle:
    name: str = "bold-white"
    primary: str = "#FFFFFF"
    outline_colour: str = "#000000"
    bold: bool = True
    outline: int = 2


@lru_cache(maxsize=1)
def load_captions_config() -> dict[str, Any]:
    return load_yaml(CAPTIONS_FILE)


def clear_caches() -> None:
    load_captions_config.cache_clear()


def pause_break_s() -> float:
    try:
        return float(load_captions_config().get("pause_break_s", DEFAULT_PAUSE_S))
    except (TypeError, ValueError):
        return DEFAULT_PAUSE_S


def _rules_from(raw: Any, base: LanguageRules) -> LanguageRules:
    data = raw if isinstance(raw, dict) else {}
    return LanguageRules(
        font=str(data.get("font") or base.font),
        popup_font=str(data.get("popup_font") or data.get("font") or base.popup_font),
        rtl=bool(data.get("rtl", base.rtl)),
        max_chars_per_line=max(4, int(data.get("max_chars_per_line", base.max_chars_per_line))),
        max_lines=max(1, int(data.get("max_lines", base.max_lines))),
        space_dependent=bool(data.get("space_dependent", base.space_dependent)),
        size=max(8, int(data.get("size", base.size))),
    )


def rules_for(language: str | None) -> LanguageRules:
    """The caption rules of a language (``config/captions.yaml``), ``default`` otherwise."""
    config = load_captions_config()
    default = _rules_from(config.get("default"), LanguageRules())
    by_language = config.get("by_language") if isinstance(config.get("by_language"), dict) else {}
    if language and language in by_language:
        return _rules_from(by_language[language], default)
    return default


def named_style(name: str | None) -> NamedStyle:
    config = load_captions_config()
    styles = config.get("styles") if isinstance(config.get("styles"), dict) else {}
    raw = styles.get(name or "")
    if not isinstance(raw, dict):
        raw = styles.get("bold-white") if isinstance(styles.get("bold-white"), dict) else {}
        name = "bold-white"
    base = NamedStyle()
    return NamedStyle(
        name=str(name or base.name),
        primary=str(raw.get("primary") or base.primary),
        outline_colour=str(raw.get("outline_colour") or base.outline_colour),
        bold=bool(raw.get("bold", base.bold)),
        outline=int(raw.get("outline", base.outline)),
    )


def caption_style_for(language: str | None, style_name: str | None = None) -> CaptionStyle:
    """The timeline's caption style for a language and a named look."""
    rules = rules_for(language)
    look = named_style(style_name)
    return CaptionStyle(
        font=rules.font,
        size=rules.size,
        primary=look.primary,
        outline=look.outline,
        position="bottom",
        max_lines=rules.max_lines,
        max_chars_per_line=rules.max_chars_per_line,
        rtl=rules.rtl,
        bold=look.bold,
        outline_colour=look.outline_colour,
    )


# Chunking ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Word:
    text: str
    start_s: float
    end_s: float


def _join(words: Sequence[Word], space_dependent: bool) -> str:
    glue = " " if space_dependent else ""
    return glue.join(w.text for w in words)


def _spread(text: str, start: float, end: float, space_dependent: bool) -> list[Word]:
    """``text`` cut into words (or single characters for a language without spaces), each
    given its share of ``start``..``end`` by length."""
    pieces = text.split(" ") if space_dependent else [c for c in text if not c.isspace()]
    total = sum(len(p) for p in pieces) or 1
    span = max(float(end) - float(start), 0.0)
    clock = float(start)
    spread: list[Word] = []
    for piece in pieces:
        length = span * len(piece) / total
        spread.append(Word(piece, round(clock, 3), round(clock + length, 3)))
        clock += length
    return spread


def _words_of(sentence: SentenceLike, space_dependent: bool) -> list[Word]:
    """The sentence's words; a sentence without word times gets them spread by length.

    For a language without spaces (Japanese) the voice tools and the estimator still cut
    on whitespace, so a whole sentence arrives as one "word": every word is re-split into
    its characters (times spread by position) so the cue and line budgets can be kept."""
    words = [
        Word(str(w.text), float(w.start_s), float(w.end_s))
        for w in (sentence.words or [])
        if str(getattr(w, "text", "")).strip()
    ]
    if words and not space_dependent:
        split: list[Word] = []
        for word in words:
            if len(word.text.strip()) <= 1:
                split.append(Word(word.text.strip(), word.start_s, word.end_s))
            else:
                split.extend(_spread(word.text, word.start_s, word.end_s, False))
        return split
    if words:
        return words
    text = " ".join(str(sentence.text or "").split())
    if not text:
        return []
    return _spread(text, float(sentence.start_s), float(sentence.end_s), space_dependent)


def _split_balanced(words: Sequence[Word], budget: int, space_dependent: bool) -> list[list[Word]]:
    """Cut a run of words into the fewest chunks of at most ``budget`` characters, each
    about the same length (no one-word tail)."""
    if not words:
        return []
    text_len = len(_join(words, space_dependent))
    if text_len <= budget:
        return [list(words)]
    count = max(2, math.ceil(text_len / budget))
    while True:
        target = text_len / count
        chunks: list[list[Word]] = []
        current: list[Word] = []
        for index, word in enumerate(words):
            remaining_chunks = count - len(chunks)
            remaining_words = len(words) - index
            if current and remaining_chunks < remaining_words:
                with_word = len(_join([*current, word], space_dependent))
                without = len(_join(current, space_dependent))
                if with_word > budget or (
                    abs(with_word - target) > abs(without - target) and remaining_chunks > 1
                ):
                    chunks.append(current)
                    current = []
            current.append(word)
        if current:
            chunks.append(current)
        if all(len(_join(c, space_dependent)) <= budget for c in chunks) or count >= len(words):
            return chunks
        count += 1


def chunk_cues(
    sentences: Iterable[SentenceLike],
    *,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
    pause_s: float = DEFAULT_PAUSE_S,
    space_dependent: bool = True,
) -> list[CaptionCue]:
    """Words -> cues of at most ``max_chars_per_line * max_lines`` characters, broken at
    sentence ends and at silences longer than ``pause_s``. Cue text is wrapped into lines
    with ``\\N`` (the ASS line break)."""
    budget = max(4, int(max_chars_per_line)) * max(1, int(max_lines))
    cues: list[CaptionCue] = []
    for sentence in sentences:
        words = _words_of(sentence, space_dependent)
        if not words:
            continue
        runs: list[list[Word]] = [[words[0]]]
        for previous, word in zip(words, words[1:], strict=False):
            if word.start_s - previous.end_s > pause_s + 1e-9:
                runs.append([word])
            else:
                runs[-1].append(word)
        for run in runs:
            for chunk in _split_balanced(run, budget, space_dependent):
                text = "\\N".join(
                    wrap_lines(_join(chunk, space_dependent), max_chars_per_line, max_lines,
                               space_dependent)
                )
                start = chunk[0].start_s
                end = max(chunk[-1].end_s, start + MIN_CUE_S)
                cues.append(CaptionCue(start_s=round(start, 3), end_s=round(end, 3), text=text))
    for current, following in zip(cues, cues[1:], strict=False):
        if current.end_s > following.start_s:
            current.end_s = round(max(following.start_s, current.start_s + 0.1), 3)
    return cues


def wrap_lines(
    text: str, max_chars_per_line: int, max_lines: int, space_dependent: bool = True
) -> list[str]:
    """Break ``text`` into at most ``max_lines`` lines of about ``max_chars_per_line``
    characters, balanced so the lines are of similar length."""
    text = " ".join(text.split()) if space_dependent else text.strip()
    if not text:
        return [""]
    if len(text) <= max_chars_per_line:
        return [text]
    units = text.split(" ") if space_dependent else list(text)
    glue = " " if space_dependent else ""
    lines_wanted = min(max_lines, max(2, math.ceil(len(text) / max_chars_per_line)))
    chunks = _split_units(units, lines_wanted, glue)
    return [glue.join(chunk) for chunk in chunks]


def _split_units(units: list[str], count: int, glue: str) -> list[list[str]]:
    if count <= 1 or len(units) <= 1:
        return [units]
    total = len(glue.join(units))
    target = total / count
    chunks: list[list[str]] = []
    current: list[str] = []
    for index, unit in enumerate(units):
        remaining_chunks = count - len(chunks)
        remaining_units = len(units) - index
        if current and remaining_chunks > 1 and remaining_chunks < remaining_units + 1:
            with_unit = len(glue.join([*current, unit]))
            without = len(glue.join(current))
            if abs(with_unit - target) > abs(without - target):
                chunks.append(current)
                current = []
        current.append(unit)
    if current:
        chunks.append(current)
    return chunks


# ASS output --------------------------------------------------------------------------------


def ass_colour(value: str, alpha: int = 0) -> str:
    """``#RRGGBB`` -> ``&HAABBGGRR`` (libass byte order). Unknown values become white."""
    match = _HEX_RE.match((value or "").strip())
    rgb = match.group(1) if match else "FFFFFF"
    red, green, blue = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


def ass_time(seconds: float) -> str:
    """``H:MM:SS.cc`` (centiseconds, as ASS wants it)."""
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = total % 60
    centis = int(round(secs * 100))
    if centis >= 6000:  # rounding pushed 59.995 to 60.00
        centis = 5999
    return f"{hours}:{minutes:02d}:{centis // 100:02d}.{centis % 100:02d}"


def _clean_text(text: str) -> str:
    """Keep ``\\N`` line breaks, drop characters libass would read as override tags."""
    cleaned = text.replace("\r", "").replace("\n", "\\N")
    return cleaned.replace("{", "(").replace("}", ")")


def build_ass(
    cues: Sequence[CaptionCue],
    style: CaptionStyle,
    aspect: str,
    *,
    margin_v_px: int = 60,
    shadow: int = 0,
    style_name: str = "Default",
) -> str:
    """The whole ASS document for ``cues`` in the reference resolution of ``aspect``."""
    width, height = REFERENCE_PORTRAIT if aspect == "9:16" else REFERENCE_LANDSCAPE
    margin_h = 40 if width >= height else 24
    lines = [
        "[Script Info]",
        "; Written by CashCow Studio",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: {name},{font},{size},{primary},{secondary},{outline_colour},{back},{bold},0,0,0,"
        "100,100,0,0,1,{outline},{shadow},2,{margin_h},{margin_h},{margin_v},1".format(
            name=style_name,
            font=style.font,
            size=int(style.size),
            primary=ass_colour(style.primary),
            secondary=ass_colour("#FF0000"),
            outline_colour=ass_colour(style.outline_colour),
            back=ass_colour("#000000", alpha=0x80),
            bold=-1 if style.bold else 0,
            outline=int(style.outline),
            shadow=int(shadow),
            margin_h=margin_h,
            margin_v=int(margin_v_px),
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        text = _clean_text(cue.text)
        if not text.strip():
            continue
        lines.append(
            f"Dialogue: 0,{ass_time(cue.start_s)},{ass_time(cue.end_s)},{style_name},,0,0,0,,"
            f"{{\\an2}}{text}"
        )
    return "\n".join(lines) + "\n"


def write_ass(
    path: Path,
    cues: Sequence[CaptionCue],
    style: CaptionStyle,
    aspect: str,
    *,
    margin_v_px: int = 60,
    shadow: int = 0,
) -> Path:
    atomic_write_text(Path(path), build_ass(cues, style, aspect, margin_v_px=margin_v_px,
                                            shadow=shadow))
    return Path(path)

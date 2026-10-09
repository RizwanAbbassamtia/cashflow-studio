"""Forced alignment of an own recording to the script: the ``Aligner`` protocol, the
``none`` default (the stage then estimates by text length) and a ``whisperx`` adapter.

WhisperX is optional and heavy; it is imported only inside :meth:`WhisperXAligner.align`,
on the CPU, with the small ``int8`` models. Nothing in this module needs a GPU and the app
runs fine without the package: ``build_aligner("whisperx")`` succeeds and ``available()``
says whether it can actually run.

Selection: ``CCS_VOICE_ALIGNER`` (``none`` or ``whisperx``), else ``settings.voice.aligner``
when that setting exists, else ``none``.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .errors import AudioError
from .estimate import word_tokens

log = logging.getLogger(__name__)

ALIGNER_ENV = "CCS_VOICE_ALIGNER"
DEFAULT_ALIGNER = "none"
WHISPERX_MODEL = "base"
WHISPERX_DEVICE = "cpu"
WHISPERX_COMPUTE_TYPE = "int8"

LANGUAGE_CODES: dict[str, str] = {
    "english": "en", "spanish": "es", "hindi": "hi", "arabic": "ar", "portuguese": "pt",
    "indonesian": "id", "japanese": "ja", "german": "de", "french": "fr", "russian": "ru",
    "vietnamese": "vi", "turkish": "tr", "korean": "ko", "urdu": "ur", "italian": "it",
}


def language_code(language: str | None, default: str = "en") -> str:
    """``"English"`` -> ``"en"``; a two-letter code passes through."""
    text = (language or "").strip().lower()
    if not text:
        return default
    if text in LANGUAGE_CODES:
        return LANGUAGE_CODES[text]
    if len(text) == 2 and text.isalpha():
        return text
    return default


class AlignerUnavailable(AudioError):
    """The chosen aligner cannot run on this PC (package missing or model download failed)."""


@dataclass(frozen=True)
class AlignedWord:
    text: str
    start_s: float
    end_s: float
    confidence: float = 1.0


@dataclass
class AlignmentResult:
    words: list[AlignedWord] = field(default_factory=list)
    engine: str = "none"
    language: str = ""


@runtime_checkable
class Aligner(Protocol):
    """Given the recording and the exact text spoken, return where every word is."""

    name: str

    def available(self) -> bool: ...

    def align(self, audio_path: Path, text: str, language: str) -> AlignmentResult: ...


class NoneAligner:
    """The default: no alignment. The stage estimates the timing by text length."""

    name = "none"

    def available(self) -> bool:
        return True

    def align(self, audio_path: Path, text: str, language: str) -> AlignmentResult:
        raise AlignerUnavailable(
            "No aligner is set up, so the timing of the recording is estimated from the text."
        )


class WhisperXAligner:
    """Forced alignment with WhisperX's wav2vec2 models on the CPU (no transcription pass:
    the text is known, so only the alignment model runs)."""

    name = "whisperx"

    def __init__(
        self,
        model_name: str = WHISPERX_MODEL,
        device: str = WHISPERX_DEVICE,
        compute_type: str = WHISPERX_COMPUTE_TYPE,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type

    def available(self) -> bool:
        return importlib.util.find_spec("whisperx") is not None

    def align(self, audio_path: Path, text: str, language: str) -> AlignmentResult:
        if not self.available():
            raise AlignerUnavailable(
                "WhisperX is not installed, so the recording cannot be aligned word by word. "
                "Install it with: pip install whisperx (CPU build), or leave the aligner on "
                "'none' to use estimated timing."
            )
        try:
            import whisperx  # type: ignore[import-not-found]  # noqa: PLC0415 - optional, heavy
        except Exception as exc:  # noqa: BLE001 - any import problem means "not available"
            raise AlignerUnavailable(f"WhisperX could not be loaded: {exc}") from exc
        code = language_code(language)
        audio_path = Path(audio_path)
        if not audio_path.is_file():
            raise AudioError(f"The recording {audio_path} does not exist.")
        try:
            audio = whisperx.load_audio(str(audio_path))
            duration = float(len(audio)) / 16000.0
            model, metadata = whisperx.load_align_model(language_code=code, device=self.device)
            segments = [{"text": text, "start": 0.0, "end": duration}]
            aligned = whisperx.align(
                segments, model, metadata, audio, self.device, return_char_alignments=False
            )
        except AlignerUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - model download or runtime failure
            raise AlignerUnavailable(f"WhisperX could not align the recording: {exc}") from exc
        return AlignmentResult(words=words_from_whisperx(aligned), engine=self.name, language=code)


def words_from_whisperx(aligned: Any) -> list[AlignedWord]:
    """``{"segments": [{"words": [{"word", "start", "end", "score"}]}]}`` -> AlignedWord list.

    Words WhisperX could not place (no ``start``) are interpolated between their neighbours.
    """
    raw: list[dict[str, Any]] = []
    for segment in (aligned or {}).get("segments") or []:
        for word in segment.get("words") or []:
            if isinstance(word, dict) and str(word.get("word", "")).strip():
                raw.append(word)
    return interpolate_missing(
        [
            (
                str(w.get("word", "")).strip(),
                _float_or_none(w.get("start")),
                _float_or_none(w.get("end")),
                float(w.get("score", 1.0) or 0.0),
            )
            for w in raw
        ]
    )


def _float_or_none(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def interpolate_missing(
    rows: Sequence[tuple[str, float | None, float | None, float]],
) -> list[AlignedWord]:
    """Fill in words without times by sharing the gap between the placed neighbours."""
    if not rows:
        return []
    placed = [i for i, row in enumerate(rows) if row[1] is not None and row[2] is not None]
    if not placed:
        return []
    words: list[AlignedWord] = []
    i = 0
    while i < len(rows):
        text, start, end, score = rows[i]
        if start is not None and end is not None:
            words.append(AlignedWord(text, round(start, 6), round(max(end, start), 6), score))
            i += 1
            continue
        j = i
        while j < len(rows) and (rows[j][1] is None or rows[j][2] is None):
            j += 1
        gap_start = words[-1].end_s if words else (rows[j][1] if j < len(rows) else 0.0)
        gap_end = rows[j][1] if j < len(rows) else gap_start
        gap_end = max(float(gap_end or 0.0), float(gap_start or 0.0))
        count = j - i
        step = (gap_end - gap_start) / count if count else 0.0
        for k in range(count):
            s = gap_start + step * k
            words.append(AlignedWord(rows[i + k][0], round(s, 6), round(s + step, 6), 0.2))
        i = j
    return words


def assign_words_to_sentences(
    sentences: Sequence[tuple[str, str]], words: Sequence[AlignedWord]
) -> list[list[AlignedWord]]:
    """Hand the aligned words out to the sentences in order, by each sentence's word count.

    The aligner was given the sentences joined in order, so its words come back in the same
    order; a sentence takes as many as its text has. Missing words at the end leave the
    last sentences with fewer (the stage then estimates inside their span).
    """
    out: list[list[AlignedWord]] = []
    cursor = 0
    for _sid, text in sentences:
        count = len(word_tokens(text))
        chunk = list(words[cursor:cursor + count])
        cursor += count
        out.append(chunk)
    return out


def aligner_choice(settings: Any = None, override: str | None = None) -> str:
    for candidate in (
        override,
        os.environ.get(ALIGNER_ENV),
        getattr(getattr(settings, "voice", None), "aligner", None),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return DEFAULT_ALIGNER


def build_aligner(name: str | None = None, settings: Any = None) -> Aligner:
    """``none`` or ``whisperx``; an unknown name logs a warning and gives ``none``."""
    chosen = aligner_choice(settings, name)
    if chosen == "whisperx":
        return WhisperXAligner()
    if chosen != "none":
        log.warning("Unknown voice aligner %r; using 'none' (estimated timing).", chosen)
    return NoneAligner()

"""Voice timing: ``05_voice/timing.json`` (:class:`TimingDoc`), the voice manifest, the
approval edits and the review payload. See docs/M3-M4-CONTRACT.md section 1.

``timing.json`` is the clock for everything after the voice stage: scenes, images, popups,
captions and music all read their start and end times from it. Sentence boundaries are
exact (they come from the sample offsets of the concatenated WAV, or from the aligner);
word times are exact only when the voice tool or the aligner returned them, otherwise they
are estimated inside each sentence by character length.

The front end mirrors these in ``frontend/src/types/timing.ts``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

TimingSource = Literal["provider_word", "provider_sentence", "estimated", "aligned"]
"""Where the times in ``timing.json`` come from:

* ``provider_word``      the voice tool returned word timestamps for every sentence;
* ``provider_sentence``  the audio was made per sentence (boundaries exact), words estimated;
* ``estimated``          an own recording with no aligner: everything is spread by text length;
* ``aligned``            an own recording aligned by a forced aligner (``whisperx``).
"""
WordSource = Literal["provider", "estimated", "aligner"]
TimingConfidence = Literal["high", "medium", "low"]

DEFAULT_SAMPLE_RATE = 48000


class TimedWord(BaseModel):
    text: str
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    confidence: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> TimedWord:
        if self.end_s < self.start_s:
            raise ValueError(
                f"the word {self.text!r} ends ({self.end_s}) before it starts ({self.start_s})"
            )
        return self

    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 6)


class TimedSentence(BaseModel):
    id: str
    text: str
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    words: list[TimedWord] = []
    words_source: WordSource = "estimated"
    start_frame: int | None = Field(default=None, description="sample offset in voice.wav")
    end_frame: int | None = None
    audio_path: str = Field(default="", description="05_voice/sentences/<id>.wav")

    @model_validator(mode="after")
    def _ordered(self) -> TimedSentence:
        if self.end_s < self.start_s:
            raise ValueError(
                f"sentence {self.id} ends ({self.end_s}) before it starts ({self.start_s})"
            )
        return self

    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 6)


class TimingDoc(BaseModel):
    """``05_voice/timing.json``."""

    sample_rate: int = Field(default=DEFAULT_SAMPLE_RATE, ge=8000)
    duration_s: float = Field(ge=0)
    source: TimingSource
    sentences: list[TimedSentence]
    timing_confidence: TimingConfidence = "medium"
    provider: str = ""
    model: str = ""
    voice_id: str = ""
    language: str = ""
    aligner: str = Field(default="none", description="aligner used for an own recording")
    own_recording: bool = False
    generated_at: datetime | None = None
    warnings: list[str] = []
    schema_version: int = 1

    @property
    def words(self) -> list[TimedWord]:
        return [w for s in self.sentences for w in s.words]

    def sentence(self, sentence_id: str) -> TimedSentence | None:
        for item in self.sentences:
            if item.id == sentence_id:
                return item
        return None


class VoiceSentenceRecord(BaseModel):
    """One row of ``05_voice/voice.json``: how a sentence's audio was made."""

    id: str
    text: str
    cache_key: str = ""
    cached: bool = False
    duration_s: float = 0.0
    characters: int = 0
    cost_usd: float = 0.0
    words_source: WordSource = "estimated"


class VoiceManifest(BaseModel):
    """``05_voice/voice.json``: provenance of the audio (provider, voice, cost, consent)."""

    project_id: str
    provider: str
    model: str = ""
    voice_id: str = ""
    language: str = ""
    speed: float = 1.0
    style: str = ""
    own_recording: bool = False
    source_file: str = Field(default="", description="the own recording this came from")
    aligner: str = "none"
    sample_rate: int = DEFAULT_SAMPLE_RATE
    duration_s: float = 0.0
    cost_usd: float = 0.0
    sentences: list[VoiceSentenceRecord] = []
    consent: dict[str, Any] | None = None
    consent_ref: str = ""
    generated_at: datetime
    gate_results: list[dict[str, Any]] = []
    warnings: list[str] = []
    schema_version: int = 1


# Edits and the review payload ----------------------------------------------------------------


class VoiceApproveEdits(BaseModel):
    """``edits`` on ``POST /stage/voice/approve`` (and kept across a redo)."""

    re_record: list[str] | None = Field(
        default=None, description="sentence ids to synthesise again (the cache is bypassed)"
    )
    audio_path: str | None = Field(
        default=None,
        description="an own recording: absolute path, or a path inside the project folder",
    )
    timing: TimingDoc | None = Field(default=None, description="hand-nudged timing.json")
    override_gates: bool = False


class VoiceReviewSentence(BaseModel):
    id: str
    text: str
    speech_text: str = ""
    start_s: float
    end_s: float
    duration_s: float
    play_url: str
    words: int = 0
    words_source: WordSource = "estimated"
    cached: bool = False
    cost_usd: float = 0.0
    flags: list[str] = []


class VoiceReviewPayload(BaseModel):
    stage: Literal["voice"] = "voice"
    cost_kind: Literal["voice"] = "voice"
    provider: str
    model: str = ""
    voice_id: str = ""
    language: str = ""
    duration_s: float
    target_s: float | None = None
    sample_rate: int = DEFAULT_SAMPLE_RATE
    source: TimingSource
    timing_confidence: TimingConfidence
    own_recording: bool = False
    aligner: str = "none"
    voice_url: str
    timing_url: str
    sentences: list[VoiceReviewSentence]
    warnings: list[str] = []
    gate_results: list[dict[str, Any]] = []
    cost_usd: float = 0.0
    cached_sentences: int = 0
    synthesized_sentences: int = 0
    consent: dict[str, Any] | None = None

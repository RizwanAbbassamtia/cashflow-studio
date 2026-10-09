"""Mock voice: silent audio of the right length plus evenly spaced word timings.

``CFS_VOICE_PROVIDER=mock`` (the default) selects it, so tests and demos run offline and
never bill anything. The WAV is 48 kHz mono 16-bit silence; its duration comes from the word
count and the speaking rate, the same arithmetic the storyboard stage uses for scene
lengths, so the mock timings line up with the estimated scenes.
"""

from __future__ import annotations

import wave
from pathlib import Path

from slugify import slugify

from ..base import CostEstimate, ProviderError, ProviderHealth
from .base import (
    CloneConsent,
    ProviderCapabilities,
    SynthRequest,
    SynthResult,
    VoiceInfo,
    WordTiming,
    estimate_speech_seconds,
)

MOCK_ID = "mock"
MOCK_MODEL = "mock-silence"
MOCK_VOICES = [
    VoiceInfo(
        id="mock-narrator",
        name="Mock narrator",
        language="English",
        description="Silent stand-in voice for offline runs.",
    ),
    VoiceInfo(
        id="mock-clone",
        name="Mock clone",
        language="English",
        is_clone=True,
        description="Pretends to be a cloned voice.",
    ),
]
BYTES_PER_SAMPLE = 2  # 16-bit PCM
WRITE_CHUNK_FRAMES = 48000  # one second at 48 kHz per write, so long texts stay cheap


def default_capabilities() -> ProviderCapabilities:
    return ProviderCapabilities(
        id=MOCK_ID,
        name="Mock voice (offline)",
        adapter="ready",
        clone=True,
        languages=[],
        timestamp_granularity="word",
        billing_unit="free",
        price_per_unit_usd=0.0,
        max_chars=None,
        notes="Writes silent audio with evenly spaced word timings. For tests and demos.",
    )


def write_silence_wav(path: Path, duration_s: float, sample_rate_hz: int) -> int:
    """Write mono 16-bit silence; returns the number of frames actually written."""
    frames = max(round(duration_s * sample_rate_hz), 1)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(BYTES_PER_SAMPLE)
        handle.setframerate(sample_rate_hz)
        remaining = frames
        while remaining > 0:
            chunk = min(remaining, WRITE_CHUNK_FRAMES)
            handle.writeframes(bytes(chunk * BYTES_PER_SAMPLE))
            remaining -= chunk
    return frames


def evenly_spaced_timings(words: list[str], duration_s: float) -> list[WordTiming]:
    """Give every word the same slot across ``duration_s``; the last word ends at the end."""
    if not words:
        return []
    slot = duration_s / len(words)
    timings: list[WordTiming] = []
    for index, word in enumerate(words):
        start = round(index * slot, 3)
        end = duration_s if index == len(words) - 1 else round((index + 1) * slot, 3)
        timings.append(
            WordTiming(text=word, start_s=start, end_s=end, confidence=0.5, source="estimated")
        )
    return timings


class MockVoiceProvider:
    id = MOCK_ID

    def __init__(self, capabilities: ProviderCapabilities | None = None) -> None:
        self.capabilities = capabilities or default_capabilities()

    def list_voices(self) -> list[VoiceInfo]:
        return [voice.model_copy() for voice in MOCK_VOICES]

    def create_clone(self, name: str, samples: list[Path], consent: CloneConsent) -> VoiceInfo:
        clean = slugify(name or "", max_length=40) or "voice"
        return VoiceInfo(
            id=f"mock-clone-{clean}",
            name=name or "Mock clone",
            is_clone=True,
            description=f"Mock clone from {len(samples)} sample(s); consent recorded by "
            f"{consent.consented_by} for {consent.owner_name}.",
        )

    def synthesize(self, request: SynthRequest) -> SynthResult:
        words = request.text.split()
        if not words:
            raise ProviderError("There is nothing to say: the sentence text is blank.")
        wanted = estimate_speech_seconds(request.text, request.speaking_rate_wpm, request.speed)
        frames = write_silence_wav(request.output_path, wanted, request.sample_rate_hz)
        duration = round(frames / request.sample_rate_hz, 3)
        return SynthResult(
            path=Path(request.output_path),
            duration_s=duration,
            sample_rate_hz=request.sample_rate_hz,
            channels=1,
            text=request.text,
            words=evenly_spaced_timings(words, duration),
            timing_source="estimated",
            provider=self.id,
            model=MOCK_MODEL,
            voice_id=request.voice_id or MOCK_VOICES[0].id,
            characters=len(request.text),
            cost_usd=0.0,
            sentence_id=request.sentence_id,
        )

    def estimate_cost(self, text: str) -> CostEstimate:
        return CostEstimate(
            provider=self.id,
            unit="free",
            units=len(text),
            cost_usd=0.0,
            note="The mock voice is free.",
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.id,
            kind="voice",
            status="ok",
            detail="Ready. Writes silent audio for offline runs; no key needed.",
            model=MOCK_MODEL,
        )

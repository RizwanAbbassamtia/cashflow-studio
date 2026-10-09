"""The voice stage on the mock and on a fake cloud voice: per-sentence caching (a redo only
re-bills changed sentences), sample-accurate concat offsets, provider versus estimated word
timings, the consent and duration gates, the own-recording path with the ``none`` aligner,
the re_record / audio_path / timing edits, cost booking and the review payload. One test runs
the stage through the engine and the HTTP API so the play URLs are proven to serve audio."""

from __future__ import annotations

import asyncio
import json
import time
import wave
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cashcow_studio.app import create_app
from cashcow_studio.audio import ffmpeg as ff
from cashcow_studio.audio.wav import read_wav_info
from cashcow_studio.config import Settings
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.project import Project, ProjectSource
from cashcow_studio.models.script import ScriptDoc, ScriptSection, SpeechDoc, SpeechSentence
from cashcow_studio.models.timing import TimedSentence, TimedWord, TimingDoc
from cashcow_studio.pipeline.stages import voice as voice_stage
from cashcow_studio.pipeline.stages.base import GateBlocked, StageContext, StageError
from cashcow_studio.pipeline.stages.script import make_paragraph, write_model
from cashcow_studio.pipeline.stages.voice import (
    SENTENCE_GAP_S,
    VoiceStage,
    consent_status,
    files_url,
    flag_words,
    load_review_payload,
    resolve_audio_path,
    timing_from_recording,
)
from cashcow_studio.providers.base import CostEstimate, ProviderError, ProviderHealth
from cashcow_studio.providers.registry import build_providers
from cashcow_studio.providers.voice.base import (
    ProviderCapabilities,
    SynthRequest,
    SynthResult,
    WordTiming,
)
from cashcow_studio.providers.voice.mock import write_silence_wav
from conftest import AppEnv, channel_payload

SR = 48000
TEXTS = [
    "The diner opened at five every morning.",
    "Nobody noticed the stranger in the corner booth.",
    "He ordered coffee and watched the door for an hour.",
]


# Helpers ------------------------------------------------------------------------------------


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def make_project(root: Path, texts: list[str] | None = None, spoken: dict[str, str] | None = None,
                 fmt: str = "long") -> Path:
    """A project folder with script.json and speech.json for the given sentences."""
    folder = root / "proj"
    (folder / "03_script").mkdir(parents=True, exist_ok=True)
    (folder / "05_voice").mkdir(parents=True, exist_ok=True)
    paragraph = make_paragraph(0, 0, " ".join(texts or TEXTS), False)
    script = ScriptDoc(
        title="The diner", language="English", format=fmt, target_words=30,  # type: ignore[arg-type]
        sections=[ScriptSection(id="sec-01", name="Hook", paragraphs=[paragraph])],
        word_count=30, model="mock", generated_at=datetime.now(UTC),
    )
    write_model(folder / "03_script" / "script.json", script)
    spoken = spoken or {}
    speech = SpeechDoc(
        language="English", model="mock", generated_at=datetime.now(UTC),
        sentences=[
            SpeechSentence(id=s.id, text=s.text, speech_text=spoken.get(s.id, s.text))
            for s in script.sentences()
        ],
    )
    write_model(folder / "03_script" / "speech.json", speech)
    return folder


def sentence_ids(folder: Path) -> list[str]:
    doc = ScriptDoc.model_validate_json((folder / "03_script" / "script.json").read_text("utf-8"))
    return [s.id for s in doc.sentences()]


def make_settings(root: Path) -> Settings:
    return Settings(
        app_data_dir=root / "app", shared_dir=root / "shared", projects_dir=root / "projects",
        exports_dir=root / "exports",
    )


def make_channel(**voice: Any) -> Channel:
    data: dict[str, Any] = {
        "slug": "kind-ledger",
        "channel": {"name": "Kind Ledger", "long_form_minutes": 1, "shorts_seconds": 20},
        "voice": {"tool": "other", **voice},
    }
    return Channel.model_validate(data)


def make_ctx(
    folder: Path, settings: Settings, channel: Channel, provider: Any, **extra: Any
) -> StageContext:
    now = datetime.now(UTC)
    project = Project(
        id="p-voice", channel_slug=channel.slug, topic_slug="the-diner", title="The diner",
        created_at=now, updated_at=now, folder=str(folder),
        source=ProjectSource(kind="own_topic", topic_text="The diner"),
        format=extra.pop("format", "long"),
    )
    write_model(folder / "job.json", project)
    ctx = StageContext(
        project=project, channel=channel, settings=settings, folder=folder,
        providers={"voice": provider}, **extra,
    )
    return ctx


class FakeVoice:
    """A pretend cloud voice: silence per word, bills per character, optional word rows."""

    id = "fakecloud"

    def __init__(self, *, words: bool = True, price: float = 0.001, fail_on: int | None = None,
                 seconds_per_word: float = 0.4) -> None:
        self.calls: list[SynthRequest] = []
        self.words = words
        self.price = price
        self.fail_on = fail_on
        self.seconds_per_word = seconds_per_word
        self.capabilities = ProviderCapabilities(
            id=self.id, name="Fake cloud voice", adapter="ready", clone=True,
            timestamp_granularity="word" if words else "none",
            billing_unit="character", price_per_unit_usd=price,
        )

    def synthesize(self, request: SynthRequest) -> SynthResult:
        self.calls.append(request)
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise ProviderError("the pretend cloud is down")
        tokens = request.text.split()
        duration = round(len(tokens) * self.seconds_per_word, 3)
        frames = write_silence_wav(request.output_path, duration, request.sample_rate_hz)
        duration = round(frames / request.sample_rate_hz, 6)
        rows: list[WordTiming] = []
        if self.words:
            slot = duration / len(tokens)
            rows = [
                WordTiming(text=t, start_s=round(i * slot, 3), end_s=round((i + 1) * slot, 3),
                           confidence=0.95, source="provider")
                for i, t in enumerate(tokens)
            ]
        return SynthResult(
            path=request.output_path, duration_s=duration, sample_rate_hz=request.sample_rate_hz,
            text=request.text, words=rows, timing_source="provider" if rows else "none",
            provider=self.id, model="fake-1", voice_id=request.voice_id,
            characters=len(request.text), cost_usd=round(len(request.text) * self.price, 6),
            sentence_id=request.sentence_id,
        )

    def estimate_cost(self, text: str) -> CostEstimate:
        return CostEstimate(provider=self.id, unit="character", units=len(text),
                            cost_usd=round(len(text) * self.price, 6))

    def list_voices(self) -> list[Any]:
        return []

    def create_clone(self, name: str, samples: list[Path], consent: Any) -> Any:
        raise ProviderError("not in this test")

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.id, kind="voice", status="ok", detail="fake")


def write_wav(path: Path, seconds: float, *, sample_rate: int = SR, channels: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = round(seconds * sample_rate)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(bytes(frames * 2 * channels))
    return path


def timing_of(folder: Path) -> TimingDoc:
    return TimingDoc.model_validate_json((folder / "05_voice" / "timing.json").read_text("utf-8"))


def wav_seconds(path: Path) -> float:
    return read_wav_info(path).duration_s


# The mock run ----------------------------------------------------------------------------------


def test_mock_run_writes_voice_timing_and_payload(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    mock = build_providers(voice="mock")["voice"]
    ctx = make_ctx(folder, make_settings(tmp_path), make_channel(), mock)
    result = run(VoiceStage().run(ctx))

    stage_dir = folder / "05_voice"
    for name in ("voice.wav", "timing.json", "voice.json"):
        assert (stage_dir / name).is_file(), name
    ids = sentence_ids(folder)
    for sid in ids:
        assert (stage_dir / "sentences" / f"{sid}.wav").is_file()
    doc = timing_of(folder)
    assert doc.sample_rate == SR and doc.source == "provider_sentence"
    assert doc.timing_confidence == "medium" and doc.provider == "mock"
    # Sentence boundaries are the concat offsets: previous end + the gap, exactly.
    clock = 0.0
    for sid, sentence in zip(ids, doc.sentences, strict=True):
        assert sentence.id == sid
        assert sentence.start_s == pytest.approx(clock, abs=1e-6)
        cut = stage_dir / "sentences" / f"{sid}.wav"
        assert sentence.end_s == pytest.approx(clock + wav_seconds(cut), abs=1e-6)
        assert sentence.end_frame - sentence.start_frame == read_wav_info(cut).frames
        assert sentence.words and sentence.words[0].start_s == sentence.start_s
        assert sentence.words[-1].end_s == pytest.approx(sentence.end_s)
        assert sentence.words_source == "estimated"
        clock = sentence.end_s + SENTENCE_GAP_S
    assert doc.duration_s == pytest.approx(wav_seconds(stage_dir / "voice.wav"))
    assert doc.duration_s == pytest.approx(doc.sentences[-1].end_s)
    # 7 words at 150 wpm = 2.8 s for the first sentence (the mock's arithmetic).
    assert doc.sentences[0].duration_s == pytest.approx(2.8)

    payload = result.needs_review_payload
    assert payload["stage"] == "voice" and payload["cost_kind"] == "voice"
    assert payload["voice_url"] == "/api/projects/p-voice/files/05_voice/voice.wav"
    assert payload["sentences"][0]["play_url"] == (
        f"/api/projects/p-voice/files/05_voice/sentences/{ids[0]}.wav"
    )
    assert payload["duration_s"] == doc.duration_s and payload["target_s"] == 60.0
    assert payload["timing_confidence"] == "medium" and payload["source"] == "provider_sentence"
    assert payload["synthesized_sentences"] == 3 and payload["cached_sentences"] == 0
    assert any("estimated inside each sentence" in w for w in payload["warnings"])
    gate_ids = {g["id"]: g for g in payload["gate_results"]}
    assert gate_ids["voice.consent"]["passed"] is True
    assert "mock" in gate_ids["voice.consent"]["detail"]
    assert gate_ids["voice.duration"]["passed"] is False  # 9 s against a 60 s target: a warning
    assert result.cost_usd == 0.0 and result.gate_results == payload["gate_results"]
    assert "3 sentences" in result.summary
    manifest = json.loads((stage_dir / "voice.json").read_text("utf-8"))
    assert manifest["provider"] == "mock" and len(manifest["sentences"]) == 3
    assert all(not row["cached"] for row in manifest["sentences"])
    # The review payload can be rebuilt from the files after a restart.
    rebuilt = load_review_payload(folder, "p-voice")
    assert rebuilt["sentences"][1]["play_url"] == payload["sentences"][1]["play_url"]


def test_second_run_comes_from_the_cache(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    settings = make_settings(tmp_path)
    ctx = make_ctx(folder, settings, make_channel(), build_providers(voice="mock")["voice"])
    run(VoiceStage().run(ctx))
    cache = settings.app_data_dir / "cache" / "voice"
    assert len(list(cache.glob("*.wav"))) == 3 and len(list(cache.glob("*.json"))) == 3
    result = run(VoiceStage().run(ctx))
    assert result.needs_review_payload["cached_sentences"] == 3
    assert "3 from cache" in result.summary


# Caching and cost on a billed voice ----------------------------------------------------------


def test_cache_rebills_only_changed_sentences(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    settings = make_settings(tmp_path)
    provider = FakeVoice(price=0.001)
    consent = {"owner_name": "Dee", "consented_by": "Imran", "consented_at": "2026-10-01T10:00:00Z"}
    ctx = make_ctx(folder, settings, make_channel(clone_ref="voice-7", consent=consent), provider)
    first = run(VoiceStage().run(ctx))
    consent_gate = next(g for g in first.gate_results if g["id"] == "voice.consent")
    assert consent_gate["passed"] and "Consent fields" in consent_gate["detail"]
    expected = round(sum(len(t) * 0.001 for t in TEXTS), 6)
    assert first.cost_usd == pytest.approx(expected) and len(provider.calls) == 3
    assert first.needs_review_payload["cost_usd"] == pytest.approx(expected)
    assert provider.calls[0].voice_id == "voice-7" and provider.calls[0].language == "English"
    rows = first.needs_review_payload["sentences"]
    assert rows[0]["cost_usd"] == pytest.approx(len(TEXTS[0]) * 0.001)

    second = run(VoiceStage().run(ctx))
    assert second.cost_usd == 0.0 and len(provider.calls) == 3
    assert second.needs_review_payload["cached_sentences"] == 3

    # Change the spoken form of one sentence: only that one is billed again.
    ids = sentence_ids(folder)
    changed = "Nobody noticed the quiet stranger in the corner booth."
    make_project(tmp_path, spoken={ids[1]: changed})
    third = run(VoiceStage().run(ctx))
    assert len(provider.calls) == 4 and provider.calls[-1].text == changed
    assert third.cost_usd == pytest.approx(len(changed) * 0.001)
    payload = third.needs_review_payload
    assert payload["cached_sentences"] == 2 and payload["synthesized_sentences"] == 1
    assert payload["sentences"][1]["speech_text"] == changed
    doc = timing_of(folder)
    assert doc.sentences[1].text == changed


def test_provider_words_become_provider_word_timing(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    provider = FakeVoice(words=True)
    ctx = make_ctx(folder, make_settings(tmp_path), make_channel(), provider)
    result = run(VoiceStage().run(ctx))
    doc = timing_of(folder)
    assert doc.source == "provider_word" and doc.timing_confidence == "high"
    second = doc.sentences[1]
    assert second.words_source == "provider"
    assert second.words[0].start_s == pytest.approx(second.start_s)
    assert second.words[0].end_s == pytest.approx(second.start_s + 0.4)
    assert second.words[-1].end_s == pytest.approx(second.end_s)
    assert all(w.confidence == pytest.approx(0.95) for w in second.words)
    assert result.needs_review_payload["source"] == "provider_word"
    assert not any("estimated" in w for w in result.needs_review_payload["warnings"])


def test_words_are_estimated_when_the_tool_returns_none(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    ctx = make_ctx(folder, make_settings(tmp_path), make_channel(), FakeVoice(words=False))
    result = run(VoiceStage().run(ctx))
    doc = timing_of(folder)
    assert doc.source == "provider_sentence"
    for sentence in doc.sentences:
        assert sentence.words_source == "estimated"
        assert [w.text for w in sentence.words] == sentence.text.split()
        assert sentence.words[0].start_s == sentence.start_s
        assert sentence.words[-1].end_s == pytest.approx(sentence.end_s)
        for a, b in zip(sentence.words, sentence.words[1:], strict=False):
            assert a.end_s == pytest.approx(b.start_s)
    warnings = result.needs_review_payload["warnings"]
    assert any("estimated inside each sentence" in w for w in warnings)


def test_provider_failure_books_what_was_spent(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    provider = FakeVoice(price=0.01, fail_on=2)
    ctx = make_ctx(folder, make_settings(tmp_path), make_channel(), provider)
    with pytest.raises(StageError) as excinfo:
        run(VoiceStage().run(ctx))
    assert "sentence 2 of 3" in str(excinfo.value) and "pretend cloud" in str(excinfo.value)
    assert excinfo.value.cost_usd == pytest.approx(len(TEXTS[0]) * 0.01)
    assert excinfo.value.cost_kind == "voice"
    assert not (folder / "05_voice" / "timing.json").exists()


def test_cancel_stops_before_the_first_sentence(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    provider = FakeVoice()
    ctx = make_ctx(folder, make_settings(tmp_path), make_channel(), provider)
    ctx.cancel.set()
    with pytest.raises(StageError, match="stopped"):
        run(VoiceStage().run(ctx))
    assert provider.calls == []


def test_tool_mismatch_and_language_warnings(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    channel = make_channel(tool="fish_audio", language="Spanish")
    ctx = make_ctx(folder, make_settings(tmp_path), channel, FakeVoice())
    warnings = run(VoiceStage().run(ctx)).needs_review_payload["warnings"]
    assert any("fish_audio" in w and "fakecloud" in w for w in warnings)
    assert any("Spanish" in w and "English" in w for w in warnings)


# Gates -----------------------------------------------------------------------------------------


def test_consent_gate_blocks_a_clone_without_a_record(tmp_path: Path) -> None:
    sample = write_wav(tmp_path / "voices" / "daniel.wav", 0.1)
    folder = make_project(tmp_path)
    provider = FakeVoice()
    channel = make_channel(clone_ref="clone-1", sample_path=str(sample))
    ctx = make_ctx(folder, make_settings(tmp_path), channel, provider)
    with pytest.raises(GateBlocked) as excinfo:
        run(VoiceStage().run(ctx))
    assert "consent" in str(excinfo.value).lower()
    assert provider.calls == []  # nothing was sent to the tool, nothing billed
    assert excinfo.value.cost_usd == 0.0 and excinfo.value.cost_kind == "voice"

    # A broken record is reported, not silently ignored.
    (sample.parent / "consent.json").write_text('{"owner_name": "Daniel"}', encoding="utf-8")
    with pytest.raises(GateBlocked, match="could not be used"):
        run(VoiceStage().run(ctx))

    (sample.parent / "consent.json").write_text(
        json.dumps({"owner_name": "Daniel", "consented_by": "Imran",
                    "consented_at": "2026-10-01T10:00:00Z", "statement": "I agree."}),
        encoding="utf-8",
    )
    result = run(VoiceStage().run(ctx))
    gate = next(g for g in result.gate_results if g["id"] == "voice.consent")
    assert gate["passed"] and "Daniel" in gate["detail"]
    manifest = json.loads((folder / "05_voice" / "voice.json").read_text("utf-8"))
    assert manifest["consent"]["owner_name"] == "Daniel"
    assert manifest["consent_ref"].endswith("consent.json")
    assert result.needs_review_payload["consent"]["consented_by"] == "Imran"


def test_consent_status_rules(tmp_path: Path) -> None:
    # Neither a clone nor a sample recording: a stock voice, no consent needed.
    assert consent_status(make_channel(), tmp_path).is_clone is False
    # A clone made in the tool's own website (clone_ref) needs consent as much as a sample.
    assert consent_status(make_channel(clone_ref="voice-clone-12345"), tmp_path).is_clone
    assert consent_status(
        make_channel(clone_ref="voice-clone-12345"), tmp_path, {"require_when": "sample_path"}
    ).is_clone is False
    assert consent_status(make_channel(), tmp_path, {"require_when": "always"}).is_clone
    # The Consent fields of the channel's Voice tab are the record.
    record = {"owner_name": "Dee", "consented_by": "Imran", "consented_at": "2026-10-01T10:00:00Z"}
    filled = consent_status(make_channel(clone_ref="voice-1", consent=record), None)
    assert filled.found and "Consent fields" in filled.ref and filled.record["owner_name"] == "Dee"
    half = consent_status(make_channel(clone_ref="voice-1", consent={"owner_name": "Dee"}), None)
    assert not half.found and "incomplete" in half.error
    sample = write_wav(tmp_path / "s" / "take.wav", 0.1)
    channel = make_channel(sample_path=str(sample))
    status = consent_status(channel, tmp_path / "channels")
    assert status.is_clone and not status.found
    # The sibling "<stem>.consent.json" file counts, as does one in the channel folder.
    record = {"owner_name": "Dee", "consented_by": "Imran", "consented_at": "2026-10-01T10:00:00Z"}
    (tmp_path / "channels" / "kind-ledger" / "voice").mkdir(parents=True)
    (tmp_path / "channels" / "kind-ledger" / "voice" / "consent.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    status = consent_status(channel, tmp_path / "channels")
    assert status.found and "kind-ledger" in status.ref
    (sample.parent / "take.consent.json").write_text(json.dumps(record), encoding="utf-8")
    assert consent_status(channel, tmp_path / "channels").ref.endswith("take.consent.json")
    # require_when can be narrowed in rules.yaml.
    assert consent_status(make_channel(clone_ref="x"), None, {"require_when": "clone_ref"}).is_clone
    assert not consent_status(
        make_channel(sample_path=str(sample)), None, {"require_when": "clone_ref"}
    ).is_clone
    # The mock is exempt even for a clone.
    folder = make_project(tmp_path)
    ctx = make_ctx(folder, make_settings(tmp_path), channel, build_providers(voice="mock")["voice"])
    result = run(VoiceStage().run(ctx))
    assert next(g for g in result.gate_results if g["id"] == "voice.consent")["passed"]


def test_duration_gate_passes_near_the_target(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    channel = make_channel()
    channel.channel.shorts_seconds = 10  # 3 sentences of 9 s on the mock: inside 25%
    ctx = make_ctx(folder, make_settings(tmp_path), channel,
                   build_providers(voice="mock")["voice"], format="shorts")
    result = run(VoiceStage().run(ctx))
    gate = next(g for g in result.gate_results if g["id"] == "voice.duration")
    assert gate["passed"] and "target 10 s" in gate["detail"]
    assert result.needs_review_payload["target_s"] == 10.0


def test_word_flags() -> None:
    doc = TimingDoc(
        duration_s=5.0, source="provider_word",
        sentences=[
            TimedSentence(id="s1", text="a tiny word", start_s=0.0, end_s=5.0, words=[
                TimedWord(text="a", start_s=0.0, end_s=0.01),
                TimedWord(text="tiny", start_s=0.01, end_s=0.5),
                TimedWord(text="word", start_s=0.5, end_s=3.0),
            ]),
        ],
    )
    warnings, flags = flag_words(doc)
    assert len(warnings) == 1 and "2 word(s)" in warnings[0] and "'a' at 0.0 s" in warnings[0]
    assert flags == {"s1": ["'a' is very short (10 ms)", "'word' is very long (2500 ms)"]}
    assert flag_words(TimingDoc(duration_s=0, source="estimated", sentences=[])) == ([], {})


# Own recording ---------------------------------------------------------------------------------


needs_ffmpeg = pytest.mark.skipif(not ff.tools_available(), reason="ffmpeg/ffprobe not on PATH")


@needs_ffmpeg
def test_own_recording_with_the_none_aligner(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    settings = make_settings(tmp_path)
    provider = FakeVoice()
    ctx = make_ctx(folder, settings, make_channel(), provider)
    # A 44.1 kHz stereo take of 6 s uploaded into the stage folder.
    take = write_wav(folder / "05_voice" / "uploads" / "take.wav", 6.0, sample_rate=44100,
                     channels=2)
    ctx.edits = {"audio_path": "05_voice/uploads/take.wav"}
    result = run(VoiceStage().apply_edits(ctx))
    assert result is not None and provider.calls == []  # nothing synthesised, nothing billed
    assert result.cost_usd == 0.0
    voice = folder / "05_voice" / "voice.wav"
    info = read_wav_info(voice)
    assert info.is_standard() and info.duration_s == pytest.approx(6.0, abs=0.01)
    doc = timing_of(folder)
    assert doc.source == "estimated" and doc.timing_confidence == "low"
    assert doc.own_recording and doc.provider == "own_recording" and doc.aligner == "none"
    assert len(doc.sentences) == 3
    assert doc.sentences[0].start_s == pytest.approx(0.12)  # 2% lead-in
    assert doc.sentences[-1].end_s == pytest.approx(6.0 - 0.12)
    for a, b in zip(doc.sentences, doc.sentences[1:], strict=False):
        assert a.end_s == pytest.approx(b.start_s)
    lengths = [s.duration_s for s in doc.sentences]
    assert lengths[2] > lengths[0]  # the longer sentence takes more of the recording
    for sentence in doc.sentences:
        cut = folder / "05_voice" / "sentences" / f"{sentence.id}.wav"
        assert read_wav_info(cut).frames == sentence.end_frame - sentence.start_frame
        assert sentence.words and sentence.words_source == "estimated"
    payload = result.needs_review_payload
    assert payload["own_recording"] is True and payload["timing_confidence"] == "low"
    assert any("estimated from the text" in w for w in payload["warnings"])
    assert take.is_file()  # the upload is kept
    manifest = json.loads((folder / "05_voice" / "voice.json").read_text("utf-8"))
    assert manifest["own_recording"] and manifest["source_file"].endswith("take.wav")
    assert "Own recording used" in result.summary

    # An absolute path anywhere on this PC works too; traversal out of the project does not.
    elsewhere = write_wav(tmp_path / "elsewhere" / "take2.wav", 3.0)
    ctx.edits = {"audio_path": str(elsewhere)}
    run(VoiceStage().apply_edits(ctx))
    assert timing_of(folder).duration_s == pytest.approx(3.0)
    with pytest.raises(StageError, match="not found inside the project folder"):
        resolve_audio_path(folder, "../elsewhere/take2.wav")
    with pytest.raises(StageError, match="not found on this PC"):
        resolve_audio_path(folder, str(tmp_path / "nope.wav"))
    with pytest.raises(StageError, match="Choose a recording"):
        resolve_audio_path(folder, "  ")


def test_dropped_voice_wav_in_manual_mode_is_the_recording(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    provider = FakeVoice()
    ctx = make_ctx(folder, make_settings(tmp_path), make_channel(), provider)
    ctx.project.stage_modes.voice = "manual"
    write_wav(folder / "05_voice" / "voice.wav", 4.0)  # already 48 kHz mono: no FFmpeg needed
    result = run(VoiceStage().run(ctx))
    assert provider.calls == []
    assert timing_of(folder).own_recording and result.needs_review_payload["own_recording"]
    assert timing_of(folder).duration_s == pytest.approx(4.0)


def test_timing_from_recording_helper(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    settings = make_settings(tmp_path)
    channel = make_channel()
    make_ctx(folder, settings, channel, FakeVoice())  # writes job.json
    write_wav(folder / "05_voice" / "voice.wav", 5.0)
    doc = timing_from_recording(folder, channel, settings)
    assert doc.own_recording and len(doc.sentences) == 3
    assert (folder / "05_voice" / "timing.json").is_file()
    assert (folder / "05_voice" / "voice.json").is_file()
    with pytest.raises(StageError, match="no recording"):
        timing_from_recording(tmp_path / "empty", channel, settings)


class FakeAligner:
    name = "fakealign"

    def __init__(self, words: list[Any]) -> None:
        self.words = words
        self.calls: list[tuple[Path, str, str]] = []

    def available(self) -> bool:
        return True

    def align(self, audio_path: Path, text: str, language: str) -> Any:
        from cashcow_studio.audio.aligner import AlignmentResult

        self.calls.append((audio_path, text, language))
        return AlignmentResult(words=self.words, engine=self.name, language="en")


def test_own_recording_with_an_aligner(tmp_path: Path) -> None:
    from cashcow_studio.audio.aligner import AlignedWord

    folder = make_project(tmp_path)
    tokens = " ".join(TEXTS).split()
    words = [AlignedWord(t, 0.2 + i * 0.3, 0.2 + i * 0.3 + 0.25, 0.9) for i, t in enumerate(tokens)]
    aligner = FakeAligner(words)
    ctx = make_ctx(folder, make_settings(tmp_path), make_channel(), FakeVoice())
    write_wav(folder / "05_voice" / "voice.wav", 10.0)
    ctx.edits = {"audio_path": "05_voice/voice.wav"}
    result = run(VoiceStage(aligner=aligner).apply_edits(ctx))
    assert result is not None and aligner.calls and aligner.calls[0][2] == "English"
    doc = timing_of(folder)
    assert doc.source == "aligned" and doc.timing_confidence == "high"
    assert doc.aligner == "fakealign"
    assert doc.sentences[0].start_s == pytest.approx(0.2)
    assert doc.sentences[0].words_source == "aligner"
    assert len(doc.sentences[0].words) == len(TEXTS[0].split())
    assert doc.sentences[1].start_s >= doc.sentences[0].end_s

    # An aligner that finds nothing falls back to estimates with a warning.
    ctx.edits = {"audio_path": "05_voice/voice.wav"}
    result = run(VoiceStage(aligner=FakeAligner([])).apply_edits(ctx))
    assert timing_of(folder).source == "estimated"
    assert any("found no words" in w for w in result.needs_review_payload["warnings"])


# Edits -------------------------------------------------------------------------------------------


def test_re_record_edit_bypasses_the_cache(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    provider = FakeVoice(price=0.001)
    ctx = make_ctx(folder, make_settings(tmp_path), make_channel(), provider)
    run(VoiceStage().run(ctx))
    ids = sentence_ids(folder)
    ctx.edits = {"re_record": [ids[1]]}
    result = run(VoiceStage().apply_edits(ctx))
    assert result is not None and len(provider.calls) == 4
    assert provider.calls[-1].sentence_id == ids[1]
    assert result.cost_usd == pytest.approx(len(TEXTS[1]) * 0.001)
    payload = result.needs_review_payload
    assert payload["cached_sentences"] == 2 and payload["synthesized_sentences"] == 1
    assert "1 sentence(s) recorded again" in result.summary
    # The same ids travel with a redo (ctx.edits on run) too.
    ctx.edits = {"re_record": [ids[0], ids[2]]}
    run(VoiceStage().run(ctx))
    assert len(provider.calls) == 6
    ctx.edits = {"re_record": ["s-99-99-99"]}
    with pytest.raises(StageError, match="not in the script"):
        run(VoiceStage().apply_edits(ctx))
    ctx.edits = {"re_record": "not-a-list"}
    with pytest.raises(StageError, match="not valid"):
        run(VoiceStage().apply_edits(ctx))
    ctx.edits = {}
    assert run(VoiceStage().apply_edits(ctx)) is None
    ctx.edits = {"something_else": 1}
    assert run(VoiceStage().apply_edits(ctx)) is None


def test_timing_edit_rewrites_timing_and_sentence_files(tmp_path: Path) -> None:
    folder = make_project(tmp_path)
    ctx = make_ctx(folder, make_settings(tmp_path), make_channel(),
                   build_providers(voice="mock")["voice"])
    run(VoiceStage().run(ctx))
    doc = timing_of(folder)
    first, second = doc.sentences[0], doc.sentences[1]
    first.end_s = 2.0
    second.start_s = 2.0
    second.words = []  # the stage re-estimates words for a sentence left without them
    doc.sentences[2].end_s = 99.0  # clamped to the audio
    ctx.edits = {"timing": doc.model_dump(mode="json")}
    result = run(VoiceStage().apply_edits(ctx))
    assert result is not None
    saved = timing_of(folder)
    assert saved.sentences[0].end_s == 2.0 and saved.sentences[1].start_s == 2.0
    assert saved.sentences[2].end_s == pytest.approx(saved.duration_s)
    assert saved.sentences[1].words and saved.sentences[1].words_source == "estimated"
    assert saved.sentences[0].words[-1].end_s <= 2.0
    assert read_wav_info(folder / "05_voice" / "sentences" / f"{first.id}.wav").frames == 2 * SR
    assert saved.provider == "mock" and "adjusted by the reviewer" in saved.warnings[0]
    assert "Timing adjusted" in result.summary
    # The edited timing must list the script's sentences.
    doc.sentences.pop()
    ctx.edits = {"timing": doc.model_dump(mode="json")}
    with pytest.raises(StageError, match="same sentences"):
        run(VoiceStage().apply_edits(ctx))


def test_files_url_quotes_every_segment() -> None:
    assert files_url("p 1", "05_voice\\sentences\\s 1.wav") == (
        "/api/projects/p%201/files/05_voice/sentences/s%201.wav"
    )


# Through the engine and the HTTP API ----------------------------------------------------------


@pytest.fixture
def api(app_env: AppEnv, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for name in ("LLM", "RESEARCH", "IMAGE", "VOICE"):
        monkeypatch.setenv(f"CCS_{name}_PROVIDER", "mock")
    with TestClient(create_app()) as client:
        stages = {stage.value for stage in client.app.state.engine.stages}
        assert {"title", "script", "storyboard", "voice"} <= stages, stages
        body = channel_payload("Kind Ledger")
        body["channel"]["long_form_minutes"] = 1
        assert client.post("/api/channels", json=body).status_code == 201
        yield client


def wait_for(
    client: TestClient, project_id: str, predicate: Callable[[dict[str, Any]], bool],
    timeout: float = 90.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/projects/{project_id}").json()
        if predicate(body):
            return body
        failed = {n: s["error"] for n, s in body["stages"].items() if s["status"] == "failed"}
        assert not failed, f"a stage failed: {failed}"
        if time.monotonic() > deadline:
            raise AssertionError({n: s["status"] for n, s in body["stages"].items()})
        time.sleep(0.05)


def test_voice_stage_through_the_engine_and_api(api: TestClient) -> None:
    response = api.post("/api/projects", json={
        "channel_slug": "kind-ledger", "format": "long",
        "source": {"kind": "own_topic", "topic_text": "The waiter who never forgot a face"},
        "stage_mode_overrides": {"title": "auto", "script": "auto", "storyboard": "auto",
                                 "voice": "review"},
    })
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]
    project = wait_for(
        api, project_id,
        lambda b: b["stages"]["voice"]["status"] == "awaiting_review",
    )
    folder = Path(project["folder"])
    assert (folder / "05_voice" / "voice.wav").is_file()
    assert (folder / "05_voice" / "timing.json").is_file()
    assert project["costs"]["voice_usd"] == 0.0
    payload = api.get(f"/api/projects/{project_id}/stage/voice").json()
    assert payload["stage"] == "voice" and payload["sentences"]
    assert payload["duration_s"] > 0 and payload["source"] == "provider_sentence"
    # The play URLs really serve the audio.
    audio = api.get(payload["sentences"][0]["play_url"])
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
    assert audio.content[:4] == b"RIFF"
    whole = api.get(payload["voice_url"], headers={"Range": "bytes=0-3"})
    assert whole.status_code == 206 and whole.content == b"RIFF"
    timing = api.get(payload["timing_url"]).json()
    assert timing["sentences"][0]["id"] == payload["sentences"][0]["id"]
    # Approve with a re-record: the stage re-voices that sentence and the project moves on.
    first_id = payload["sentences"][0]["id"]
    approved = api.post(
        f"/api/projects/{project_id}/stage/voice/approve",
        json={"by": "Imran", "edits": {"re_record": [first_id]}},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["stages"]["voice"]["status"] == "approved"
    payload = api.get(f"/api/projects/{project_id}/stage/voice").json()
    assert payload["synthesized_sentences"] == 1
    history = approved.json()["stages"]["voice"]["history"]
    assert any("Edits applied by Imran: re_record" in line for line in history)


def test_stage_module_exports_the_stage_class() -> None:
    assert voice_stage.VoiceStage.name.value == "voice"

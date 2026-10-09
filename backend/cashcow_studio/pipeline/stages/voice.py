"""Voice stage: the spoken form of the script becomes ``05_voice/voice.wav`` plus
``timing.json``, the clock every later stage reads (docs/M3-M4-CONTRACT.md section 1).

Flow:

1. ``03_script/script.json`` + ``speech.json`` give the sentences (ids and spoken text).
2. The consent gate runs before any money is spent: a cloned voice (the channel names a
   clone in ``voice.clone_ref`` or a sample in ``voice.sample_path``) needs a consent
   record (the Consent fields of the channel's Voice tab or a ``consent.json`` next to the
   sample), else the stage blocks.
3. Every sentence is synthesised on its own through the voice provider and cached under
   ``<app_data_dir>/cache/voice/<sha256>.wav`` (+ ``.json``), so a redo only re-bills the
   sentences whose text or voice settings changed. ``05_voice/sentences/<id>.wav`` gets a copy.
4. The sentence files are stitched into ``voice.wav`` with sample-accurate offsets; those
   offsets are the sentence boundaries in ``timing.json``. Word times come from the provider
   when it returned them, otherwise they are estimated inside the sentence by character
   length.
5. ``voice.json`` records provenance (provider, voice, cost per sentence, consent) and the
   review payload lists every sentence with a ``play_url`` served by the files API.

Own recording: approval edits ``{audio_path}`` (or a file dropped at ``05_voice/voice.wav``
in manual mode) are converted to 48 kHz mono WAV with FFmpeg and aligned by the configured
aligner (``whisperx``) or, with the ``none`` default, spread over the recording by text
length (``source: estimated``, ``timing_confidence: low``). Other edits: ``{re_record:
[ids]}`` re-synthesises only those sentences; ``{timing: TimingDoc}`` saves hand nudges.

Files in ``05_voice``: ``voice.wav``, ``timing.json``, ``voice.json`` (manifest),
``sentences/<id>.wav``, ``uploads/`` (from ``POST /api/projects/{id}/upload/voice``).
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import ValidationError

from ...audio.aligner import (
    AlignedWord,
    Aligner,
    AlignerUnavailable,
    assign_words_to_sentences,
    build_aligner,
)
from ...audio.errors import AudioError
from ...audio.estimate import distribute_sentences, estimate_words, word_tokens
from ...audio.ffmpeg import convert_to_wav
from ...audio.wav import concat_wavs, is_standard_wav, read_wav_info, slice_wav
from ...llm.config import speaking_rate_wpm
from ...models.channel import Channel
from ...models.project import Project, StageName
from ...models.script import SpeechDoc
from ...models.timing import (
    DEFAULT_SAMPLE_RATE,
    TimedSentence,
    TimedWord,
    TimingConfidence,
    TimingDoc,
    TimingSource,
    VoiceApproveEdits,
    VoiceManifest,
    VoiceReviewPayload,
    VoiceReviewSentence,
    VoiceSentenceRecord,
    WordSource,
)
from ...policy import gates
from ...providers.base import ProviderError
from ...providers.voice.base import CloneConsent, SynthRequest, SynthResult
from ...storage.settings_store import atomic_write_text
from .base import GateBlocked, StageContext, StageError, StageResult, run_in_thread
from .script import read_model, read_script_doc

log = logging.getLogger(__name__)

STAGE_DIR_NAME = "05_voice"
VOICE_WAV = "voice.wav"
TIMING_JSON = "timing.json"
MANIFEST_JSON = "voice.json"
SENTENCES_DIR = "sentences"
UPLOADS_DIR = "uploads"
CACHE_SUBDIRS = ("cache", "voice")
CONSENT_FILE = "consent.json"
OWN_RECORDING = "own_recording"
SENTENCE_GAP_S = 0.25
"""Silence between two sentences in the stitched narration (a natural pause)."""
WORD_MIN_S = 0.04
WORD_MAX_S = 2.0
MAX_FLAG_EXAMPLES = 5
COST_KIND = "voice"


@dataclass(frozen=True)
class SentenceText:
    id: str
    text: str
    speech_text: str = ""

    @property
    def spoken(self) -> str:
        return " ".join((self.speech_text or self.text).split())


@dataclass
class ConsentStatus:
    is_clone: bool = False
    found: bool = False
    ref: str = ""
    error: str = ""
    record: dict[str, Any] | None = None

    def context(self, provider_id: str) -> dict[str, Any]:
        return {
            "provider": provider_id,
            "is_clone": self.is_clone,
            "consent_found": self.found,
            "consent_ref": self.ref,
            "consent_error": self.error,
            "owner_name": (self.record or {}).get("owner_name", ""),
        }


@dataclass
class SentenceAudio:
    """One synthesised (or cached) sentence, before stitching."""

    sentence: SentenceText
    result: SynthResult
    path: Path
    cached: bool
    cost_usd: float
    cache_key: str


@dataclass
class Built:
    """What a run or an edit produced; ``finish`` turns it into a StageResult."""

    doc: TimingDoc
    manifest: VoiceManifest
    warnings: list[str] = field(default_factory=list)
    outputs: list[Path] = field(default_factory=list)


# Inputs -----------------------------------------------------------------------------------


def load_sentences(folder: Path) -> list[SentenceText]:
    """Script sentences with their spoken form (speech.json), in script order."""
    script_dir = Path(folder) / "03_script"
    script = read_script_doc(script_dir)
    if script is None:
        raise StageError("The script step has not produced a script yet. Finish it first.")
    speech = read_model(script_dir / "speech.json", SpeechDoc)
    spoken = {s.id: s.speech_text for s in speech.sentences} if speech else {}
    rows = [
        SentenceText(id=s.id, text=s.text, speech_text=spoken.get(s.id, ""))
        for s in script.sentences()
    ]
    rows = [r for r in rows if word_tokens(r.spoken)]
    if not rows:
        raise StageError("The script has no sentences to read out.")
    return rows


def cache_dir(settings: Any) -> Path:
    base = Path(getattr(settings, "app_data_dir", "") or Path.home() / ".cashcow")
    return base.joinpath(*CACHE_SUBDIRS)


def rate_override(settings: Any) -> int | None:
    value = getattr(getattr(settings, "voice", None), "speaking_rate_wpm", None)
    return int(value) if isinstance(value, int | float) and value > 0 else None


def target_seconds(channel: Channel, fmt: str) -> float:
    if fmt == "shorts":
        return float(channel.channel.shorts_seconds)
    return float(channel.channel.long_form_minutes) * 60.0


def voice_language(channel: Channel, project_language: str) -> str:
    return channel.voice.language or project_language


def synth_request(
    sentence: SentenceText, channel: Channel, language: str, wpm: int, output_path: Path
) -> SynthRequest:
    voice = channel.voice
    return SynthRequest(
        text=sentence.spoken,
        output_path=output_path,
        voice_id=voice.clone_ref or "",
        language=language,
        model=voice.model or "",
        speed=voice.speed,
        style=voice.style or "",
        speaking_rate_wpm=max(60, min(400, int(wpm))),
        sample_rate_hz=DEFAULT_SAMPLE_RATE,
        sentence_id=sentence.id,
    )


def files_url(project_id: str, relative: str) -> str:
    """The files-endpoint URL for a path inside the project folder (section 5)."""
    parts = [p for p in relative.replace("\\", "/").split("/") if p]
    return f"/api/projects/{quote(project_id, safe='')}/files/" + "/".join(
        quote(p, safe="") for p in parts
    )


def sentence_audio_path(sentence_id: str) -> str:
    return f"{STAGE_DIR_NAME}/{SENTENCES_DIR}/{sentence_id}.wav"


# Consent ------------------------------------------------------------------------------------


def consent_candidates(channel: Channel, channels_dir: Path | None) -> list[Path]:
    """Where a consent.json may live, in the order they are tried."""
    paths: list[Path] = []
    sample = (channel.voice.sample_path or "").strip()
    if sample:
        sample_path = Path(os.path.expandvars(os.path.expanduser(sample)))
        paths.append(sample_path.with_name(sample_path.stem + ".consent.json"))
        paths.append(sample_path.parent / CONSENT_FILE)
    if channels_dir is not None:
        root = Path(channels_dir) / channel.slug
        paths.append(root / "voice" / CONSENT_FILE)
        paths.append(root / CONSENT_FILE)
    return paths


def consent_status(
    channel: Channel, channels_dir: Path | None, rule_params: dict[str, Any] | None = None
) -> ConsentStatus:
    """Does this voice need consent, and is it recorded?

    A voice counts as a clone when the channel names one: ``voice.clone_ref`` (a clone made
    in the tool's own website) or ``voice.sample_path`` (a sample recorded for it); the
    rule's ``require_when`` can narrow that to ``clone_ref``, ``sample_path`` or widen it to
    ``always``. A stock voice is one with neither. The record is either the ``consent``
    fields of the channel's voice config or a ``consent.json`` next to the sample / in the
    channel folder, validated as :class:`CloneConsent`.
    """
    params = rule_params or {}
    status = ConsentStatus()
    require_when = str(params.get("require_when", "clone_ref_or_sample"))
    voice = channel.voice
    has_clone_ref = bool((voice.clone_ref or "").strip())
    has_sample = bool((voice.sample_path or "").strip())
    if require_when == "clone_ref":
        status.is_clone = has_clone_ref
    elif require_when == "sample_path":
        status.is_clone = has_sample
    elif require_when == "always":
        status.is_clone = True
    else:
        status.is_clone = has_clone_ref or has_sample
    if not status.is_clone:
        return status
    inline = getattr(voice, "consent", None)
    if inline is not None:
        data = inline.model_dump(mode="json") if hasattr(inline, "model_dump") else inline
        if isinstance(data, dict) and data.get("owner_name"):
            try:
                record = CloneConsent.model_validate(data)
            except ValidationError as exc:
                status.error = (
                    f"the channel's consent fields are incomplete ({exc.errors()[0]['msg']})"
                )
            else:
                status.found = True
                status.ref = "the channel's voice settings (Consent fields)"
                status.record = record.model_dump(mode="json")
                return status
    for candidate in consent_candidates(channel, channels_dir):
        if not candidate.is_file():
            continue
        try:
            record = CloneConsent.model_validate_json(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError) as exc:
            detail = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            status.error = f"{candidate} could not be read: {detail}"
            continue
        status.found = True
        status.ref = str(candidate)
        status.record = record.model_dump(mode="json")
        status.error = ""
        return status
    return status


# Synthesis ------------------------------------------------------------------------------------


def synthesize_sentence(
    provider: Any,
    request: SynthRequest,
    cache: Path,
    dest: Path,
    *,
    force: bool = False,
) -> tuple[SynthResult, bool]:
    """One sentence -> ``dest`` (48 kHz mono WAV), through the hash cache.

    Returns ``(result, cached)``. ``force`` skips the cache (a re-record) and refreshes it.
    """
    cache.mkdir(parents=True, exist_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    key = request.cache_key(provider.id)
    cached_wav = cache / f"{key}.wav"
    cached_json = cache / f"{key}.json"
    if not force and cached_wav.is_file() and cached_json.is_file():
        try:
            result = SynthResult.model_validate_json(cached_json.read_text(encoding="utf-8"))
            if is_standard_wav(cached_wav, request.sample_rate_hz):
                shutil.copyfile(cached_wav, dest)
                result.path = dest
                result.cached = True
                result.cost_usd = 0.0
                result.sentence_id = request.sentence_id
                return result, True
        except (OSError, ValueError, ValidationError):
            pass  # a damaged cache entry is simply rebuilt
    # One temp file per call: two projects synthesising the same sentence at the same
    # time must not share it (os.replace would fail on Windows while the other writes).
    tmp = cache / f"{key}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp.wav"
    result = provider.synthesize(request.model_copy(update={"output_path": tmp}))
    produced = Path(result.path) if result.path else tmp
    if not produced.is_file():
        raise ProviderError("The voice tool reported success but wrote no audio file.")
    if not is_standard_wav(produced, request.sample_rate_hz):
        fixed = tmp.with_name(tmp.name.replace(".tmp.wav", ".fixed.wav"))
        try:
            convert_to_wav(produced, fixed, sample_rate=request.sample_rate_hz)
        except AudioError as exc:
            raise ProviderError(str(exc)) from exc
        _unlink(produced)
        produced = fixed
    info = read_wav_info(produced)
    try:
        os.replace(produced, cached_wav)
    except OSError:
        # Another project finished the same sentence a moment ago: its copy is as good.
        if not is_standard_wav(cached_wav, request.sample_rate_hz):
            raise
        _unlink(produced)
    result.path = cached_wav
    result.duration_s = info.duration_s
    result.sample_rate_hz = info.sample_rate
    result.channels = info.channels
    result.cached = False
    atomic_write_text(cached_json, result.model_dump_json(indent=2) + "\n")
    shutil.copyfile(cached_wav, dest)
    result.path = dest
    return result, False


def forget_cached(provider_id: str, request: SynthRequest, cache: Path) -> None:
    key = request.cache_key(provider_id)
    for suffix in (".wav", ".json"):
        _unlink(cache / f"{key}{suffix}")


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def sentence_cost(provider: Any, result: SynthResult, cached: bool) -> float:
    if cached:
        return 0.0
    if result.cost_usd:
        return round(float(result.cost_usd), 6)
    try:
        return round(float(provider.estimate_cost(result.text).cost_usd), 6)
    except Exception:  # noqa: BLE001 - a missing price must not fail the stage
        return 0.0


# Timing ------------------------------------------------------------------------------------


def shift_words(
    words: list[Any], start_s: float, end_s: float, text: str
) -> tuple[list[TimedWord], WordSource]:
    """Provider words (relative to the sentence) placed at ``start_s`` and clamped to the
    sentence; without provider words the text is spread by character length."""
    if not words:
        return estimate_words(text, start_s, end_s), "estimated"
    out: list[TimedWord] = []
    for word in words:
        s = min(end_s, max(start_s, start_s + float(word.start_s)))
        e = min(end_s, max(s, start_s + float(word.end_s)))
        out.append(
            TimedWord(
                text=str(word.text), start_s=round(s, 6), end_s=round(e, 6),
                confidence=float(getattr(word, "confidence", 1.0)),
            )
        )
    sources = {str(getattr(w, "source", "provider")) for w in words}
    if sources <= {"provider", "aligner"}:
        return out, "provider"
    return out, "estimated"


def doc_source(sentences: list[TimedSentence], own_recording: bool, aligned: bool) -> TimingSource:
    if own_recording:
        return "aligned" if aligned else "estimated"
    if sentences and all(s.words_source == "provider" for s in sentences):
        return "provider_word"
    return "provider_sentence"


def confidence_for(source: str) -> TimingConfidence:
    if source in ("provider_word", "aligned"):
        return "high"
    return "medium" if source == "provider_sentence" else "low"


def flag_words(doc: TimingDoc) -> tuple[list[str], dict[str, list[str]]]:
    """Words shorter than 40 ms or longer than 2 s: a warning line and per-sentence flags."""
    per_sentence: dict[str, list[str]] = {}
    examples: list[str] = []
    count = 0
    for sentence in doc.sentences:
        for word in sentence.words:
            length = word.duration_s
            if WORD_MIN_S <= length <= WORD_MAX_S:
                continue
            count += 1
            kind = "very short" if length < WORD_MIN_S else "very long"
            per_sentence.setdefault(sentence.id, []).append(
                f"'{word.text}' is {kind} ({length * 1000:.0f} ms)"
            )
            if len(examples) < MAX_FLAG_EXAMPLES:
                examples.append(f"'{word.text}' at {word.start_s:.1f} s ({length * 1000:.0f} ms)")
    if not count:
        return [], per_sentence
    more = f" and {count - len(examples)} more" if count > len(examples) else ""
    return [
        f"{count} word(s) are shorter than {WORD_MIN_S * 1000:.0f} ms or longer than "
        f"{WORD_MAX_S:g} s; check the timing: " + ", ".join(examples) + more + "."
    ], per_sentence


def write_timing(stage_dir: Path, doc: TimingDoc) -> Path:
    path = stage_dir / TIMING_JSON
    atomic_write_text(path, doc.model_dump_json(indent=2) + "\n")
    return path


def read_timing(stage_dir: Path) -> TimingDoc | None:
    return read_model(stage_dir / TIMING_JSON, TimingDoc)


def write_manifest(stage_dir: Path, manifest: VoiceManifest) -> Path:
    path = stage_dir / MANIFEST_JSON
    atomic_write_text(path, manifest.model_dump_json(indent=2) + "\n")
    return path


def read_manifest(stage_dir: Path) -> VoiceManifest | None:
    return read_model(stage_dir / MANIFEST_JSON, VoiceManifest)


def cut_sentences(stage_dir: Path, doc: TimingDoc) -> Path:
    """``sentences/<id>.wav`` cut from ``voice.wav`` at the sentence boundaries."""
    voice = stage_dir / VOICE_WAV
    info = read_wav_info(voice)
    folder = stage_dir / SENTENCES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for sentence in doc.sentences:
        start = sentence.start_frame
        end = sentence.end_frame
        if start is None or end is None:
            start = round(sentence.start_s * info.sample_rate)
            end = round(sentence.end_s * info.sample_rate)
            sentence.start_frame, sentence.end_frame = start, end
        slice_wav(voice, folder / f"{sentence.id}.wav", start, end)
        sentence.audio_path = sentence_audio_path(sentence.id)
    return folder


def clear_sentence_files(stage_dir: Path) -> None:
    folder = stage_dir / SENTENCES_DIR
    if folder.is_dir():
        for item in folder.glob("*.wav"):
            _unlink(item)


# Review payload and gates -------------------------------------------------------------------


def gate_context(
    consent: ConsentStatus, provider_id: str, duration_s: float, target_s: float
) -> dict[str, Any]:
    return {**consent.context(provider_id), "duration_s": duration_s, "target_s": target_s}


def review_payload(
    project_id: str,
    doc: TimingDoc,
    manifest: VoiceManifest,
    sentences: list[SentenceText],
    *,
    gate_results: list[dict[str, Any]],
    warnings: list[str],
    target_s: float | None,
) -> dict[str, Any]:
    _, flags = flag_words(doc)
    by_id = {s.id: s for s in sentences}
    records = {r.id: r for r in manifest.sentences}
    rows: list[VoiceReviewSentence] = []
    for timed in doc.sentences:
        source = by_id.get(timed.id)
        record = records.get(timed.id)
        rows.append(
            VoiceReviewSentence(
                id=timed.id,
                text=source.text if source else timed.text,
                speech_text=source.spoken if source else timed.text,
                start_s=timed.start_s,
                end_s=timed.end_s,
                duration_s=timed.duration_s,
                play_url=files_url(project_id, timed.audio_path or sentence_audio_path(timed.id)),
                words=len(timed.words),
                words_source=timed.words_source,
                cached=record.cached if record else False,
                cost_usd=record.cost_usd if record else 0.0,
                flags=flags.get(timed.id, []),
            )
        )
    payload = VoiceReviewPayload(
        provider=manifest.provider,
        model=manifest.model,
        voice_id=manifest.voice_id,
        language=manifest.language,
        duration_s=doc.duration_s,
        target_s=target_s,
        sample_rate=doc.sample_rate,
        source=doc.source,
        timing_confidence=doc.timing_confidence,
        own_recording=doc.own_recording,
        aligner=doc.aligner,
        voice_url=files_url(project_id, f"{STAGE_DIR_NAME}/{VOICE_WAV}"),
        timing_url=files_url(project_id, f"{STAGE_DIR_NAME}/{TIMING_JSON}"),
        sentences=rows,
        warnings=list(warnings),
        gate_results=gate_results,
        cost_usd=manifest.cost_usd,
        cached_sentences=sum(1 for r in manifest.sentences if r.cached),
        synthesized_sentences=sum(1 for r in manifest.sentences if not r.cached),
        consent=manifest.consent,
    )
    return payload.model_dump(mode="json")


def _provider(ctx: StageContext) -> Any:
    provider = ctx.providers.get("voice")
    if provider is None:
        raise StageError("No voice tool is set up. Check Settings > Models and providers.")
    return provider


def resolve_audio_path(folder: Path, value: str) -> Path:
    """An own recording: an absolute file on this PC, or a path inside the project folder."""
    text = (value or "").strip()
    if not text:
        raise StageError("Choose a recording file first.")
    candidate = Path(os.path.expandvars(os.path.expanduser(text)))
    if candidate.is_absolute():
        if not candidate.is_file():
            raise StageError(f"The recording {candidate} was not found on this PC.")
        return candidate
    root = Path(folder).resolve()
    inside = (root / text).resolve()
    if root not in inside.parents or not inside.is_file():
        raise StageError(
            f"The recording {text} was not found inside the project folder. Upload it first."
        )
    return inside


# The stage ---------------------------------------------------------------------------------


class VoiceStage:
    name = StageName.voice

    def __init__(self, aligner: Aligner | None = None, gap_s: float = SENTENCE_GAP_S) -> None:
        self.aligner = aligner
        self.gap_s = gap_s

    # Entry points ---------------------------------------------------------------------

    async def run(self, ctx: StageContext) -> StageResult:
        stage_dir = ctx.stage_dir(StageName.voice)
        sentences = load_sentences(ctx.folder)
        edits = _edits(ctx)
        if edits.audio_path:
            source = resolve_audio_path(ctx.folder, edits.audio_path)
            built = await self._own_recording(ctx, source, sentences)
            return finish(ctx, built, sentences, "Own recording used")
        dropped = stage_dir / VOICE_WAV
        if ctx.project.stage_modes.for_stage(StageName.voice) == "manual" and dropped.is_file():
            built = await self._own_recording(ctx, dropped, sentences)
            return finish(ctx, built, sentences, "Own recording used")
        built = await self._synthesize(ctx, sentences, force_ids=set(edits.re_record or []))
        return finish(ctx, built, sentences, "Narration made")

    async def finish_manual(self, ctx: StageContext) -> StageResult | None:
        """Manual mode: a person dropped ``voice.wav`` into ``05_voice`` and pressed Run.

        Called by the engine before the stage is marked done. When ``timing.json`` is not
        there yet, the recording is converted and cut into sentences exactly like an own
        recording sent with the ``audio_path`` edit, so the later steps always find their
        clock. Returns ``None`` when the timing file already exists (nothing to do).
        """
        stage_dir = ctx.stage_dir(StageName.voice)
        dropped = stage_dir / VOICE_WAV
        if not dropped.is_file():
            raise StageError(f"There is no recording at {STAGE_DIR_NAME}/{VOICE_WAV}.")
        if (stage_dir / TIMING_JSON).is_file():
            return None
        sentences = load_sentences(ctx.folder)
        built = await self._own_recording(ctx, dropped, sentences)
        return finish(ctx, built, sentences, "Own recording used")

    async def apply_edits(self, ctx: StageContext) -> StageResult | None:
        """Approval edits: ``re_record`` (those sentences again), ``audio_path`` (an own
        recording), ``timing`` (hand nudges). Files are rewritten; the payload is replaced."""
        if not ctx.edits:
            return None
        edits = _edits(ctx)
        if not (edits.re_record or edits.audio_path or edits.timing is not None):
            return None
        sentences = load_sentences(ctx.folder)
        if edits.audio_path:
            source = resolve_audio_path(ctx.folder, edits.audio_path)
            built = await self._own_recording(ctx, source, sentences)
            return finish(ctx, built, sentences, "Own recording used")
        if edits.re_record:
            known = {s.id for s in sentences}
            unknown = sorted(set(edits.re_record) - known)
            if unknown:
                raise StageError(
                    "These sentence ids are not in the script: " + ", ".join(unknown[:5])
                )
            built = await self._synthesize(ctx, sentences, force_ids=set(edits.re_record))
            return finish(
                ctx, built, sentences, f"{len(edits.re_record)} sentence(s) recorded again"
            )
        assert edits.timing is not None
        built = await run_in_thread(ctx, partial(apply_timing, ctx, edits.timing, sentences))
        return finish(ctx, built, sentences, "Timing adjusted by the reviewer")

    # Synthesis ------------------------------------------------------------------------

    async def _synthesize(
        self, ctx: StageContext, sentences: list[SentenceText], *, force_ids: set[str]
    ) -> Built:
        provider = _provider(ctx)
        project, channel = ctx.project, ctx.channel
        stage_dir = ctx.stage_dir(StageName.voice)
        warnings: list[str] = []
        consent = consent_status(
            channel, ctx.settings.channels_dir, gates.params("voice.consent")
        )
        pre = gates.evaluate("voice", gate_context(consent, provider.id, 0.0, 0.0))
        blocking = [
            r for r in pre if r.id == "voice.consent" and r.severity == "block" and not r.passed
        ]
        if blocking:
            raise GateBlocked(gates.blocking_reasons(blocking), cost_kind=COST_KIND)
        tool = channel.voice.tool
        if provider.id != "mock" and tool not in ("other", provider.id):
            warnings.append(
                f"The channel is set up for the {tool} voice tool, but the app uses "
                f"{provider.id}. Change the voice provider in Settings if that is not intended."
            )
        language = voice_language(channel, project.language)
        if language != project.language:
            warnings.append(
                f"The voice is set to {language} while the video is in {project.language}."
            )
        wpm = speaking_rate_wpm(language, rate_override(ctx.settings))
        cache = cache_dir(ctx.settings)
        sentences_dir = stage_dir / SENTENCES_DIR
        clear_sentence_files(stage_dir)
        pieces: list[SentenceAudio] = []
        total = len(sentences)
        spent = 0.0
        await ctx.report(f"Voicing {total} sentences with {provider.id}", 2)
        for index, sentence in enumerate(sentences):
            if ctx.cancelled:
                raise StageError(
                    "The voice step was stopped.", cost_usd=spent, cost_kind=COST_KIND
                )
            dest = sentences_dir / f"{sentence.id}.wav"
            request = synth_request(sentence, channel, language, wpm, dest)
            force = sentence.id in force_ids
            try:
                result, cached = await run_in_thread(
                    ctx,
                    partial(synthesize_sentence, provider, request, cache, dest, force=force),
                )
            except ProviderError as exc:
                raise StageError(
                    f"The voice tool could not read sentence {index + 1} of {total}: {exc}",
                    cost_usd=spent, cost_kind=COST_KIND,
                ) from exc
            except AudioError as exc:
                raise StageError(str(exc), cost_usd=spent, cost_kind=COST_KIND) from exc
            cost = sentence_cost(provider, result, cached)
            spent = round(spent + cost, 6)
            pieces.append(
                SentenceAudio(sentence, result, dest, cached, cost, request.cache_key(provider.id))
            )
            pct = 2 + 80 * (index + 1) / total
            await ctx.report(
                f"Sentence {index + 1} of {total} {'(from cache)' if cached else 'voiced'}", pct
            )
        await ctx.report("Stitching the narration", 85)
        try:
            segments = await run_in_thread(
                ctx,
                partial(
                    concat_wavs, [p.path for p in pieces], stage_dir / VOICE_WAV,
                    sample_rate=DEFAULT_SAMPLE_RATE, gap_s=self.gap_s,
                ),
            )
        except AudioError as exc:
            raise StageError(str(exc), cost_usd=spent, cost_kind=COST_KIND) from exc
        timed: list[TimedSentence] = []
        for piece, segment in zip(pieces, segments, strict=True):
            start_s = segment.start_s(DEFAULT_SAMPLE_RATE)
            end_s = segment.end_s(DEFAULT_SAMPLE_RATE)
            words, source = shift_words(piece.result.words, start_s, end_s, piece.sentence.spoken)
            timed.append(
                TimedSentence(
                    id=piece.sentence.id, text=piece.sentence.spoken, start_s=start_s,
                    end_s=end_s, words=words, words_source=source,
                    start_frame=segment.start_frame, end_frame=segment.end_frame,
                    audio_path=sentence_audio_path(piece.sentence.id),
                )
            )
        info = read_wav_info(stage_dir / VOICE_WAV)
        source_value = doc_source(timed, own_recording=False, aligned=False)
        now = datetime.now(UTC)
        model = next((p.result.model for p in pieces if p.result.model), channel.voice.model)
        doc = TimingDoc(
            sample_rate=info.sample_rate, duration_s=info.duration_s, source=source_value,
            sentences=timed, timing_confidence=confidence_for(source_value),
            provider=provider.id, model=model, voice_id=channel.voice.clone_ref or "",
            language=language, aligner="none", own_recording=False, generated_at=now,
        )
        if source_value == "provider_sentence":
            warnings.append(
                "Word times are estimated inside each sentence (the voice tool returned no "
                "word timestamps); sentence boundaries are exact."
            )
        manifest = VoiceManifest(
            project_id=project.id, provider=provider.id, model=model,
            voice_id=channel.voice.clone_ref or "", language=language,
            speed=channel.voice.speed, style=channel.voice.style or "",
            sample_rate=info.sample_rate, duration_s=info.duration_s, cost_usd=spent,
            sentences=[
                VoiceSentenceRecord(
                    id=p.sentence.id, text=p.sentence.spoken, cache_key=p.cache_key,
                    cached=p.cached, duration_s=p.result.duration_s,
                    characters=p.result.characters or len(p.sentence.spoken),
                    cost_usd=p.cost_usd, words_source=t.words_source,
                )
                for p, t in zip(pieces, timed, strict=True)
            ],
            consent=consent.record, consent_ref=consent.ref, generated_at=now,
        )
        return Built(doc=doc, manifest=manifest, warnings=warnings,
                     outputs=[stage_dir / VOICE_WAV, sentences_dir])

    # Own recording ----------------------------------------------------------------------

    async def _own_recording(
        self, ctx: StageContext, source: Path, sentences: list[SentenceText]
    ) -> Built:
        aligner = self.aligner or build_aligner(settings=ctx.settings)
        await ctx.report(f"Converting {source.name} to 48 kHz mono WAV", 10)
        if aligner.name != "none":
            await ctx.report(f"Aligning the recording with {aligner.name}", 30)
        built = await run_in_thread(
            ctx,
            partial(
                build_own_recording, ctx.stage_dir(StageName.voice), ctx.project, ctx.channel,
                source, sentences, aligner, cancel=ctx.cancel,
            ),
        )
        await ctx.report("Sentences cut from the recording", 90)
        return built


# Own recording ---------------------------------------------------------------------------


def build_own_recording(
    stage_dir: Path,
    project: Project,
    channel: Channel,
    source: Path,
    sentences: list[SentenceText],
    aligner: Aligner,
    cancel: threading.Event | None = None,
) -> Built:
    """Convert, align (or estimate) and cut an own recording. Synchronous; the stage runs
    it in a worker thread (``cancel`` stops the FFmpeg conversion when the project is
    archived) and later stages may call it directly through :func:`timing_from_recording`."""
    warnings: list[str] = []
    voice = stage_dir / VOICE_WAV
    try:
        _install_recording(source, voice, cancel)
    except AudioError as exc:
        raise StageError(str(exc)) from exc
    if cancel is not None and cancel.is_set():
        raise StageError("Stopped: the project was archived while the recording was prepared.")
    info = read_wav_info(voice)
    if info.frames == 0:
        raise StageError(f"The recording {source.name} is empty.")
    language = voice_language(channel, project.language)
    timed: list[TimedSentence] = []
    aligned = False
    if aligner.name != "none":
        full_text = " ".join(s.spoken for s in sentences)
        try:
            alignment = aligner.align(voice, full_text, language)
        except AlignerUnavailable as exc:
            warnings.append(f"{exc} The timing is estimated from the text instead.")
        except AudioError as exc:
            raise StageError(str(exc)) from exc
        else:
            timed = sentences_from_alignment(sentences, alignment.words, info.duration_s)
            aligned = bool(timed)
            if not aligned:
                warnings.append(
                    f"{aligner.name} found no words in the recording; the timing is "
                    "estimated from the text instead."
                )
    if not aligned:
        timed = sentences_by_share(sentences, info.duration_s)
        warnings.append(
            "The timing of your recording is estimated from the text (no aligner). Nudge "
            "the sentence times if the captions drift, or set up WhisperX for exact words."
        )
    for sentence in timed:
        sentence.start_frame = round(sentence.start_s * info.sample_rate)
        sentence.end_frame = round(sentence.end_s * info.sample_rate)
    clear_sentence_files(stage_dir)
    source_value = doc_source(timed, own_recording=True, aligned=aligned)
    now = datetime.now(UTC)
    doc = TimingDoc(
        sample_rate=info.sample_rate, duration_s=info.duration_s, source=source_value,
        sentences=timed, timing_confidence=confidence_for(source_value),
        provider=OWN_RECORDING, model="", voice_id="", language=language,
        aligner=aligner.name if aligned else "none", own_recording=True, generated_at=now,
    )
    sentences_dir = cut_sentences(stage_dir, doc)
    manifest = VoiceManifest(
        project_id=project.id, provider=OWN_RECORDING, language=language,
        own_recording=True, source_file=str(source), aligner=doc.aligner,
        sample_rate=info.sample_rate, duration_s=info.duration_s, cost_usd=0.0,
        sentences=[
            VoiceSentenceRecord(
                id=t.id, text=t.text, duration_s=t.duration_s, characters=len(t.text),
                words_source=t.words_source,
            )
            for t in timed
        ],
        generated_at=now,
    )
    return Built(doc=doc, manifest=manifest, warnings=warnings, outputs=[voice, sentences_dir])


def _install_recording(
    source: Path, voice: Path, cancel: threading.Event | None = None
) -> None:
    """Put the recording at ``voice.wav`` as 48 kHz mono 16-bit (converting when needed)."""
    source, voice = Path(source), Path(voice)
    if source.resolve() == voice.resolve():
        if is_standard_wav(voice, DEFAULT_SAMPLE_RATE):
            return
        original = voice.with_name("voice.original" + (source.suffix.lower() or ".wav"))
        shutil.copyfile(voice, original)
        convert_to_wav(original, voice, sample_rate=DEFAULT_SAMPLE_RATE, cancel=cancel)
        return
    if is_standard_wav(source, DEFAULT_SAMPLE_RATE):
        shutil.copyfile(source, voice)
        return
    convert_to_wav(source, voice, sample_rate=DEFAULT_SAMPLE_RATE, cancel=cancel)


def sentences_by_share(sentences: list[SentenceText], duration_s: float) -> list[TimedSentence]:
    """Sentences spread over the recording by text length; words estimated inside."""
    lead = min(0.3, duration_s * 0.02)
    spans = distribute_sentences(
        [s.spoken for s in sentences], duration_s, lead_s=lead, tail_s=lead
    )
    rows: list[TimedSentence] = []
    for sentence, (start_s, end_s) in zip(sentences, spans, strict=True):
        rows.append(
            TimedSentence(
                id=sentence.id, text=sentence.spoken, start_s=start_s, end_s=end_s,
                words=estimate_words(sentence.spoken, start_s, end_s), words_source="estimated",
                audio_path=sentence_audio_path(sentence.id),
            )
        )
    return rows


def sentences_from_alignment(
    sentences: list[SentenceText], words: list[AlignedWord], duration_s: float
) -> list[TimedSentence]:
    """Aligned words handed out to the sentences; sentences the aligner missed are placed
    in the gap their neighbours leave and estimated inside it."""
    if not words:
        return []
    chunks = assign_words_to_sentences([(s.id, s.spoken) for s in sentences], words)
    rows: list[TimedSentence | None] = [None] * len(sentences)
    for index, (sentence, chunk) in enumerate(zip(sentences, chunks, strict=True)):
        if not chunk:
            continue
        start_s = round(min(w.start_s for w in chunk), 6)
        end_s = round(min(duration_s, max(w.end_s for w in chunk)), 6)
        timed_words = [
            TimedWord(
                text=w.text,
                start_s=round(min(max(w.start_s, start_s), end_s), 6),
                end_s=round(min(max(w.end_s, w.start_s), end_s), 6),
                confidence=min(1.0, max(0.0, w.confidence)),
            )
            for w in chunk
        ]
        rows[index] = TimedSentence(
            id=sentence.id, text=sentence.spoken, start_s=start_s, end_s=end_s,
            words=timed_words, words_source="aligner",
            audio_path=sentence_audio_path(sentence.id),
        )
    for index, sentence in enumerate(sentences):
        if rows[index] is not None:
            continue
        before = next((rows[j] for j in range(index - 1, -1, -1) if rows[j] is not None), None)
        after = next((rows[j] for j in range(index + 1, len(rows)) if rows[j] is not None), None)
        start_s = before.end_s if before else 0.0
        end_s = max(after.start_s if after else duration_s, start_s)
        rows[index] = TimedSentence(
            id=sentence.id, text=sentence.spoken, start_s=round(start_s, 6),
            end_s=round(end_s, 6), words=estimate_words(sentence.spoken, start_s, end_s),
            words_source="estimated", audio_path=sentence_audio_path(sentence.id),
        )
    out = [r for r in rows if r is not None]
    clock = 0.0
    for row in out:  # keep the sentences in order and non-overlapping
        row.start_s = round(max(row.start_s, clock), 6)
        row.end_s = round(max(row.end_s, row.start_s), 6)
        clock = row.end_s
    return out


# Timing edits ------------------------------------------------------------------------------


def apply_timing(ctx: StageContext, timing: TimingDoc, sentences: list[SentenceText]) -> Built:
    """``{timing}`` edits: the reviewer's sentence (and word) times, clamped to the audio."""
    stage_dir = ctx.stage_dir(StageName.voice)
    voice = stage_dir / VOICE_WAV
    if not voice.is_file():
        raise StageError("There is no narration yet to adjust. Run the voice step first.")
    info = read_wav_info(voice)
    wanted = [s.id for s in sentences]
    given = [s.id for s in timing.sentences]
    if sorted(given) != sorted(wanted):
        raise StageError(
            "The edited timing does not list the same sentences as the script "
            f"({len(given)} given, {len(wanted)} expected)."
        )
    order = {sid: i for i, sid in enumerate(wanted)}
    rows = sorted(timing.sentences, key=lambda s: order[s.id])
    clock = 0.0
    for row in rows:
        row.start_s = round(min(max(row.start_s, clock), info.duration_s), 6)
        row.end_s = round(min(max(row.end_s, row.start_s), info.duration_s), 6)
        clock = row.end_s
        row.start_frame = round(row.start_s * info.sample_rate)
        row.end_frame = round(row.end_s * info.sample_rate)
        if not row.words:
            row.words = estimate_words(row.text, row.start_s, row.end_s)
            row.words_source = "estimated"
        else:
            for word in row.words:
                word.start_s = round(min(max(word.start_s, row.start_s), row.end_s), 6)
                word.end_s = round(min(max(word.end_s, word.start_s), row.end_s), 6)
    previous = read_timing(stage_dir) or timing
    manifest = read_manifest(stage_dir)
    now = datetime.now(UTC)
    doc = TimingDoc(
        sample_rate=info.sample_rate, duration_s=info.duration_s, source=timing.source,
        sentences=rows, timing_confidence=timing.timing_confidence,
        provider=previous.provider, model=previous.model, voice_id=previous.voice_id,
        language=previous.language, aligner=previous.aligner,
        own_recording=previous.own_recording, generated_at=now,
        warnings=["Sentence times were adjusted by the reviewer."],
    )
    clear_sentence_files(stage_dir)
    sentences_dir = cut_sentences(stage_dir, doc)
    if manifest is None:
        manifest = VoiceManifest(
            project_id=ctx.project.id, provider=doc.provider, model=doc.model,
            voice_id=doc.voice_id, language=doc.language, own_recording=doc.own_recording,
            aligner=doc.aligner, sample_rate=info.sample_rate, duration_s=info.duration_s,
            generated_at=now,
        )
    manifest.duration_s = info.duration_s
    manifest.generated_at = now
    return Built(doc=doc, manifest=manifest, warnings=list(doc.warnings),
                 outputs=[voice, sentences_dir])


# Finishing ---------------------------------------------------------------------------------


def finish(
    ctx: StageContext, built: Built, sentences: list[SentenceText], headline: str
) -> StageResult:
    """Gates, ``timing.json``, ``voice.json`` and the review payload for whatever was built."""
    stage_dir = ctx.stage_dir(StageName.voice)
    project, channel = ctx.project, ctx.channel
    doc, manifest = built.doc, built.manifest
    warnings = list(built.warnings)
    word_warnings, _ = flag_words(doc)
    warnings.extend(word_warnings)
    target = target_seconds(channel, project.format)
    if manifest.own_recording:
        context = gate_context(ConsentStatus(), OWN_RECORDING, doc.duration_s, target)
    else:
        consent = consent_status(
            channel, ctx.settings.channels_dir, gates.params("voice.consent")
        )
        context = gate_context(consent, manifest.provider, doc.duration_s, target)
    results = gates.evaluate("voice", context)
    gate_results = gates.to_dicts(results)
    doc.warnings = warnings
    manifest.warnings = warnings
    manifest.gate_results = gate_results
    outputs = [*built.outputs, write_timing(stage_dir, doc), write_manifest(stage_dir, manifest)]
    payload = review_payload(
        project.id, doc, manifest, sentences, gate_results=gate_results,
        warnings=warnings, target_s=target,
    )
    blocking = gates.blocking_reasons(results)
    if blocking:
        raise GateBlocked(blocking, cost_usd=manifest.cost_usd, cost_kind=COST_KIND)
    summary = (
        f"{headline}: {doc.duration_s:.1f} s, {len(doc.sentences)} sentences, "
        f"timing {doc.source} ({doc.timing_confidence} confidence)"
    )
    if manifest.own_recording:
        summary += "."
    else:
        cached = sum(1 for r in manifest.sentences if r.cached)
        summary += f", {cached} from cache, ${manifest.cost_usd:.4f}."
    return StageResult(
        outputs=outputs,
        summary=summary,
        cost_usd=manifest.cost_usd,
        needs_review_payload=payload,
        gate_results=gate_results,
    )


def _edits(ctx: StageContext) -> VoiceApproveEdits:
    try:
        return VoiceApproveEdits.model_validate(ctx.edits or {})
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise StageError(f"The voice edits are not valid ({where}): {first['msg']}") from exc


def load_review_payload(folder: Path, project_id: str) -> dict[str, Any]:
    """The review payload rebuilt from the files (for a server restart)."""
    stage_dir = Path(folder) / STAGE_DIR_NAME
    doc = read_timing(stage_dir)
    manifest = read_manifest(stage_dir)
    if doc is None or manifest is None:
        return {}
    try:
        sentences = load_sentences(Path(folder))
    except StageError:
        sentences = [SentenceText(id=s.id, text=s.text) for s in doc.sentences]
    return review_payload(
        project_id, doc, manifest, sentences, gate_results=manifest.gate_results,
        warnings=manifest.warnings, target_s=None,
    )


def timing_from_recording(
    folder: Path,
    channel: Channel,
    settings: Any,
    *,
    source: Path | None = None,
    aligner: Aligner | None = None,
) -> TimingDoc:
    """Build ``timing.json`` (and the sentence files) for a recording a person supplied by
    hand, without the engine: the manual-mode path where ``voice.wav`` was dropped into
    ``05_voice`` and the project was continued with Run. A later stage that finds
    ``voice.wav`` but no ``timing.json`` can call this (synchronous; use a worker thread
    from async code). Writes ``timing.json`` and ``voice.json`` and returns the document."""
    folder = Path(folder)
    stage_dir = folder / STAGE_DIR_NAME
    recording = Path(source) if source else stage_dir / VOICE_WAV
    if not recording.is_file():
        raise StageError(f"There is no recording at {recording}.")
    project = read_model(folder / "job.json", Project)
    if project is None:
        raise StageError(f"{folder / 'job.json'} could not be read.")
    sentences = load_sentences(folder)
    ctx = StageContext(project=project, channel=channel, settings=settings, folder=folder)
    chosen = aligner or build_aligner(settings=settings)
    built = build_own_recording(stage_dir, project, channel, recording, sentences, chosen)
    finish(ctx, built, sentences, "Own recording used")
    return built.doc

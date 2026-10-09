"""Sample-accurate WAV work with the standard library: inspect, stitch and cut PCM files.

The voice stage stitches one WAV per sentence into ``voice.wav``. Doing that here, frame by
frame, means the sentence offsets are exact by construction (the offset of sentence *n* is
the number of frames written before it), which is what ``timing.json`` promises. Anything
that is not already 48 kHz mono 16-bit PCM goes through :func:`audio.ffmpeg.convert_to_wav`
first.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

from .errors import AudioError

DEFAULT_SAMPLE_RATE = 48000
SAMPLE_WIDTH = 2  # 16-bit PCM
CHUNK_FRAMES = 48000  # one second at 48 kHz per read/write


@dataclass(frozen=True)
class WavInfo:
    sample_rate: int
    channels: int
    sample_width: int
    frames: int

    @property
    def duration_s(self) -> float:
        return round(self.frames / self.sample_rate, 6) if self.sample_rate else 0.0

    def is_standard(self, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bool:
        return (
            self.sample_rate == sample_rate
            and self.channels == 1
            and self.sample_width == SAMPLE_WIDTH
        )


def read_wav_info(path: Path | str) -> WavInfo:
    """Header facts of a PCM WAV; :class:`AudioError` for anything else."""
    path = Path(path)
    if not path.is_file():
        raise AudioError(f"The audio file {path} does not exist.")
    try:
        with wave.open(str(path), "rb") as handle:
            return WavInfo(
                sample_rate=handle.getframerate(),
                channels=handle.getnchannels(),
                sample_width=handle.getsampwidth(),
                frames=handle.getnframes(),
            )
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioError(f"{path.name} is not a PCM WAV file ({exc}).") from exc


def is_standard_wav(path: Path | str, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bool:
    try:
        return read_wav_info(path).is_standard(sample_rate)
    except AudioError:
        return False


def write_silence(path: Path | str, frames: int, sample_rate: int = DEFAULT_SAMPLE_RATE) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(int(frames), 0)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(SAMPLE_WIDTH)
        out.setframerate(sample_rate)
        remaining = frames
        while remaining > 0:
            chunk = min(remaining, CHUNK_FRAMES)
            out.writeframes(bytes(chunk * SAMPLE_WIDTH))
            remaining -= chunk
    return frames


def write_pcm_wav(
    path: Path | str, pcm: bytes, sample_rate: int = DEFAULT_SAMPLE_RATE, channels: int = 1
) -> WavInfo:
    """Raw 16-bit little-endian PCM bytes -> a WAV file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_size = SAMPLE_WIDTH * channels
    usable = len(pcm) - (len(pcm) % frame_size)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(SAMPLE_WIDTH)
        out.setframerate(sample_rate)
        out.writeframes(pcm[:usable])
    return WavInfo(sample_rate, channels, SAMPLE_WIDTH, usable // frame_size)


@dataclass(frozen=True)
class Segment:
    """Where one input landed inside the stitched file, in frames."""

    start_frame: int
    end_frame: int

    def start_s(self, sample_rate: int) -> float:
        return round(self.start_frame / sample_rate, 6)

    def end_s(self, sample_rate: int) -> float:
        return round(self.end_frame / sample_rate, 6)


def concat_wavs(
    parts: list[Path | str],
    dest: Path | str,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    gap_s: float = 0.0,
) -> list[Segment]:
    """Stitch ``parts`` (all ``sample_rate`` mono 16-bit) into ``dest`` with ``gap_s`` of
    silence between them. Returns the exact frame span of every part."""
    if not parts:
        raise AudioError("There is nothing to stitch: no sentence audio was produced.")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    gap_frames = max(0, round(gap_s * sample_rate))
    segments: list[Segment] = []
    written = 0
    tmp = dest.with_name(dest.name + ".part.wav")
    try:
        with wave.open(str(tmp), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(SAMPLE_WIDTH)
            out.setframerate(sample_rate)
            for index, part in enumerate(parts):
                info = read_wav_info(part)
                if not info.is_standard(sample_rate):
                    raise AudioError(
                        f"{Path(part).name} is {info.sample_rate} Hz, {info.channels} channel(s), "
                        f"{info.sample_width * 8}-bit; expected {sample_rate} Hz mono 16-bit."
                    )
                if index > 0 and gap_frames:
                    remaining = gap_frames
                    while remaining > 0:
                        chunk = min(remaining, CHUNK_FRAMES)
                        out.writeframes(bytes(chunk * SAMPLE_WIDTH))
                        remaining -= chunk
                    written += gap_frames
                start = written
                with wave.open(str(part), "rb") as source:
                    while True:
                        data = source.readframes(CHUNK_FRAMES)
                        if not data:
                            break
                        out.writeframes(data)
                        written += len(data) // SAMPLE_WIDTH
                segments.append(Segment(start_frame=start, end_frame=written))
        tmp.replace(dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return segments


def slice_wav(
    source: Path | str, dest: Path | str, start_frame: int, end_frame: int
) -> WavInfo:
    """Copy frames ``[start_frame, end_frame)`` of ``source`` into ``dest``."""
    source, dest = Path(source), Path(dest)
    info = read_wav_info(source)
    start = max(0, min(int(start_frame), info.frames))
    end = max(start, min(int(end_frame), info.frames))
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame_size = info.sample_width * info.channels
    with wave.open(str(source), "rb") as inp, wave.open(str(dest), "wb") as out:
        out.setnchannels(info.channels)
        out.setsampwidth(info.sample_width)
        out.setframerate(info.sample_rate)
        inp.setpos(start)
        remaining = end - start
        while remaining > 0:
            chunk = min(remaining, CHUNK_FRAMES)
            data = inp.readframes(chunk)
            if not data:
                break
            out.writeframes(data)
            remaining -= len(data) // frame_size
    return WavInfo(info.sample_rate, info.channels, info.sample_width, end - start)

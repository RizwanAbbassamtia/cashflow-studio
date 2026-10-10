"""FFmpeg and ffprobe, used to convert any recording or provider output into the one format
the pipeline works with (WAV, 48 kHz, mono, 16-bit PCM) and to measure durations.

The tools are found through ``FFMPEG_PATH`` / ``FFPROBE_PATH`` (the Settings screen writes
them to ``.env``), then on ``PATH``. Every call is a short subprocess with no console window
on Windows; a missing tool raises :class:`AudioToolMissing` with the same fix hint the
Doctor page shows.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .errors import AudioError, AudioToolMissing

DEFAULT_SAMPLE_RATE = 48000
DEFAULT_CHANNELS = 1
TOOL_TIMEOUT_S = 600
INSTALL_HINT = (
    "Install FFmpeg (for example: winget install Gyan.FFmpeg) or set FFMPEG_PATH and "
    "FFPROBE_PATH in Settings > API keys."
)


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _find(name: str, env_name: str) -> str | None:
    configured = os.environ.get(env_name, "").strip()
    candidates = [configured] if configured else []
    if name == "ffprobe":
        ffmpeg = os.environ.get("FFMPEG_PATH", "").strip()
        if ffmpeg:
            suffix = ".exe" if sys.platform == "win32" else ""
            candidates.append(str(Path(ffmpeg).with_name(f"ffprobe{suffix}")))
    candidates.append(name)
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def ffmpeg_path() -> str:
    found = _find("ffmpeg", "FFMPEG_PATH")
    if not found:
        raise AudioToolMissing(f"FFmpeg was not found on this PC. {INSTALL_HINT}")
    return found


def ffprobe_path() -> str:
    found = _find("ffprobe", "FFPROBE_PATH")
    if not found:
        raise AudioToolMissing(f"FFprobe was not found on this PC. {INSTALL_HINT}")
    return found


def tools_available() -> bool:
    """True when both ffmpeg and ffprobe can be found (no subprocess is started)."""
    return (
        _find("ffmpeg", "FFMPEG_PATH") is not None
        and _find("ffprobe", "FFPROBE_PATH") is not None
    )


class AudioCancelled(AudioError):
    """The cancel flag was set while the tool ran; the output was not written."""


def _run(
    command: list[str], what: str, cancel: threading.Event | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the tool and capture its output. When ``cancel`` is set while it runs (the
    project was archived, the app closed) the child is killed within a quarter of a second
    and :class:`AudioCancelled` is raised, so no file in the project folder stays open."""
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed program, our own arguments
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
        )
    except OSError as exc:
        raise AudioError(f"{what} could not be started: {exc}") from exc
    killed = threading.Event()

    def watchdog() -> None:
        while process.poll() is None:
            if cancel is not None and cancel.wait(0.25):
                killed.set()
                process.kill()
                return
            if cancel is None:
                time.sleep(0.25)

    watcher = threading.Thread(target=watchdog, name="audio-tool-cancel", daemon=True)
    watcher.start()
    try:
        stdout, stderr = process.communicate(timeout=TOOL_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise AudioError(
            f"{what} took longer than {TOOL_TIMEOUT_S} seconds and was stopped."
        ) from exc
    finally:
        watcher.join(timeout=1.0)
    if killed.is_set():
        raise AudioCancelled(f"{what} was stopped.")
    completed = subprocess.CompletedProcess(command, int(process.returncode or 0), stdout, stderr)
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        detail = tail[-1] if tail else f"exit code {completed.returncode}"
        raise AudioError(f"{what} failed: {detail}")
    return completed


def convert_to_wav(
    source: Path | str,
    dest: Path | str,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = DEFAULT_CHANNELS,
    cancel: threading.Event | None = None,
) -> Path:
    """Any audio (or video) file -> WAV ``sample_rate`` Hz, ``channels`` channels, 16-bit PCM.

    ``cancel`` stops FFmpeg early (see :func:`_run`); the destination is then not written."""
    source, dest = Path(source), Path(dest)
    if not source.is_file():
        raise AudioError(f"The audio file {source} does not exist.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part.wav")
    command = [
        ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(source),
        "-vn",
        "-map_metadata",
        "-1",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(tmp),
    ]
    try:
        _run(command, f"Converting {source.name} to WAV", cancel)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return dest


def convert_to_mp3(
    source: Path | str,
    dest: Path | str,
    *,
    bitrate_kbps: int = 96,
    sample_rate: int | None = None,
    channels: int = DEFAULT_CHANNELS,
    cancel: threading.Event | None = None,
) -> Path:
    """Any audio (or video) file -> MP3 at ``bitrate_kbps`` (mono by default), for uploads
    with a size limit such as a voice-clone sample. ``sample_rate`` ``None`` keeps the
    source rate. ``cancel`` works as in :func:`convert_to_wav`."""
    source, dest = Path(source), Path(dest)
    if not source.is_file():
        raise AudioError(f"The audio file {source} does not exist.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part.mp3")
    command = [
        ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(source),
        "-vn",
        "-map_metadata",
        "-1",
        "-ac",
        str(channels),
    ]
    if sample_rate:
        command += ["-ar", str(sample_rate)]
    command += ["-c:a", "libmp3lame", "-b:a", f"{int(bitrate_kbps)}k", "-f", "mp3", str(tmp)]
    try:
        _run(command, f"Converting {source.name} to MP3", cancel)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return dest


def probe_duration(path: Path | str) -> float:
    """Duration in seconds as ffprobe reports it (works for any container)."""
    path = Path(path)
    if not path.is_file():
        raise AudioError(f"The audio file {path} does not exist.")
    command = [
        ffprobe_path(),
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=duration",
        "-of",
        "json",
        str(path),
    ]
    completed = _run(command, f"Reading the length of {path.name}")
    try:
        data = json.loads(completed.stdout or "{}")
    except ValueError as exc:
        raise AudioError(f"ffprobe gave an unreadable answer for {path.name}.") from exc
    candidates: list[float] = []
    fmt = data.get("format") or {}
    if fmt.get("duration") not in (None, "", "N/A"):
        candidates.append(float(fmt["duration"]))
    for stream in data.get("streams") or []:
        value = stream.get("duration")
        if value not in (None, "", "N/A"):
            candidates.append(float(value))
    if not candidates:
        raise AudioError(f"ffprobe could not read the length of {path.name}.")
    return round(max(candidates), 6)

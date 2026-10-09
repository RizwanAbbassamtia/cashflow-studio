"""The FFmpeg side of the edit stage: one filter graph per render, run with progress and a
cancel flag.

Per scene: ``scale`` (cover the working canvas) -> ``crop`` -> ``zoompan`` interpolating the
start rectangle to the end rectangle over the scene's frames -> ``format=yuv420p``. Scenes
are chained with ``xfade`` (a transition starts where the scene ends and overlaps the next
scene), popups are PNG inputs laid over with ``overlay`` (alpha fade, optional slide),
captions are burnt with ``subtitles`` (libass, the vendored Noto fonts), the voice ducks the
music through ``sidechaincompress`` + ``amix`` and the mix is normalised with ``loudnorm``.

The graph is written to a file and passed with ``-/filter_complex <file>`` (FFmpeg 7+; the
older ``-filter_complex_script`` is gone in FFmpeg 9), so an 80-scene video never hits the
Windows command-line limit. Progress comes from ``-progress pipe:1`` (``out_time_us``);
setting the cancel flag kills FFmpeg within a quarter of a second.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..models.timeline import Timeline, TimelinePopup
from .fonts import fonts_dir
from .geometry import cover_region, rect_to_canvas
from .presets import RenderConfig, RenderPreset

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str, float], None]

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PROBE_TIMEOUT_S = 60
PEAK_TIMEOUT_S = 600
REQUIRED_FILTERS: dict[str, str] = {
    "subtitles": "libass (burnt-in captions)",
    "xfade": "scene transitions",
    "zoompan": "camera moves",
    "sidechaincompress": "music ducking",
    "loudnorm": "loudness normalisation",
    "overlay": "popups",
}
FADE_TO_WHITE = {"fadewhite"}
FADE_TRANSITIONS = {"fade", "fadeblack", "fadewhite", "fadegrays", "dissolve"}


class RenderError(Exception):
    """FFmpeg could not be run or failed; the message is plain English."""


class RenderCancelled(RenderError):
    """The cancel flag was set while FFmpeg ran; the output file is incomplete."""


# Tools -----------------------------------------------------------------------------------------


def _which(configured: str, name: str) -> str | None:
    for candidate in (configured, name):
        if candidate:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def find_ffmpeg(settings: Any = None) -> str:
    configured = str(getattr(settings, "ffmpeg_path", "") or os.environ.get("FFMPEG_PATH", ""))
    found = _which(configured, "ffmpeg")
    if not found:
        raise RenderError(
            "FFmpeg was not found on this PC. Install it (winget install Gyan.FFmpeg) or set "
            "FFMPEG_PATH in Settings, then run the health checks."
        )
    return found


def find_ffprobe(settings: Any = None) -> str:
    configured = str(getattr(settings, "ffprobe_path", "") or os.environ.get("FFPROBE_PATH", ""))
    found = _which(configured, "ffprobe")
    if not found:
        ffmpeg = _which(
            str(getattr(settings, "ffmpeg_path", "") or os.environ.get("FFMPEG_PATH", "")),
            "ffmpeg",
        )
        if ffmpeg:
            sibling = Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix)
            if sibling.is_file():
                return str(sibling)
        raise RenderError(
            "FFprobe was not found on this PC. It normally comes with FFmpeg; set FFPROBE_PATH "
            "in Settings if it lives somewhere else."
        )
    return found


def ffmpeg_filters(ffmpeg: str | None = None) -> set[str]:
    """Names of the filters this FFmpeg build offers (``ffmpeg -filters``)."""
    completed = subprocess.run(
        [ffmpeg or find_ffmpeg(), "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=PROBE_TIMEOUT_S,
        creationflags=NO_WINDOW,
        check=False,
    )
    names: set[str] = set()
    for line in (completed.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and "->" in parts[2]:
            names.add(parts[1])
    return names


def missing_filters(ffmpeg: str | None = None) -> list[str]:
    present = ffmpeg_filters(ffmpeg)
    return [name for name in REQUIRED_FILTERS if name not in present]


# Probing ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolRun:
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False


def run_tool(
    command: list[str],
    *,
    timeout_s: float,
    cancel: threading.Event | None = None,
) -> ToolRun:
    """Run ffmpeg/ffprobe for a short job and capture its output. The child is killed as
    soon as ``cancel`` is set (the project was archived or the app closed), so a probe or a
    loudness scan never keeps a file in the project folder open after a cancel."""
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
    except OSError as exc:
        raise RenderError(f"{Path(command[0]).name} could not be started: {exc}") from exc
    killed = threading.Event()

    def watchdog() -> None:
        while process.poll() is None:
            if cancel is not None and cancel.wait(0.25):
                killed.set()
                process.kill()
                return
            if cancel is None:
                time.sleep(0.25)

    watcher = threading.Thread(target=watchdog, name="tool-cancel", daemon=True)
    watcher.start()
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise RenderError(
            f"{Path(command[0]).name} took longer than {timeout_s:.0f} seconds and was stopped."
        ) from None
    finally:
        watcher.join(timeout=1.0)
    return ToolRun(
        returncode=int(process.returncode or 0), stdout=stdout or "", stderr=stderr or "",
        cancelled=killed.is_set(),
    )


@dataclass(frozen=True)
class MediaInfo:
    duration_s: float
    width: int = 0
    height: int = 0
    has_video: bool = False
    has_audio: bool = False
    size_bytes: int = 0


def probe_media(
    path: Path | str, ffprobe: str | None = None, cancel: threading.Event | None = None
) -> MediaInfo:
    """Duration, size and stream kinds of a media file (``ffprobe`` JSON)."""
    file = Path(path)
    if not file.is_file():
        raise RenderError(f"The file {file} does not exist.")
    completed = run_tool(
        [
            ffprobe or find_ffprobe(),
            "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,width,height",
            "-of", "json",
            str(file),
        ],
        timeout_s=PROBE_TIMEOUT_S,
        cancel=cancel,
    )
    if completed.cancelled:
        raise RenderCancelled(f"Reading {file.name} was stopped.")
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()
        raise RenderError(
            f"{file.name} could not be read by ffprobe: {detail[-1] if detail else 'unknown error'}"
        )
    try:
        data = json.loads(completed.stdout or "{}")
    except ValueError as exc:
        raise RenderError(f"ffprobe gave no readable answer for {file.name}.") from exc
    streams = data.get("streams") or []
    width = height = 0
    has_video = has_audio = False
    for stream in streams:
        kind = stream.get("codec_type")
        if kind == "video" and not has_video:
            has_video = True
            width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        elif kind == "audio":
            has_audio = True
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return MediaInfo(
        duration_s=round(duration, 3),
        width=width,
        height=height,
        has_video=has_video,
        has_audio=has_audio,
        size_bytes=file.stat().st_size,
    )


def probe_duration(path: Path | str, ffprobe: str | None = None) -> float:
    return probe_media(path, ffprobe).duration_s


_PEAK_RE = re.compile(r"Peak:\s*(-?[0-9.]+|-inf)\s*dBFS", re.IGNORECASE)


def measure_true_peak(
    path: Path | str, ffmpeg: str | None = None, cancel: threading.Event | None = None
) -> float | None:
    """The true peak of a file's audio in dBTP (``ebur128=peak=true``); ``None`` for silence,
    when FFmpeg cannot measure it, or when the scan was cancelled."""
    try:
        completed = run_tool(
            [
                ffmpeg or find_ffmpeg(),
                "-hide_banner", "-nostats", "-nostdin",
                "-i", str(path),
                "-vn", "-af", "ebur128=peak=true", "-f", "null", "-",
            ],
            timeout_s=PEAK_TIMEOUT_S,
            cancel=cancel,
        )
    except RenderError:
        return None
    if completed.cancelled:
        return None
    text = completed.stderr or ""
    marker = text.rfind("True peak:")
    if marker < 0:
        return None
    match = _PEAK_RE.search(text, marker)
    if not match or match.group(1).lower() == "-inf":
        return None
    try:
        return round(float(match.group(1)), 2)
    except ValueError:
        return None


# Filter graph ------------------------------------------------------------------------------------


def fmt(value: float) -> str:
    """A number for an FFmpeg expression: no exponent, no trailing zeros, no locale."""
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-", "-0") else "0"


def escape_filter_path(path: Path | str) -> str:
    """A file path as a quoted filter option value (what FFmpeg's option parser accepts on
    Windows): forward slashes, the drive colon, commas and brackets escaped for the option
    parser, and the whole value single-quoted for the graph parser.

    Nothing can be escaped inside a quoted graph token, so an apostrophe is written as
    ``'\\\\\\''``: the quote closes, ``\\\\`` and ``\\'`` give the option parser ``\\'`` (a
    literal apostrophe), and the quote opens again (``o'brien`` -> ``'o'\\\\\\''brien'``).
    """
    text = str(path).replace("\\", "/")
    for char in (":", ",", "[", "]", ";"):
        text = text.replace(char, "\\" + char)
    text = text.replace("'", "'\\\\\\''")
    return f"'{text}'"


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except (OSError, UnidentifiedImageError) as exc:
        raise RenderError(f"The picture {path} could not be read: {exc}") from exc


def resolve_path(folder: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(folder) / path


@dataclass(frozen=True)
class PopupAsset:
    """A popup PNG rendered for one preset, with its place in the frame."""

    popup: TimelinePopup
    png: Path
    width: int
    height: int
    x: int
    y: int


@dataclass
class RenderPlan:
    preset: RenderPreset
    width: int
    height: int
    fps: int
    canvas: tuple[int, int]
    inputs: list[list[str]] = field(default_factory=list)
    filter_graph: str = ""
    total_s: float = 0.0
    """Length of the finished file (intro + scenes + outro)."""
    x264_preset: str = "medium"
    crf: int = 20
    config: RenderConfig = field(default_factory=RenderConfig)

    def output_args(self) -> list[str]:
        config = self.config
        return [
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", self.x264_preset,
            "-crf", str(self.crf),
            "-profile:v", config.x264_profile,
            "-pix_fmt", config.pix_fmt,
            "-r", str(self.fps),
            "-c:a", config.audio_codec,
            "-b:a", f"{config.audio_bitrate_kbps}k",
            "-ar", str(config.audio_sample_rate),
            "-ac", "2",
            "-movflags", config.movflags,
            "-t", fmt(self.total_s),
        ]


def _popup_exprs(
    asset: PopupAsset, scale: float, config: RenderConfig
) -> tuple[str, str]:
    """``x`` and ``y`` overlay expressions: slide_left eases in from the right over
    ``slide_s``; pop rises into place; fade stays put (the alpha fade is on the input)."""
    look = config.popups
    travel = max(1.0, look.slide_px * scale)
    start = fmt(asset.popup.start_s)
    ease = f"pow(max(0\\,1-(t-{start})/{fmt(max(look.slide_s, 0.01))})\\,2)"
    x, y = str(asset.x), str(asset.y)
    if asset.popup.anim == "slide_left":
        return f"{x}+{fmt(travel)}*{ease}", y
    if asset.popup.anim == "pop":
        return x, f"{y}+{fmt(travel / 2)}*{ease}"
    return x, y


def build_plan(
    timeline: Timeline,
    preset: RenderPreset,
    *,
    folder: Path,
    config: RenderConfig,
    popups: Sequence[PopupAsset] = (),
    ass_path: Path | None = None,
    captions: bool = True,
    x264_preset: str | None = None,
    fonts_folder: Path | None = None,
) -> RenderPlan:
    """The inputs and the filter graph for one preset. Pure: nothing is run here."""
    folder = Path(folder)
    width, height = preset.size(timeline.aspect)
    canvas_w, canvas_h = preset.canvas(timeline.aspect)
    fps = int(timeline.fps)
    plan = RenderPlan(
        preset=preset,
        width=width,
        height=height,
        fps=fps,
        canvas=(canvas_w, canvas_h),
        x264_preset=config.x264_preset_for(preset, x264_preset),
        crf=preset.crf,
        config=config,
    )
    parts: list[str] = []
    scenes = timeline.scenes
    if not scenes:
        raise RenderError("The timeline has no scenes.")
    first_frame = round(scenes[0].start_s * fps)
    end_frames: list[int] = []
    transition_frames: list[int] = []
    for index, scene in enumerate(scenes):
        start_f = round(scene.start_s * fps) - first_frame
        end_f = round(scene.end_s * fps) - first_frame
        last = index == len(scenes) - 1
        trans_f = round(scene.transition_out.duration_s * fps)
        if trans_f < config.transition_min_frames:
            trans_f = 0
        if last:
            trans_f = min(trans_f, max(end_f - start_f - 1, 0))
        clip_frames = max((end_f - start_f) + (0 if last else trans_f), 1)
        end_frames.append(end_f)
        transition_frames.append(trans_f)

        image = resolve_path(folder, scene.image)
        if not image.is_file():
            raise RenderError(f"The picture for scene {index + 1} is missing: {image}")
        image_w, image_h = _image_size(image)
        region = cover_region(image_w, image_h, width, height)
        x0, y0, w0, _h0 = rect_to_canvas(scene.motion.start_rect, region, canvas_w, canvas_h)
        x1, y1, w1, _h1 = rect_to_canvas(scene.motion.end_rect, region, canvas_w, canvas_h)
        progress = f"min(on/{clip_frames - 1}\\,1)" if clip_frames > 1 else "0"
        zoom = f"{fmt(canvas_w)}/({fmt(w0)}+({fmt(w1 - w0)})*{progress})"
        x_expr = f"{fmt(x0)}+({fmt(x1 - x0)})*{progress}"
        y_expr = f"{fmt(y0)}+({fmt(y1 - y0)})*{progress}"
        plan.inputs.append(["-i", str(image)])
        input_index = len(plan.inputs) - 1
        chain = (
            f"[{input_index}:v]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase"
            f":flags={config.scale_flags},crop={canvas_w}:{canvas_h},format=yuv420p,setsar=1,"
            f"zoompan=z='{zoom}':x='{x_expr}':y='{y_expr}':d={clip_frames}:s={width}x{height}"
            f":fps={fps},format=yuv420p,setsar=1"
        )
        if last and trans_f > 0:
            colour = "white" if scene.transition_out.type in FADE_TO_WHITE else "black"
            chain += (
                f",fade=t=out:st={fmt((clip_frames - trans_f) / fps)}:d={fmt(trans_f / fps)}"
                f":color={colour}"
            )
        parts.append(f"{chain}[s{index}]")

    current = "[s0]"
    for index in range(len(scenes) - 1):
        trans_f = transition_frames[index]
        offset = end_frames[index] / fps
        if trans_f > 0:
            parts.append(
                f"{current}[s{index + 1}]xfade=transition={scenes[index].transition_out.type}"
                f":duration={fmt(trans_f / fps)}:offset={fmt(offset)}[v{index + 1}]"
            )
        else:
            parts.append(f"{current}[s{index + 1}]concat=n=2:v=1:a=0[v{index + 1}]")
        current = f"[v{index + 1}]"
    scenes_s = end_frames[-1] / fps

    scale = min(width, height) / 1080.0
    for number, asset in enumerate(popups):
        duration = max(asset.popup.end_s - asset.popup.start_s, 0.2)
        fade = min(config.popups.fade_s, duration / 3)
        plan.inputs.append(
            ["-loop", "1", "-framerate", str(fps), "-t", fmt(duration), "-i", str(asset.png)]
        )
        input_index = len(plan.inputs) - 1
        x_expr, y_expr = _popup_exprs(asset, scale, config)
        parts.append(
            f"[{input_index}:v]format=rgba,fade=t=in:st=0:d={fmt(fade)}:alpha=1,"
            f"fade=t=out:st={fmt(duration - fade)}:d={fmt(fade)}:alpha=1,"
            f"setpts=PTS+{fmt(asset.popup.start_s)}/TB[p{number}]"
        )
        parts.append(
            f"{current}[p{number}]overlay=x='{x_expr}':y='{y_expr}':eof_action=pass"
            f":enable='between(t\\,{fmt(asset.popup.start_s)}\\,{fmt(asset.popup.end_s)})'"
            f"[vp{number}]"
        )
        current = f"[vp{number}]"

    if captions and ass_path is not None and timeline.captions.cues:
        parts.append(
            f"{current}subtitles=filename={escape_filter_path(ass_path)}"
            f":fontsdir={escape_filter_path(fonts_folder or fonts_dir())}[vcap]"
        )
        current = "[vcap]"
    main_video = current

    # Audio: voice (padded to the scenes' length), music ducked under it, loudness normalised.
    rate = config.audio_sample_rate
    aformat = f"aformat=sample_fmts=fltp:sample_rates={rate}:channel_layouts=stereo"
    voice = resolve_path(folder, timeline.voice.path)
    if not voice.is_file():
        raise RenderError(f"The voice file is missing: {voice}")
    plan.inputs.append(["-i", str(voice)])
    voice_index = len(plan.inputs) - 1
    voice_chain = f"[{voice_index}:a]{aformat}"
    if timeline.voice.start_s > 0:
        voice_chain += f",adelay={int(round(timeline.voice.start_s * 1000))}:all=1"
    voice_chain += f",atrim=0:{fmt(scenes_s)},apad=whole_dur={fmt(scenes_s)}"
    music = timeline.music
    music_file = resolve_path(folder, music.path) if music.path else None
    if music_file is not None and music_file.is_file():
        parts.append(f"{voice_chain},asplit=2[voice_mix][voice_sc]")
        plan.inputs.append(["-stream_loop", "-1", "-i", str(music_file)])
        music_index = len(plan.inputs) - 1
        music_chain = (
            f"[{music_index}:a]{aformat},atrim={fmt(music.start_s)}:{fmt(music.start_s + scenes_s)}"
            f",asetpts=PTS-STARTPTS,volume={fmt(music.gain_db)}dB"
        )
        if music.fade_in_s > 0:
            music_chain += f",afade=t=in:st=0:d={fmt(min(music.fade_in_s, scenes_s))}"
        if 0 < music.fade_out_s < scenes_s:
            music_chain += (
                f",afade=t=out:st={fmt(scenes_s - music.fade_out_s)}:d={fmt(music.fade_out_s)}"
            )
        parts.append(f"{music_chain}[music_in]")
        parts.append(
            "[music_in][voice_sc]sidechaincompress"
            f"=threshold={fmt(config.duck_threshold)}:ratio={fmt(config.duck_ratio)}"
            f":attack={fmt(config.duck_attack_ms)}:release={fmt(config.duck_release_ms)}[ducked]"
        )
        parts.append(
            "[voice_mix][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]"
        )
    else:
        parts.append(f"{voice_chain}[mix]")
    parts.append(
        f"[mix]loudnorm=I={fmt(config.loudnorm_i)}:TP={fmt(config.loudnorm_tp)}"
        f":LRA={fmt(config.loudnorm_lra)},{aformat}[amain]"
    )

    # Intro and outro clips around the scenes (optional).
    total = scenes_s
    sequence_v: list[str] = []
    sequence_a: list[str] = []
    for label, clip in (("intro", timeline.intro), ("outro", timeline.outro)):
        if not clip.path:
            continue
        file = resolve_path(folder, clip.path)
        if not file.is_file():
            raise RenderError(f"The {label} video is missing: {file}")
        info = probe_media(file)
        if not info.has_video or info.duration_s <= 0:
            raise RenderError(f"The {label} file {file.name} has no video.")
        plan.inputs.append(["-i", str(file)])
        clip_index = len(plan.inputs) - 1
        parts.append(
            f"[{clip_index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease"
            f":flags={config.scale_flags},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={fps},format=yuv420p,trim=0:{fmt(info.duration_s)},"
            f"setpts=PTS-STARTPTS[{label}_v]"
        )
        if info.has_audio:
            parts.append(
                f"[{clip_index}:a]{aformat},atrim=0:{fmt(info.duration_s)},asetpts=PTS-STARTPTS,"
                f"apad=whole_dur={fmt(info.duration_s)}[{label}_a]"
            )
        else:
            parts.append(
                f"anullsrc=r={rate}:cl=stereo,atrim=0:{fmt(info.duration_s)}[{label}_a]"
            )
        sequence_v.append(f"[{label}_v]")
        sequence_a.append(f"[{label}_a]")
        total += info.duration_s
        if label == "intro":
            sequence_v.append(main_video)
            sequence_a.append("[amain]")
    if sequence_v:
        if "[amain]" not in sequence_a:
            sequence_v.insert(0, main_video)
            sequence_a.insert(0, "[amain]")
        pairs = "".join(f"{v}{a}" for v, a in zip(sequence_v, sequence_a, strict=True))
        parts.append(f"{pairs}concat=n={len(sequence_v)}:v=1:a=1[vout][aout]")
    else:
        parts.append(f"{main_video}null[vout]")
        parts.append("[amain]anull[aout]")

    plan.filter_graph = ";\n".join(parts) + "\n"
    plan.total_s = round(total, 3)
    return plan


# Running ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderResult:
    path: Path
    duration_s: float
    size_bytes: int
    seconds_taken: float
    width: int = 0
    height: int = 0


def parse_progress_line(line: str) -> tuple[str, str] | None:
    key, sep, value = line.strip().partition("=")
    if not sep:
        return None
    return key.strip(), value.strip()


BENIGN_LOG_LINES: tuple[re.Pattern[str], ...] = (
    # libass tries every file in fontsdir as a font; the licence text is not one.
    re.compile(r"Error opening memory font '(OFL|LICEN[CS]E)[^']*'"),
    re.compile(r"Guessed Channel Layout"),
)


def is_benign_log_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in BENIGN_LOG_LINES)


def _pump_stderr(stream: Any, log_file: Any) -> None:
    """Copy FFmpeg's messages to the log, leaving out the lines that only confuse people."""
    try:
        for line in stream:
            if not is_benign_log_line(line):
                log_file.write(line)
    except (OSError, ValueError):
        pass


def _tail(path: Path, lines: int = 12) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    rows = [row for row in text.splitlines() if row.strip()]
    return "\n".join(rows[-lines:])


def run_render(
    plan: RenderPlan,
    output: Path,
    *,
    work_dir: Path,
    log_path: Path,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    label: str | None = None,
) -> RenderResult:
    """Run one render to ``output``. FFmpeg's messages go to ``log_path`` (appended), the
    graph to ``filters_<preset>.txt`` next to it. Raises :class:`RenderCancelled` when the
    cancel flag is set, :class:`RenderError` when FFmpeg fails."""
    ffmpeg = ffmpeg or find_ffmpeg()
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    script = work_dir / f"filters_{plan.preset.id}.txt"
    script.write_text(plan.filter_graph, encoding="utf-8")
    command = [
        ffmpeg,
        "-hide_banner", "-nostdin", "-loglevel", "warning", "-nostats",
        "-progress", "pipe:1", "-stats_period", "0.5", "-y",
    ]
    for item in plan.inputs:
        command.extend(item)
    command += ["-/filter_complex", str(script), *plan.output_args(), str(output)]
    name = label or plan.preset.id
    started = time.monotonic()
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(
            f"\n===== {stamp}  render {name} -> {output.name} ({plan.width}x{plan.height}, "
            f"{plan.total_s:g} s, x264 {plan.x264_preset} crf {plan.crf}) =====\n"
        )
        log_file.write("command: " + subprocess.list2cmdline(command) + "\n")
        log_file.flush()
        try:
            process = subprocess.Popen(
                command,
                cwd=str(work_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW,
            )
        except OSError as exc:
            raise RenderError(f"FFmpeg could not be started: {exc}") from exc

        killed = threading.Event()

        def watchdog() -> None:
            while process.poll() is None:
                if cancel is not None and cancel.wait(0.25):
                    killed.set()
                    process.kill()
                    return
                if cancel is None:
                    time.sleep(0.25)

        watcher = threading.Thread(target=watchdog, name=f"render-cancel-{name}", daemon=True)
        watcher.start()
        pump = threading.Thread(
            target=_pump_stderr, args=(process.stderr, log_file), name=f"render-log-{name}",
            daemon=True,
        )
        pump.start()
        last_pct = -1.0
        last_report = 0.0
        total_us = max(plan.total_s, 0.01) * 1_000_000
        assert process.stdout is not None
        try:
            for line in process.stdout:
                parsed = parse_progress_line(line)
                if parsed is None:
                    continue
                key, value = parsed
                pct: float | None = None
                if key == "out_time_us" or key == "out_time_ms":
                    try:
                        pct = min(99.0, max(0.0, int(value) / total_us * 100.0))
                    except ValueError:
                        pct = None
                elif key == "progress" and value == "end":
                    pct = 100.0
                if pct is None or progress is None:
                    continue
                now = time.monotonic()
                if pct >= last_pct + 1.0 or pct == 100.0 or now - last_report > 2.0:
                    last_pct, last_report = pct, now
                    progress(f"Rendering {name} {int(pct)}%", pct)
        finally:
            process.stdout.close()
            returncode = process.wait()
            watcher.join(timeout=1.0)
            pump.join(timeout=5.0)
            if process.stderr is not None:
                process.stderr.close()
        log_file.write(f"exit code {returncode} after {time.monotonic() - started:.1f} s\n")
        log_file.flush()
    if killed.is_set() or (cancel is not None and cancel.is_set()):
        try:
            output.unlink()
        except OSError:
            pass
        raise RenderCancelled(f"The {name} render was stopped.")
    if returncode != 0:
        detail = _tail(log_path)
        raise RenderError(
            f"FFmpeg could not render {name} (exit code {returncode}). The last lines of "
            f"{log_path.name}:\n{detail}"
        )
    info = probe_media(output, ffprobe, cancel)
    return RenderResult(
        path=output,
        duration_s=info.duration_s,
        size_bytes=info.size_bytes,
        seconds_taken=round(time.monotonic() - started, 1),
        width=info.width,
        height=info.height,
    )

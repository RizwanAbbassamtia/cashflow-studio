"""Builds ``07_edit/timeline.json`` from the storyboard, the voice timing and the pictures.

Rules (docs/M3-M4-CONTRACT.md section 3):

* a scene runs from the start of its first sentence to the start of the next scene; the
  last scene runs to the end of the voice plus ``tail_s``;
* a transition never takes more than a quarter of the shorter neighbouring scene;
* a popup appears ``lead_s`` after its key sentence (the scene's first sentence) starts
  and leaves after at most ``max_show_s``, and before the scene ends;
* captions are the words of ``timing.json`` grouped by the language's rules;
* the music track is picked deterministically from the channel folder and marked
  ``license_ok`` only when a licence file sits next to it.

Nothing here invents timing: every second comes from ``timing.json``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..models.storyboard import Scene, StoryboardDoc
from ..models.timeline import (
    MusicTrack,
    ProxySize,
    Timeline,
    TimelineCaptions,
    TimelineMotion,
    TimelinePopup,
    TimelineScene,
    TimelineTransition,
    VoiceTrack,
)
from .captions import caption_style_for, chunk_cues, pause_break_s, rules_for
from .geometry import rect_in_pixels
from .music import has_licence, pick_track, resolve_track
from .presets import RenderConfig, load_render_config

log = logging.getLogger(__name__)

MIN_SCENE_S = 0.2
TIMING_FILE = "timing.json"
IMAGES_DOC_FILE = "images.json"


class TimelineBuildError(ValueError):
    """The files needed for the timeline are missing or unreadable (plain English)."""


# timing.json -------------------------------------------------------------------------------


@dataclass(frozen=True)
class TimedWord:
    text: str
    start_s: float
    end_s: float
    confidence: float = 1.0


@dataclass(frozen=True)
class TimedSentence:
    id: str
    text: str
    start_s: float
    end_s: float
    words: tuple[TimedWord, ...] = ()

    @property
    def duration_s(self) -> float:
        return round(self.end_s - self.start_s, 6)


@dataclass(frozen=True)
class Timing:
    """The parts of ``05_voice/timing.json`` the edit stage reads."""

    duration_s: float
    sample_rate: int = 48000
    source: str = "estimated"
    timing_confidence: str = "medium"
    sentences: tuple[TimedSentence, ...] = ()

    def sentence(self, sentence_id: str) -> TimedSentence | None:
        for item in self.sentences:
            if item.id == sentence_id:
                return item
        return None


def parse_timing(data: dict[str, Any]) -> Timing:
    sentences: list[TimedSentence] = []
    for raw in data.get("sentences") or []:
        if not isinstance(raw, dict):
            continue
        words = tuple(
            TimedWord(
                text=str(w.get("text", "")),
                start_s=float(w.get("start_s", 0.0)),
                end_s=float(w.get("end_s", 0.0)),
                confidence=float(w.get("confidence", 1.0) or 0.0),
            )
            for w in (raw.get("words") or [])
            if isinstance(w, dict)
        )
        sentences.append(
            TimedSentence(
                id=str(raw.get("id", "")),
                text=str(raw.get("text", "")),
                start_s=float(raw.get("start_s", 0.0)),
                end_s=float(raw.get("end_s", 0.0)),
                words=words,
            )
        )
    duration = float(data.get("duration_s") or 0.0)
    if duration <= 0 and sentences:
        duration = max(s.end_s for s in sentences)
    return Timing(
        duration_s=duration,
        sample_rate=int(data.get("sample_rate") or 48000),
        source=str(data.get("source") or "estimated"),
        timing_confidence=str(data.get("timing_confidence") or "medium"),
        sentences=tuple(sentences),
    )


def read_timing(path: Path) -> Timing:
    if not path.is_file():
        raise TimelineBuildError(
            f"The voice step has not written {path.name} yet (expected at {path}). Finish the "
            "voice step first."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TimelineBuildError(f"The file {path} could not be read: {exc}") from exc
    if not isinstance(data, dict):
        raise TimelineBuildError(f"The file {path} does not hold a timing document.")
    timing = parse_timing(data)
    if not timing.sentences or timing.duration_s <= 0:
        raise TimelineBuildError(f"The file {path} has no sentences or no duration.")
    return timing


# Scenes ------------------------------------------------------------------------------------


def scene_bounds(
    scenes: Sequence[Scene], timing: Timing, tail_s: float = 0.8
) -> list[tuple[float, float]]:
    """``(start_s, end_s)`` per scene on the voice clock: first sentence start to the next
    scene's start; the first scene starts at 0; the last ends at the voice end plus ``tail_s``.
    A scene whose sentences are not in the timing keeps its estimated length."""
    if not scenes:
        return []
    by_id = {s.id: s for s in timing.sentences}
    bounds: list[list[float]] = []
    clock = 0.0
    for scene in scenes:
        covered = [by_id[i] for i in scene.sentence_ids if i in by_id]
        if covered:
            start = min(s.start_s for s in covered)
            end = max(s.end_s for s in covered)
        else:
            start = clock
            end = clock + max(float(scene.est_duration_s or 0.0), MIN_SCENE_S)
        start = max(start, clock)
        end = max(end, start + MIN_SCENE_S)
        bounds.append([start, end])
        clock = end
    for current, following in zip(bounds, bounds[1:], strict=False):
        current[1] = max(following[0], current[0] + MIN_SCENE_S)
        following[0] = current[1]
    bounds[0][0] = 0.0
    bounds[-1][1] = max(bounds[-1][1], timing.duration_s) + max(tail_s, 0.0)
    return [(round(s, 3), round(e, 3)) for s, e in bounds]


def clip_transitions(
    bounds: Sequence[tuple[float, float]],
    durations: Sequence[float],
    *,
    max_share: float = 0.25,
    min_s: float = 0.0,
) -> list[float]:
    """Transition lengths clipped to ``max_share`` of the shorter neighbouring scene (the last
    scene's fade-out to its own length); anything under ``min_s`` becomes a plain cut (0)."""
    result: list[float] = []
    count = len(bounds)
    for index, wanted in enumerate(durations[:count]):
        this = bounds[index][1] - bounds[index][0]
        if index < count - 1:
            following = bounds[index + 1][1] - bounds[index + 1][0]
            shorter = min(this, following)
        else:
            shorter = this
        limit = max(0.0, max_share * shorter)
        duration = min(max(float(wanted), 0.0), limit)
        if duration < min_s:
            duration = 0.0
        result.append(round(duration, 3))
    return result


def popup_window(
    key_start_s: float,
    scene_start_s: float,
    scene_end_s: float,
    *,
    lead_s: float = 0.3,
    max_show_s: float = 4.0,
    end_margin_s: float = 0.2,
    min_show_s: float = 0.6,
) -> tuple[float, float] | None:
    """When a popup is on screen: ``lead_s`` after its key sentence starts until at most
    ``max_show_s`` later and ``end_margin_s`` before the scene ends. ``None`` when the scene
    is too short to show it for ``min_show_s``."""
    start = max(scene_start_s, key_start_s + lead_s)
    end = min(start + max_show_s, scene_end_s - end_margin_s)
    if end - start < min_show_s:
        start = max(scene_start_s, end - min_show_s)
        if end - start < min_show_s - 1e-9:
            return None
    return round(start, 3), round(end, 3)


def popup_anim_for(style: str | None) -> str:
    text = (style or "").lower()
    if "pop" in text:
        return "pop"
    if "fade" in text and "slide" not in text:
        return "fade"
    return "slide_left"


# Pictures ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SceneSource:
    index: int
    image_rel: str
    """Relative to the project folder, forward slashes."""
    image_abs: Path
    width: int
    height: int


def _relative(folder: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(folder.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except (OSError, UnidentifiedImageError) as exc:
        raise TimelineBuildError(f"The picture {path} could not be read: {exc}") from exc


def _images_doc_files(folder: Path) -> dict[int, str]:
    """``scene index -> file`` from ``06_images/images.json`` when the images stage wrote it."""
    path = folder / "06_images" / IMAGES_DOC_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files: dict[int, str] = {}
    for raw in (data.get("scenes") if isinstance(data, dict) else None) or []:
        if not isinstance(raw, dict) or not raw.get("file"):
            continue
        status = str(raw.get("status") or "generated")
        if status in ("rejected", "pending"):
            continue
        try:
            files[int(raw.get("scene"))] = str(raw["file"])
        except (TypeError, ValueError):
            continue
    return files


def resolve_scene_images(folder: Path, storyboard: StoryboardDoc) -> list[SceneSource]:
    """The picture of every scene: ``images.json``, then the storyboard's ``image.path``, then
    ``06_images/scene_NN.png``. Raises when a scene has no picture."""
    folder = Path(folder)
    from_doc = _images_doc_files(folder)
    sources: list[SceneSource] = []
    missing: list[int] = []
    for position, scene in enumerate(storyboard.scenes):
        candidates: list[str] = []
        if scene.index in from_doc:
            candidates.append(from_doc[scene.index])
        if scene.image.path and scene.image.status != "rejected":
            candidates.append(scene.image.path)
        candidates.append(f"06_images/scene_{scene.index + 1:02d}.png")
        candidates.append(f"06_images/scene_{position + 1:02d}.png")
        found: Path | None = None
        for candidate in candidates:
            path = Path(candidate)
            if not path.is_absolute():
                path = folder / path
            if path.is_file():
                found = path
                break
        if found is None:
            missing.append(scene.index + 1)
            continue
        width, height = _image_size(found)
        sources.append(SceneSource(scene.index, _relative(folder, found), found, width, height))
    if missing:
        listed = ", ".join(str(n) for n in missing[:12]) + (" ..." if len(missing) > 12 else "")
        raise TimelineBuildError(
            f"No picture for scene(s) {listed}. Finish the images step (or put scene_NN.png "
            "files in 06_images) first."
        )
    return sources


# The timeline ------------------------------------------------------------------------------


@dataclass
class BuildRequest:
    project_id: str
    fmt: str
    aspect: str
    language: str
    folder: Path
    storyboard: StoryboardDoc
    timing: Timing
    sources: list[SceneSource]
    width: int
    height: int
    proxy: tuple[int, int]
    presets: list[str]
    fps: int = 30
    voice_path: str = "05_voice/voice.wav"
    music_folder: str | None = None
    music_choice: str | None = None
    """``None`` = pick automatically, ``""`` = no music, else a track path or name."""
    brand_colours: list[str] = field(default_factory=list)
    captions_enabled: bool = True
    caption_style_name: str = "bold-white"
    popups_enabled: bool = True
    config: RenderConfig | None = None


def build_timeline(request: BuildRequest) -> tuple[Timeline, list[str]]:
    """The timeline plus plain-English warnings (no music, unlicensed music, skipped popups)."""
    config = request.config or load_render_config()
    warnings: list[str] = []
    scenes = list(request.storyboard.scenes)
    if not scenes:
        raise TimelineBuildError("The storyboard has no scenes.")
    sources = {s.index: s for s in request.sources}
    bounds = scene_bounds(scenes, request.timing, config.tail_s)
    transitions = clip_transitions(
        bounds,
        [float(s.transition_out.duration_s) for s in scenes],
        max_share=config.transition_max_share,
        min_s=config.transition_min_frames / max(request.fps, 1),
    )
    timeline_scenes: list[TimelineScene] = []
    for position, scene in enumerate(scenes):
        source = sources.get(scene.index)
        if source is None:
            raise TimelineBuildError(f"No picture for scene {scene.index + 1}.")
        start, end = bounds[position]
        timeline_scenes.append(
            TimelineScene(
                index=position,
                image=source.image_rel,
                start_s=start,
                end_s=end,
                motion=TimelineMotion(
                    preset=scene.motion.preset,
                    start_rect=rect_in_pixels(
                        scene.motion.start_rect, source.width, source.height,
                        request.width, request.height,
                    ),
                    end_rect=rect_in_pixels(
                        scene.motion.end_rect, source.width, source.height,
                        request.width, request.height,
                    ),
                ),
                transition_out=TimelineTransition(
                    type=scene.transition_out.type, duration_s=transitions[position]
                ),
                locked=bool(scene.locked.motion or scene.locked.transition_out),
            )
        )

    popups: list[TimelinePopup] = []
    look = config.popups
    if request.popups_enabled:
        for position, scene in enumerate(scenes):
            text = " ".join((scene.popup.text or "").split())
            if not text:
                continue
            start, end = bounds[position]
            key = next(
                (request.timing.sentence(i) for i in scene.sentence_ids
                 if request.timing.sentence(i) is not None),
                None,
            )
            key_start = key.start_s if key is not None else start
            window = popup_window(
                key_start, start, end, lead_s=look.lead_s, max_show_s=look.max_show_s,
                end_margin_s=look.end_margin_s, min_show_s=look.min_show_s,
            )
            if window is None:
                warnings.append(
                    f"Scene {position + 1} is too short for its popup '{text}'; it was left out."
                )
                continue
            popups.append(
                TimelinePopup(
                    scene=position,
                    text=text,
                    style=scene.popup.style,
                    position=scene.popup.position,
                    start_s=window[0],
                    end_s=window[1],
                    anim=popup_anim_for(scene.popup.style),  # type: ignore[arg-type]
                )
            )

    rules = rules_for(request.language)
    style = caption_style_for(request.language, request.caption_style_name)
    cues = chunk_cues(
        request.timing.sentences,
        max_chars_per_line=rules.max_chars_per_line,
        max_lines=rules.max_lines,
        pause_s=pause_break_s(),
        space_dependent=rules.space_dependent,
    )

    music = MusicTrack(
        gain_db=config.music_gain_db,
        fade_in_s=config.music_fade_in_s,
        fade_out_s=config.music_fade_out_s,
    )
    if request.music_choice is None:
        track = pick_track(request.music_folder, request.project_id)
        if track is None and request.music_folder:
            warnings.append(
                f"No music: the channel's music folder {request.music_folder} has no audio "
                "files (or does not exist)."
            )
        elif track is None:
            warnings.append("No music: the channel has no music folder set.")
    elif request.music_choice == "":
        track = None
    else:
        track = resolve_track(request.music_folder, request.music_choice)
        if track is None:
            warnings.append(
                f"The music track '{request.music_choice}' was not found; the video has no music."
            )
    if track is not None:
        music.path = str(track)
        music.license_ok = has_licence(track)
        if not music.license_ok:
            warnings.append(
                f"The music track {track.name} has no licence file next to it "
                f"({track.name}.license.txt or a LICENSE file in the folder)."
            )

    timeline = Timeline(
        project_id=request.project_id,
        format=request.fmt,  # type: ignore[arg-type]
        aspect=request.aspect,  # type: ignore[arg-type]
        fps=request.fps,
        width=request.width,
        height=request.height,
        duration_s=timeline_scenes[-1].end_s,
        voice=VoiceTrack(path=request.voice_path, start_s=0.0),
        music=music,
        scenes=timeline_scenes,
        popups=popups,
        captions=TimelineCaptions(enabled=request.captions_enabled, style=style, cues=cues),
        presets=list(request.presets),
        proxy=ProxySize(width=request.proxy[0], height=request.proxy[1]),
    )
    return timeline, warnings

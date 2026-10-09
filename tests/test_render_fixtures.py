"""Helpers shared by the ``test_render_*`` modules: a complete 3-scene mock project on disk
(storyboard, voice timing, silent voice.wav, placeholder pictures, a licensed music track)
and a stage context for it. Media is tiny (1K pictures, a few seconds) so the real FFmpeg
renders stay quick on a laptop. One sanity test keeps the fixture honest."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cashcow_studio.config import Settings
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.project import Project, ProjectSource
from cashcow_studio.models.storyboard import (
    Scene,
    SceneImage,
    SceneMotion,
    ScenePopup,
    SceneTransition,
    StoryboardDoc,
)
from cashcow_studio.pipeline.stages.base import StageContext
from cashcow_studio.pipeline.stages.storyboard import MOTION_RECTS
from cashcow_studio.providers.image.base import ImageRequest
from cashcow_studio.providers.image.mock import MockImageProvider
from cashcow_studio.providers.voice.mock import write_silence_wav

TMP_ROOT = Path("F:/cfs_tmp/edit")
SENTENCES: list[tuple[str, str]] = [
    ("s-01-01-01", "The diner opened at five every morning without fail, and the coffee was hot."),
    ("s-01-01-02", "Nobody noticed the stranger in the corner booth that day."),
    ("s-01-01-03", "When the bill came he left a note folded under the plate."),
]
SENTENCE_S = 1.6
GAP_S = 0.1
POPUPS: list[str | None] = ["Five every morning", None, "A folded note"]
POSITIONS = ["top-left", "bottom-left", "bottom-right"]
MOTIONS = ["zoom_in", "pan_right", "zoom_out"]
TRANSITIONS: list[tuple[str, float]] = [("fade", 0.6), ("wipeleft", 0.5), ("fadeblack", 0.8)]


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="FFmpeg is not installed")


def make_render_tmp_fixture():
    """The ``render_tmp`` fixture: a short temporary folder (Windows path limit), removed
    afterwards. Each test module binds it with ``render_tmp = make_render_tmp_fixture()``."""

    @pytest.fixture(name="render_tmp")
    def render_tmp(tmp_path: Path) -> Iterator[Path]:
        base = TMP_ROOT
        try:
            base.mkdir(parents=True, exist_ok=True)
            folder = base / f"t-{os.getpid()}-{uuid.uuid4().hex[:6]}"
            folder.mkdir()
        except OSError:
            folder = tmp_path / "render"
            folder.mkdir()
        try:
            yield folder
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    return render_tmp


render_tmp = make_render_tmp_fixture()


def make_timing(
    sentences: list[tuple[str, str]] | None = None,
    *,
    sentence_s: float = SENTENCE_S,
    gap_s: float = GAP_S,
) -> dict[str, Any]:
    """A ``timing.json`` document with evenly spaced words (the mock voice's shape)."""
    rows: list[dict[str, Any]] = []
    clock = 0.0
    for sid, text in sentences or SENTENCES:
        words = text.split()
        slot = sentence_s / len(words)
        rows.append(
            {
                "id": sid,
                "text": text,
                "start_s": round(clock, 3),
                "end_s": round(clock + sentence_s, 3),
                "words": [
                    {
                        "text": word,
                        "start_s": round(clock + i * slot, 3),
                        "end_s": round(clock + (i + 1) * slot, 3),
                        "confidence": 0.5,
                    }
                    for i, word in enumerate(words)
                ],
            }
        )
        clock += sentence_s + gap_s
    return {
        "sample_rate": 48000,
        "duration_s": round(clock - gap_s, 3),
        "source": "estimated",
        "sentences": rows,
    }


def make_storyboard(aspect: str = "16:9", popups: bool = True) -> StoryboardDoc:
    scenes: list[Scene] = []
    for index, (sid, text) in enumerate(SENTENCES):
        start, end = MOTION_RECTS[MOTIONS[index]]
        scenes.append(
            Scene(
                index=index,
                sentence_ids=[sid],
                narration=text,
                image_prompt=f"Picture {index + 1}",
                popup=ScenePopup(
                    text=POPUPS[index] if popups else None,
                    style="Rounded box, brand colour, slide-in from left",
                    position=POSITIONS[index],  # type: ignore[arg-type]
                ),
                motion=SceneMotion(
                    preset=MOTIONS[index],  # type: ignore[arg-type]
                    start_rect=list(start),
                    end_rect=list(end),
                ),
                transition_out=SceneTransition(
                    type=TRANSITIONS[index][0], duration_s=TRANSITIONS[index][1]
                ),
                image=SceneImage(path=f"06_images/scene_{index + 1:02d}.png", status="generated"),
            )
        )
    return StoryboardDoc(
        project_id="p-edit-test",
        format="long" if aspect == "16:9" else "shorts",
        aspect=aspect,  # type: ignore[arg-type]
        style_guide="Cinematic photo-realism",
        generated_at=datetime.now(UTC),
        model="mock",
        scenes=scenes,
    )


def write_music_track(music_dir: Path, name: str = "calm.wav", licence: bool = True) -> Path:
    """A two-second sine tone (needs FFmpeg); the licence sidecar is optional."""
    music_dir.mkdir(parents=True, exist_ok=True)
    track = music_dir / name
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-ac", "2", str(track),
        ],
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if licence:
        (music_dir / f"{name}.license.txt").write_text("CC0 1.0", encoding="utf-8")
    return track


@dataclass
class ProjectFiles:
    root: Path
    folder: Path
    app_dir: Path
    music_dir: Path | None
    storyboard: StoryboardDoc
    timing: dict[str, Any]

    @property
    def edit_dir(self) -> Path:
        return self.folder / "07_edit"


def write_project(
    root: Path,
    *,
    aspect: str = "16:9",
    music: bool = True,
    licence: bool = True,
    popups: bool = True,
    images: bool = True,
) -> ProjectFiles:
    """The files the edit stage reads, as the earlier stages (mocked) would leave them."""
    folder = root / "proj"
    for name in ("04_storyboard", "05_voice", "06_images", "07_edit", "08_export"):
        (folder / name).mkdir(parents=True, exist_ok=True)
    app_dir = root / "app"
    app_dir.mkdir(exist_ok=True)
    storyboard = make_storyboard(aspect, popups=popups)
    (folder / "04_storyboard" / "storyboard.json").write_text(
        storyboard.model_dump_json(indent=2), encoding="utf-8"
    )
    timing = make_timing()
    (folder / "05_voice" / "timing.json").write_text(json.dumps(timing, indent=2), "utf-8")
    write_silence_wav(folder / "05_voice" / "voice.wav", timing["duration_s"], 48000)
    if images:
        provider = MockImageProvider()
        for index, (_sid, text) in enumerate(SENTENCES):
            provider.generate(
                ImageRequest(
                    prompt=f"Scene {index + 1}: {text}",
                    output_path=folder / "06_images" / f"scene_{index + 1:02d}.png",
                    aspect=aspect,
                    size="1K",
                    scene_id=str(index + 1),
                )
            )
    music_dir: Path | None = None
    if music:
        music_dir = root / "music"
        if ffmpeg_available():
            write_music_track(music_dir, licence=licence)
        else:
            music_dir.mkdir(exist_ok=True)
            write_silence_wav(music_dir / "calm.wav", 2.0, 48000)
            if licence:
                (music_dir / "calm.wav.license.txt").write_text("CC0", encoding="utf-8")
    return ProjectFiles(root, folder, app_dir, music_dir, storyboard, timing)


def make_ctx(
    files: ProjectFiles,
    *,
    render: dict[str, Any] | None = None,
    captions: dict[str, Any] | None = None,
    language: str = "English",
    progress: Callable[[str, float | None], None] | None = None,
    edits: dict[str, Any] | None = None,
) -> StageContext:
    settings = Settings(
        app_data_dir=files.app_dir,
        projects_dir=files.root,
        exports_dir=files.root / "exports",
        render=render or {"default_presets": ["720p"], "x264_preset": "ultrafast"},
        captions=captions or {"enabled": True, "style": "bold-white"},
    )
    channel = Channel.model_validate(
        {
            "slug": "kind-ledger",
            "channel": {
                "name": "Kind Ledger",
                "language": language,
                "music_folder": str(files.music_dir) if files.music_dir else "",
                "brand_colors": ["#1F3864", "#FFC000"],
            },
        }
    )
    now = datetime.now(UTC)
    project = Project(
        id="p-edit-test",
        channel_slug="kind-ledger",
        topic_slug="diner",
        title="The Diner Debt",
        format=files.storyboard.format,
        language=language,  # type: ignore[arg-type]
        created_at=now,
        updated_at=now,
        folder=str(files.folder),
        source=ProjectSource(kind="own_topic", topic_text="diner"),
    )
    return StageContext(
        project=project,
        channel=channel,
        settings=settings,
        folder=files.folder,
        progress=progress or (lambda message, pct=None: None),
        edits=dict(edits or {}),
    )


def test_fixture_builds_a_complete_mock_project(render_tmp: Path) -> None:
    files = write_project(render_tmp)
    assert (files.folder / "04_storyboard" / "storyboard.json").is_file()
    assert (files.folder / "05_voice" / "timing.json").is_file()
    assert (files.folder / "05_voice" / "voice.wav").stat().st_size > 1000
    assert sorted(p.name for p in (files.folder / "06_images").iterdir()) == [
        "scene_01.png", "scene_02.png", "scene_03.png",
    ]
    assert files.timing["duration_s"] == pytest.approx(3 * SENTENCE_S + 2 * GAP_S)
    assert files.music_dir is not None and (files.music_dir / "calm.wav").is_file()
    ctx = make_ctx(files)
    assert ctx.settings.render.default_presets == ["720p"]
    assert ctx.channel.channel.music_folder == str(files.music_dir)

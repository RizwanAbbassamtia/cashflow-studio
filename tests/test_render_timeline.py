"""Timeline builder maths (docs/M3-M4-CONTRACT.md section 3): scene bounds from the voice
clock, transition clipping, popup timing, rectangle mapping, the music pick with licence
detection, and the Timeline model itself. No FFmpeg needed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cashcow_studio.models.timeline import EditApproveEdits, Timeline
from cashcow_studio.providers.voice.mock import write_silence_wav
from cashcow_studio.render import music
from cashcow_studio.render.geometry import cover_region, rect_in_pixels, rect_to_canvas
from cashcow_studio.render.presets import config_from, load_render_config
from cashcow_studio.render.timeline_builder import (
    BuildRequest,
    TimelineBuildError,
    build_timeline,
    clip_transitions,
    parse_timing,
    popup_anim_for,
    popup_window,
    read_timing,
    resolve_scene_images,
    scene_bounds,
)
from test_render_fixtures import (
    GAP_S,
    SENTENCE_S,
    SENTENCES,
    make_render_tmp_fixture,
    make_storyboard,
    make_timing,
    write_project,
)

render_tmp = make_render_tmp_fixture()


def timing():
    return parse_timing(make_timing())


# Scene bounds and transitions -----------------------------------------------------------------


def test_scene_bounds_follow_the_voice_clock() -> None:
    bounds = scene_bounds(make_storyboard().scenes, timing(), tail_s=0.8)
    second_start = SENTENCE_S + GAP_S
    third_start = 2 * (SENTENCE_S + GAP_S)
    voice_end = 3 * SENTENCE_S + 2 * GAP_S
    assert bounds == [
        (0.0, round(second_start, 3)),
        (round(second_start, 3), round(third_start, 3)),
        (round(third_start, 3), round(voice_end + 0.8, 3)),
    ]
    # Scenes are contiguous: a scene runs until the next one starts (no black gaps).
    for (_s, end), (start, _e) in zip(bounds, bounds[1:], strict=False):
        assert end == start


def test_scene_without_timing_keeps_its_estimated_length() -> None:
    doc = make_storyboard()
    doc.scenes[1].sentence_ids = ["s-missing"]
    doc.scenes[1].est_duration_s = 2.5
    bounds = scene_bounds(doc.scenes, timing(), tail_s=0.0)
    # The untimed scene starts where the previous scene's last sentence ends and keeps its
    # estimated length; the next timed scene cannot start before it ends.
    assert bounds[1] == (round(SENTENCE_S, 3), round(SENTENCE_S + 2.5, 3))
    assert bounds[2][0] == bounds[1][1]
    assert bounds[2][1] >= timing().duration_s


def test_transitions_are_clipped_to_a_quarter_of_the_shorter_scene() -> None:
    bounds = [(0.0, 2.0), (2.0, 10.0), (10.0, 11.0)]
    clipped = clip_transitions(bounds, [1.0, 0.6, 0.8], max_share=0.25, min_s=0.07)
    assert clipped == [0.5, 0.25, 0.25]
    # Shorter than min_s becomes a plain cut; a wanted duration under the limit is kept.
    assert clip_transitions([(0.0, 0.2), (0.2, 5.0)], [0.6, 0.3], min_s=0.07) == [0.0, 0.3]
    assert clip_transitions(bounds, [0.1, 0.1, 0.1]) == [0.1, 0.1, 0.1]


def test_popup_window_rules() -> None:
    assert popup_window(1.7, 1.7, 10.0) == (2.0, 6.0)  # key + 0.3, at most 4 s
    assert popup_window(1.7, 1.7, 3.4) == (2.0, 3.2)  # out 0.2 s before the scene ends
    assert popup_window(0.0, 0.0, 0.9) == (0.1, 0.7)  # pulled earlier to show min 0.6 s
    assert popup_window(0.0, 0.0, 0.5) is None  # too short to show at all
    assert popup_anim_for("Rounded box, slide-in from left") == "slide_left"
    assert popup_anim_for("soft fade") == "fade"
    assert popup_anim_for("pop in") == "pop"
    assert popup_anim_for("") == "slide_left"


# Rectangles ----------------------------------------------------------------------------------


def test_rect_in_pixels_keeps_the_frame_aspect_inside_the_image() -> None:
    assert rect_in_pixels([0, 0, 1, 1], 1024, 576, 1920, 1080) == [0.0, 0.0, 1024.0, 576.0]
    zoomed = rect_in_pixels([0.1, 0.1, 0.8, 0.8], 1024, 576, 1920, 1080)
    assert zoomed == [102.4, 57.6, 819.2, 460.8]
    assert zoomed[2] / zoomed[3] == pytest.approx(16 / 9, abs=1e-3)
    # pan_left's 0.85 x 1.0 fraction is squeezed to 16:9 around the same centre.
    pan = rect_in_pixels([0.15, 0.0, 0.85, 1.0], 1024, 576, 1920, 1080)
    assert pan[2] / pan[3] == pytest.approx(16 / 9, abs=1e-3)
    assert pan[0] + pan[2] / 2 == pytest.approx((0.15 + 0.425) * 1024, abs=0.1)
    assert pan[0] >= 0 and pan[0] + pan[2] <= 1024 and pan[1] >= 0 and pan[1] + pan[3] <= 576
    # A square picture in a 16:9 frame: only the centred band covers the frame.
    assert cover_region(1024, 1024, 1920, 1080) == (0.0, 224.0, 1024.0, 576.0)
    assert rect_in_pixels([0, 0, 1, 1], 1024, 1024, 1920, 1080) == [0.0, 224.0, 1024.0, 576.0]
    # Portrait frame, landscape picture: a centred column.
    region = cover_region(1024, 576, 1080, 1920)
    assert region[3] == 576.0 and region[2] == pytest.approx(324.0, abs=0.1)
    # Canvas mapping scales the cover region to the canvas and clamps.
    assert rect_to_canvas([0, 224, 1024, 576], (0, 224, 1024, 576), 854, 480) == (0, 0, 854, 480)


# Reading timing.json -----------------------------------------------------------------------


def test_read_timing_errors_are_plain_english(render_tmp: Path) -> None:
    with pytest.raises(TimelineBuildError, match="voice step has not written timing.json"):
        read_timing(render_tmp / "timing.json")
    (render_tmp / "timing.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(TimelineBuildError, match="could not be read"):
        read_timing(render_tmp / "timing.json")
    (render_tmp / "timing.json").write_text(json.dumps({"sentences": []}), encoding="utf-8")
    with pytest.raises(TimelineBuildError, match="no sentences"):
        read_timing(render_tmp / "timing.json")
    (render_tmp / "timing.json").write_text(json.dumps(make_timing()), encoding="utf-8")
    parsed = read_timing(render_tmp / "timing.json")
    assert len(parsed.sentences) == 3 and parsed.sentence(SENTENCES[1][0]) is not None
    assert parsed.sentences[0].words[0].text == "The"


# Music ----------------------------------------------------------------------------------------


def test_music_pick_is_deterministic_and_licences_are_detected(render_tmp: Path) -> None:
    folder = render_tmp / "music"
    folder.mkdir()
    for name in ("a.wav", "b.wav", "c.wav"):
        write_silence_wav(folder / name, 0.2, 8000)
    (folder / "notes.txt").write_text("not audio", encoding="utf-8")
    assert [t.name for t in music.list_tracks(folder)] == ["a.wav", "b.wav", "c.wav"]
    first = music.pick_track(folder, "project-1")
    assert first is not None and first == music.pick_track(folder, "project-1")
    picks = {music.pick_track(folder, f"project-{i}").name for i in range(30)}  # type: ignore[union-attr]
    assert len(picks) > 1  # different projects get different tracks
    assert music.pick_track(folder / "missing", "x") is None
    assert music.pick_track(None, "x") is None

    assert not music.has_licence(folder / "a.wav")
    (folder / "a.wav.license.txt").write_text("CC-BY", encoding="utf-8")
    assert music.has_licence(folder / "a.wav") and not music.has_licence(folder / "b.wav")
    (folder / "LICENSE.md").write_text("all tracks CC0", encoding="utf-8")
    assert music.has_licence(folder / "b.wav")
    infos = music.track_infos(folder)
    assert [(i.name, i.license_ok) for i in infos] == [("a", True), ("b", True), ("c", True)]
    assert music.resolve_track(folder, "b") == folder / "b.wav"
    assert music.resolve_track(folder, "b.wav") == folder / "b.wav"
    assert music.resolve_track(folder, str(folder / "c.wav")) == folder / "c.wav"
    assert music.resolve_track(folder, "zzz") is None


# The whole builder --------------------------------------------------------------------------


def build(files, **overrides):
    storyboard = files.storyboard
    sources = resolve_scene_images(files.folder, storyboard)
    config = load_render_config()
    aspect = storyboard.aspect
    preset = config.presets["1080p"]
    width, height = preset.size(aspect)
    request = BuildRequest(
        project_id="p-edit-test",
        fmt=storyboard.format,
        aspect=aspect,
        language="English",
        folder=files.folder,
        storyboard=storyboard,
        timing=parse_timing(files.timing),
        sources=sources,
        width=width,
        height=height,
        proxy=config.proxy.size(aspect),
        presets=["1080p"],
        fps=config.fps,
        music_folder=str(files.music_dir) if files.music_dir else None,
        brand_colours=["#1F3864"],
        config=config,
    )
    for key, value in overrides.items():
        setattr(request, key, value)
    return build_timeline(request)


def test_build_timeline_from_the_mock_project(render_tmp: Path) -> None:
    files = write_project(render_tmp)
    timeline, warnings = build(files)
    assert warnings == []
    assert timeline.project_id == "p-edit-test" and timeline.aspect == "16:9"
    assert (timeline.width, timeline.height) == (1920, 1080)
    assert (timeline.proxy.width, timeline.proxy.height) == (854, 480)
    assert timeline.fps == 30 and timeline.presets == ["1080p"]
    assert [s.image for s in timeline.scenes] == [
        "06_images/scene_01.png", "06_images/scene_02.png", "06_images/scene_03.png",
    ]
    assert timeline.scenes[0].start_s == 0.0
    assert timeline.duration_s == timeline.scenes[-1].end_s
    assert timeline.duration_s == pytest.approx(files.timing["duration_s"] + 0.8, abs=1e-3)
    for scene in timeline.scenes:
        for rect in (scene.motion.start_rect, scene.motion.end_rect):
            assert rect[2] / rect[3] == pytest.approx(16 / 9, abs=1e-2)
            assert rect[0] >= 0 and rect[1] >= 0 and rect[0] + rect[2] <= 1024.01
        assert scene.transition_out.duration_s <= 0.25 * (scene.end_s - scene.start_s) + 1e-6
    assert [s.transition_out.type for s in timeline.scenes] == ["fade", "wipeleft", "fadeblack"]
    assert timeline.transition_types == ["fade", "wipeleft", "fadeblack"]
    # Two scenes carry a popup; the key sentence + 0.3 s rule holds.
    assert [(p.scene, p.text) for p in timeline.popups] == [
        (0, "Five every morning"), (2, "A folded note"),
    ]
    assert timeline.popups[0].start_s == pytest.approx(0.3)
    assert timeline.popups[1].start_s == pytest.approx(timeline.scenes[2].start_s + 0.3)
    assert timeline.popups[1].end_s <= timeline.scenes[2].end_s - 0.2 + 1e-6
    assert all(p.anim == "slide_left" for p in timeline.popups)
    # Captions: one cue per sentence here (short sentences), English rules.
    assert timeline.captions.enabled and len(timeline.captions.cues) == 3
    assert timeline.captions.style.font == "Noto Sans" and not timeline.captions.style.rtl
    assert timeline.voice.path == "05_voice/voice.wav"
    assert timeline.music.path is not None and timeline.music.path.endswith("calm.wav")
    assert timeline.music.license_ok is True and timeline.music.duck_db == -12
    assert timeline.music.duck_attack_s == 0.3 and timeline.music.duck_release_s == 0.8
    # Round trip through JSON.
    again = Timeline.model_validate_json(timeline.model_dump_json())
    assert again == timeline


def test_build_timeline_for_shorts_and_without_music(render_tmp: Path) -> None:
    files = write_project(render_tmp, aspect="9:16", music=False)
    timeline, warnings = build(files, width=1080, height=1920, proxy=(480, 854))
    assert timeline.aspect == "9:16" and timeline.format == "shorts"
    assert (timeline.proxy.width, timeline.proxy.height) == (480, 854)
    assert timeline.music.path is None and not timeline.music.license_ok
    assert any("No music" in w for w in warnings)
    for scene in timeline.scenes:
        rect = scene.motion.start_rect
        assert rect[2] / rect[3] == pytest.approx(9 / 16, abs=1e-2)


def test_unlicensed_music_and_missing_choices_warn(render_tmp: Path) -> None:
    files = write_project(render_tmp, licence=False)
    timeline, warnings = build(files)
    assert timeline.music.path and timeline.music.license_ok is False
    assert any("no licence file" in w for w in warnings)
    timeline, warnings = build(files, music_choice="")
    assert timeline.music.path is None and warnings == []
    timeline, warnings = build(files, music_choice="does-not-exist")
    assert timeline.music.path is None and any("was not found" in w for w in warnings)
    timeline, _ = build(files, popups_enabled=False)
    assert timeline.popups == []


def test_missing_pictures_are_reported_by_scene(render_tmp: Path) -> None:
    files = write_project(render_tmp, images=False)
    with pytest.raises(TimelineBuildError, match=r"No picture for scene\(s\) 1, 2, 3"):
        resolve_scene_images(files.folder, files.storyboard)


# The model ---------------------------------------------------------------------------------


def test_timeline_model_rejects_out_of_order_scenes(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    timeline, _ = build(files)
    data = timeline.model_dump(mode="json")
    data["scenes"][1]["start_s"] = 0.5  # overlaps scene 1
    with pytest.raises(ValidationError, match="before the previous scene ends"):
        Timeline.model_validate(data)
    data = timeline.model_dump(mode="json")
    data["scenes"][2]["index"] = 7
    with pytest.raises(ValidationError, match="numbered 0, 1, 2"):
        Timeline.model_validate(data)
    data = timeline.model_dump(mode="json")
    data["scenes"][0]["motion"]["start_rect"] = [0, 0, 0, 10]
    with pytest.raises(ValidationError, match="positive width and height"):
        Timeline.model_validate(data)


def test_edit_approve_edits_shapes() -> None:
    assert EditApproveEdits().is_empty
    edits = EditApproveEdits.model_validate({"music_path": ""})
    assert edits.changes_music and not edits.is_empty
    assert not EditApproveEdits.model_validate({"presets": ["720p"]}).changes_music
    assert EditApproveEdits.model_validate({"captions_enabled": False}).captions_enabled is False


def test_render_config_defaults_and_overrides() -> None:
    config = config_from({})
    assert config.fps == 30 and config.tail_s == 0.8 and config.x264_preset == "medium"
    assert config.presets["1080p"].size("16:9") == (1920, 1080)
    assert config.presets["1080p"].size("9:16") == (1080, 1920)
    assert config.presets["2160p"].requires_enable_4k
    assert config.available_presets(False) == ["720p", "1080p"]
    assert config.available_presets(True) == ["720p", "1080p", "2160p"]
    assert config.proxy.x264_preset == "ultrafast" and config.proxy.crf == 30
    assert config.x264_preset_for(config.proxy, "slow") == "ultrafast"
    assert config.x264_preset_for(config.presets["720p"], "slow") == "slow"
    assert config.x264_preset_for(config.presets["720p"], "bogus") == "medium"
    custom = config_from({"fps": 25, "x264": {"preset": "fast", "crf": 18},
                          "presets": {"720p": {"canvas_scale": 1.0}}})
    assert custom.fps == 25 and custom.x264_preset == "fast" and custom.crf == 18
    assert custom.presets["720p"].canvas("16:9") == (1280, 720)
    assert config.presets["720p"].canvas("16:9") == (2560, 1440)
    with pytest.raises(ValueError, match="not a render size"):
        config.preset("4k")

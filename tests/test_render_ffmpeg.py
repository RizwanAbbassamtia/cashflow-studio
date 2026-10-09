"""The FFmpeg side: the filter graph as text (no FFmpeg needed), then real renders of the
3-scene mock project at proxy and 720p, a 9:16 variant, the duration check with ffprobe,
cancellation, and the edit stage's re-run and edit paths. Media stays tiny."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from cashcow_studio.models.timeline import Timeline
from cashcow_studio.pipeline.stages.base import StageError
from cashcow_studio.pipeline.stages.edit import (
    EditStage,
    load_review_payload,
    read_state,
)
from cashcow_studio.render import ffmpeg as ff
from cashcow_studio.render.presets import load_render_config
from test_render_fixtures import (
    SENTENCES,
    make_ctx,
    make_render_tmp_fixture,
    requires_ffmpeg,
    write_project,
)

render_tmp = make_render_tmp_fixture()


def run(coro):
    return asyncio.run(coro)


# Pure helpers ----------------------------------------------------------------------------------


def test_expression_numbers_and_filter_path_escaping() -> None:
    assert ff.fmt(1.7) == "1.7" and ff.fmt(0.43333) == "0.4333" and ff.fmt(854) == "854"
    assert ff.fmt(-170.8) == "-170.8" and ff.fmt(0.0) == "0" and ff.fmt(-0.00001) == "0"
    escaped = ff.escape_filter_path(Path("F:/cfs_tmp/edit/esc test/captions.ass"))
    assert escaped == "'F\\:/cfs_tmp/edit/esc test/captions.ass'"
    assert ff.escape_filter_path("C:\\Users\\punjab pc\\x.ass") == "'C\\:/Users/punjab pc/x.ass'"
    # Nothing can be escaped inside a quoted graph token: an apostrophe closes the quote,
    # gives the option parser \' and opens the quote again.
    assert ff.escape_filter_path("a'b,c") == "'a'\\\\\\''b\\,c'"


def test_progress_lines_and_benign_log_lines() -> None:
    assert ff.parse_progress_line("out_time_us=1500000\n") == ("out_time_us", "1500000")
    assert ff.parse_progress_line("progress=end") == ("progress", "end")
    assert ff.parse_progress_line("no equals here") is None
    assert ff.is_benign_log_line("[Parsed_subtitles_3 @ 0x1] Error opening memory font 'OFL.txt'")
    assert ff.is_benign_log_line("[aist#5:0/pcm_s16le @ 0x1] Guessed Channel Layout: mono")
    assert not ff.is_benign_log_line("[libx264 @ 0x1] something went wrong")


def test_filter_graph_shape_for_the_mock_project(render_tmp: Path) -> None:
    files = write_project(render_tmp)
    ctx = make_ctx(files)
    config = load_render_config()
    from cashcow_studio.render.timeline_builder import (
        BuildRequest,
        build_timeline,
        parse_timing,
        resolve_scene_images,
    )

    request = BuildRequest(
        project_id="p-edit-test", fmt="long", aspect="16:9", language="English",
        folder=files.folder, storyboard=files.storyboard, timing=parse_timing(files.timing),
        sources=resolve_scene_images(files.folder, files.storyboard), width=1920, height=1080,
        proxy=(854, 480), presets=["720p"], music_folder=str(files.music_dir), config=config,
    )
    timeline, _ = build_timeline(request)
    ass = files.edit_dir / "captions.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")
    popups = [
        ff.PopupAsset(popup, files.edit_dir / f"p{i}.png", 300, 100, 60, 60)
        for i, popup in enumerate(timeline.popups)
    ]
    plan = ff.build_plan(
        timeline, config.presets["720p"], folder=files.folder, config=config, popups=popups,
        ass_path=ass, captions=True, x264_preset=ctx.settings.render.x264_preset,
    )
    assert (plan.width, plan.height) == (1280, 720) and plan.canvas == (2560, 1440)
    assert plan.x264_preset == "ultrafast" and plan.crf == 20
    assert plan.total_s == pytest.approx(timeline.duration_s, abs=1 / 30)
    # 3 pictures + 2 popups + voice + music = 7 inputs, music looped.
    assert len(plan.inputs) == 7
    assert plan.inputs[3][:2] == ["-loop", "1"] and plan.inputs[6][:2] == ["-stream_loop", "-1"]
    graph = plan.filter_graph
    assert graph.count("zoompan=") == 3 and graph.count("xfade=") == 2
    assert "xfade=transition=fade:duration=0.4333:offset=1.7[v1]" in graph
    assert "xfade=transition=wipeleft:duration=0.4333:offset=3.4[v2]" in graph
    assert "fade=t=out:st=" in graph and ":color=black[s2]" in graph
    assert graph.count("overlay=") == 2 and "eof_action=pass" in graph
    assert "pow(max(0\\,1-(t-0.3)/0.35)\\,2)" in graph  # slide_left eases in
    assert "subtitles=filename='" in graph and "\\:/" in graph and "fontsdir='" in graph
    assert "sidechaincompress=threshold=0.03:ratio=8:attack=300:release=800" in graph
    assert "amix=inputs=2:duration=first:dropout_transition=0:normalize=0" in graph
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in graph
    assert "apad=whole_dur=" in graph and graph.rstrip().endswith("[amain]anull[aout]")
    assert "null[vout]" in graph
    # Proxy preset: its own speed and size, captions can be left out, no music -> no ducking.
    timeline.music.path = None
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config,
                         ass_path=ass, captions=False, x264_preset="slow")
    assert plan.x264_preset == "ultrafast" and plan.crf == 30
    assert (plan.width, plan.height) == (854, 480) and len(plan.inputs) == 4
    assert "subtitles=" not in plan.filter_graph and "sidechaincompress" not in plan.filter_graph
    output = plan.output_args()
    assert output[:4] == ["-map", "[vout]", "-map", "[aout]"]
    assert "-movflags" in output and "+faststart" in output and "-t" in output


def test_filter_graph_handles_80_scenes_without_a_long_command_line(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    config = load_render_config()
    scenes = []
    clock = 0.0
    for index in range(80):
        scenes.append({
            "index": index, "image": f"06_images/scene_{index % 3 + 1:02d}.png",
            "start_s": round(clock, 3), "end_s": round(clock + 2.0, 3),
            "motion": {"preset": "zoom_in", "start_rect": [0, 0, 1024, 576],
                       "end_rect": [102.4, 57.6, 819.2, 460.8]},
            "transition_out": {"type": "fade", "duration_s": 0.4},
        })
        clock += 2.0
    timeline = Timeline.model_validate({
        "project_id": "p", "format": "long", "aspect": "16:9", "width": 1280, "height": 720,
        "duration_s": clock, "scenes": scenes,
    })
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    assert plan.filter_graph.count("xfade=") == 79 and len(plan.inputs) == 81
    assert len(plan.filter_graph) > 20000  # far beyond a Windows command line; it goes to a file
    assert plan.total_s == pytest.approx(160.0)


# Real renders ------------------------------------------------------------------------------


@requires_ffmpeg
def test_render_proxy_and_720p_with_ffprobe_duration(render_tmp: Path) -> None:
    files = write_project(render_tmp)
    events: list[tuple[str, float | None]] = []
    ctx = make_ctx(files, progress=lambda message, pct=None: events.append((message, pct)))
    result = run(EditStage().run(ctx))

    edit = files.edit_dir
    timeline = Timeline.model_validate_json((edit / "timeline.json").read_text("utf-8"))
    assert timeline.presets == ["720p"]
    for name in ("timeline.json", "captions.ass", "proxy.mp4", "final_720p.mp4", "render.log",
                 "render_state.json", "filters_proxy.txt", "filters_720p.txt"):
        assert (edit / name).is_file(), name
    assert sorted(p.name for p in (edit / "popups" / "ref").iterdir()) == [
        "popup_00.png", "popup_01.png",
    ]
    assert (edit / "popups" / "720p" / "popup_01.png").is_file()
    assert timeline.popups[0].png == "07_edit/popups/ref/popup_00.png"

    for name, size in (("proxy.mp4", (854, 480)), ("final_720p.mp4", (1280, 720))):
        info = ff.probe_media(edit / name)
        assert (info.width, info.height) == size, name
        assert info.has_audio and info.has_video
        assert abs(info.duration_s - timeline.duration_s) < 1.0, name
        assert abs(info.duration_s - timeline.duration_s) < 0.2, name
    assert ff.probe_duration(edit / "proxy.mp4") == pytest.approx(timeline.duration_s, abs=0.2)

    gates = {g["id"]: g for g in result.gate_results}
    assert set(gates) == {"edit.music_license", "edit.duration", "edit.audio_peaks"}
    assert all(g["passed"] for g in gates.values()), gates
    assert "calm.wav" in gates["edit.music_license"]["detail"]
    payload = result.needs_review_payload
    assert payload["stage"] == "edit"
    assert payload["proxy_url"] == "/api/projects/p-edit-test/files/07_edit/proxy.mp4"
    assert payload["proxy_path"] == "07_edit/proxy.mp4"
    renders = {r["preset"]: r for r in payload["renders"]}
    assert renders["proxy"]["status"] == "done" and renders["720p"]["status"] == "done"
    assert renders["720p"]["play_url"].endswith("/files/07_edit/final_720p.mp4")
    assert renders["720p"]["width"] == 1280 and renders["720p"]["size_bytes"] > 1000
    assert renders["1080p"]["status"] == "not_rendered"
    assert payload["summary"]["scene_count"] == 3
    assert payload["summary"]["transitions"] == ["fade", "wipeleft", "fadeblack"]
    assert payload["summary"]["caption_cues"] == 3 and payload["summary"]["popup_count"] == 2
    assert [s["image_url"] for s in payload["scenes"]][0].endswith("06_images/scene_01.png")
    assert payload["scenes"][0]["popup_text"] == "Five every morning"
    assert payload["music_license_ok"] is True and payload["music_path"].endswith("calm.wav")
    assert [t["name"] for t in payload["music_tracks"]] == ["calm"]
    assert payload["captions_enabled"] is True and payload["popups_enabled"] is True
    assert payload["available_presets"] == ["720p", "1080p", "2160p"]
    assert payload["enable_4k"] is False and payload["warnings"] == []
    assert payload["render_log_path"] == "07_edit/render.log"
    assert payload["timing_source"] == "estimated"
    assert "3 scenes" in result.summary and "final_720p.mp4" in result.summary
    assert [p.name for p in result.outputs][:3] == ["timeline.json", "proxy.mp4", "final_720p.mp4"]

    messages = [m for m, _ in events]
    assert any(m.startswith("Rendering proxy") for m in messages)
    assert any(m.startswith("Rendering 720p") and m.endswith("100%") for m in messages)
    pcts = [p for m, p in events if m.startswith("Rendering 720p") and p is not None]
    assert pcts == sorted(pcts) and pcts[-1] == 100.0
    log = (edit / "render.log").read_text(encoding="utf-8")
    assert "render proxy -> proxy.mp4" in log and "render 720p -> final_720p.mp4" in log
    assert "exit code 0" in log and "OFL.txt" not in log

    # The payload can be rebuilt from the files alone (server restart).
    rebuilt = load_review_payload(files.folder, "p-edit-test", str(files.music_dir))
    assert rebuilt["proxy_url"] == payload["proxy_url"]
    assert {r["preset"]: r["status"] for r in rebuilt["renders"]} == {
        r["preset"]: r["status"] for r in payload["renders"]
    }


@requires_ffmpeg
def test_render_9_16_variant(render_tmp: Path) -> None:
    files = write_project(render_tmp, aspect="9:16", music=False)
    ctx = make_ctx(files, render={"default_presets": ["720p"], "x264_preset": "ultrafast"})
    result = run(EditStage().run(ctx))
    edit = files.edit_dir
    timeline = Timeline.model_validate_json((edit / "timeline.json").read_text("utf-8"))
    assert timeline.aspect == "9:16" and (timeline.width, timeline.height) == (720, 1280)
    assert (timeline.proxy.width, timeline.proxy.height) == (480, 854)
    proxy = ff.probe_media(edit / "proxy.mp4")
    final = ff.probe_media(edit / "final_720p.mp4")
    assert (proxy.width, proxy.height) == (480, 854)
    assert (final.width, final.height) == (720, 1280)
    assert abs(final.duration_s - timeline.duration_s) < 1.0
    gates = {g["id"]: g for g in result.gate_results}
    assert gates["edit.duration"]["passed"] and gates["edit.music_license"]["passed"]
    assert "No background music" in gates["edit.music_license"]["detail"]
    assert any("No music" in w for w in result.needs_review_payload["warnings"])


@requires_ffmpeg
def test_second_run_reuses_the_timeline_and_edits_rerender(render_tmp: Path) -> None:
    files = write_project(render_tmp)
    ctx = make_ctx(files)
    stage = EditStage()
    first = run(stage.run(ctx))
    edit = files.edit_dir
    before = (edit / "timeline.json").read_text("utf-8")
    proxy_mtime = (edit / "proxy.mp4").stat().st_mtime

    # Nothing changed: the timeline and the renders are kept.
    second = run(stage.run(make_ctx(files)))
    assert "nothing new" in second.summary
    assert (edit / "timeline.json").read_text("utf-8") == before
    assert (edit / "proxy.mp4").stat().st_mtime == proxy_mtime
    assert second.needs_review_payload["renders"][0]["status"] == "done"

    # Approval edits: captions off and no music -> the timeline changes and renders again.
    time.sleep(0.05)
    edited = run(stage.apply_edits(make_ctx(files, edits={"captions_enabled": False,
                                                          "music_path": "", "presets": []})))
    assert edited is not None
    timeline = Timeline.model_validate_json((edit / "timeline.json").read_text("utf-8"))
    assert timeline.captions.enabled is False and timeline.music.path is None
    assert timeline.presets == []
    assert (edit / "proxy.mp4").stat().st_mtime > proxy_mtime
    assert "subtitles=" not in (edit / "filters_proxy.txt").read_text("utf-8")
    assert "sidechaincompress" not in (edit / "filters_proxy.txt").read_text("utf-8")
    payload = edited.needs_review_payload
    assert payload["captions_enabled"] is False and payload["music_path"] is None
    assert {r["preset"]: r["status"] for r in payload["renders"]}["720p"] == "done"  # kept
    assert "No background music" in {g["id"]: g for g in edited.gate_results}[
        "edit.music_license"
    ]["detail"]

    # Popups off is remembered in render_state.json; unknown presets are refused plainly.
    off = run(stage.apply_edits(make_ctx(files, edits={"popups_enabled": False})))
    assert off is not None and off.needs_review_payload["popups_enabled"] is False
    assert read_state(edit)["popups_enabled"] is False
    assert "overlay=" not in (edit / "filters_proxy.txt").read_text("utf-8")
    with pytest.raises(StageError, match="not a render size"):
        run(stage.apply_edits(make_ctx(files, edits={"presets": ["4k"]})))
    with pytest.raises(StageError, match="was not found"):
        run(stage.apply_edits(make_ctx(files, edits={"music_path": "nope.mp3"})))
    assert first.gate_results and len(first.needs_review_payload["scenes"]) == 3


@requires_ffmpeg
def test_cancel_flag_stops_ffmpeg(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    config = load_render_config()
    scenes = []
    for index in range(3):
        scenes.append({
            "index": index, "image": f"06_images/scene_{index + 1:02d}.png",
            "start_s": index * 40.0, "end_s": (index + 1) * 40.0,
            "motion": {"preset": "zoom_in", "start_rect": [0, 0, 1024, 576],
                       "end_rect": [102.4, 57.6, 819.2, 460.8]},
            "transition_out": {"type": "fade", "duration_s": 0.5},
        })
    timeline = Timeline.model_validate({
        "project_id": "p", "format": "long", "aspect": "16:9", "width": 854, "height": 480,
        "duration_s": 120.0, "scenes": scenes,
    })
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    cancel = threading.Event()
    seen: list[float] = []

    def progress(_message: str, pct: float) -> None:
        seen.append(pct)
        if len(seen) >= 2:
            cancel.set()

    started = time.monotonic()
    with pytest.raises(ff.RenderCancelled):
        ff.run_render(
            plan, files.edit_dir / "long.mp4", work_dir=files.edit_dir,
            log_path=files.edit_dir / "render.log", progress=progress, cancel=cancel,
        )
    assert time.monotonic() - started < 30
    assert not (files.edit_dir / "long.mp4").exists()
    assert seen and max(seen) < 100


@requires_ffmpeg
def test_render_failures_read_plainly(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    config = load_render_config()
    timeline = Timeline.model_validate({
        "project_id": "p", "format": "long", "aspect": "16:9", "width": 854, "height": 480,
        "duration_s": 4.0,
        "scenes": [{"index": 0, "image": "06_images/scene_01.png", "start_s": 0, "end_s": 2.0,
                    "motion": {"preset": "hold", "start_rect": [0, 0, 1024, 576],
                               "end_rect": [0, 0, 1024, 576]},
                    "transition_out": {"type": "no-such-transition", "duration_s": 0.3}},
                   {"index": 1, "image": "06_images/scene_02.png", "start_s": 2.0, "end_s": 4.0,
                    "motion": {"preset": "hold", "start_rect": [0, 0, 1024, 576],
                               "end_rect": [0, 0, 1024, 576]}}],
    })
    plan = ff.build_plan(timeline, config.proxy, folder=files.folder, config=config)
    with pytest.raises(ff.RenderError, match="could not render proxy"):
        ff.run_render(plan, files.edit_dir / "bad.mp4", work_dir=files.edit_dir,
                      log_path=files.edit_dir / "render.log")
    assert "exit code" in (files.edit_dir / "render.log").read_text("utf-8")
    missing = timeline.model_copy(deep=True)
    missing.scenes[0].image = "06_images/none.png"
    with pytest.raises(ff.RenderError, match="picture for scene 1 is missing"):
        ff.build_plan(missing, config.proxy, folder=files.folder, config=config)


@requires_ffmpeg
def test_true_peak_of_silence_is_none_and_filters_are_listed() -> None:
    assert ff.missing_filters() == []
    assert ff.measure_true_peak(Path("F:/does/not/exist.wav")) is None
    assert len(SENTENCES) == 3  # the fixture is what the contract asks: 3 scenes


@requires_ffmpeg
def test_captions_render_from_a_folder_with_an_apostrophe_and_a_space(render_tmp: Path) -> None:
    """A project under C:/Users/O'Neil/... (or a fonts folder with an apostrophe) must not
    break the filter graph: FFmpeg's graph parser cannot escape inside a quoted token."""
    from cashcow_studio.render.captions import write_ass
    from cashcow_studio.render.timeline_builder import (
        BuildRequest,
        build_timeline,
        parse_timing,
        resolve_scene_images,
    )

    root = render_tmp / "o'neil clips"
    root.mkdir()
    files = write_project(root, music=False, popups=False)
    config = load_render_config()
    request = BuildRequest(
        project_id="p-edit-test", fmt="long", aspect="16:9", language="English",
        folder=files.folder, storyboard=files.storyboard, timing=parse_timing(files.timing),
        sources=resolve_scene_images(files.folder, files.storyboard), width=854, height=480,
        proxy=(854, 480), presets=[], music_folder=None, config=config,
    )
    timeline, _ = build_timeline(request)
    assert timeline.captions.cues
    ass = files.edit_dir / "captions.ass"
    write_ass(
        ass, timeline.captions.cues, timeline.captions.style, timeline.aspect,
        margin_v_px=config.captions_margin_v_px, shadow=config.captions_shadow,
    )
    plan = ff.build_plan(
        timeline, config.proxy, folder=files.folder, config=config, ass_path=ass, captions=True,
    )
    assert "subtitles=filename='" in plan.filter_graph
    assert "o'\\\\\\''neil clips" in plan.filter_graph
    result = ff.run_render(
        plan, files.edit_dir / "proxy.mp4", work_dir=files.edit_dir,
        log_path=files.edit_dir / "render.log",
    )
    assert result.duration_s == pytest.approx(timeline.duration_s, abs=0.3)
    assert "Error parsing filterchain" not in (files.edit_dir / "render.log").read_text("utf-8")


@requires_ffmpeg
def test_adding_a_preset_keeps_finished_renders(render_tmp: Path) -> None:
    """``{presets: [...]}`` adds a size; the proxy (and any finished final) is not made again
    just because timeline.json was rewritten with the longer preset list."""
    files = write_project(render_tmp, music=False)
    stage = EditStage()
    run(stage.run(make_ctx(files, render={"default_presets": [], "x264_preset": "ultrafast"})))
    edit = files.edit_dir
    assert (edit / "proxy.mp4").is_file() and not (edit / "final_720p.mp4").exists()
    proxy_mtime = (edit / "proxy.mp4").stat().st_mtime
    time.sleep(0.05)

    result = run(stage.apply_edits(make_ctx(files, edits={"presets": ["720p"]})))
    assert result is not None
    assert (edit / "final_720p.mp4").is_file()
    assert (edit / "proxy.mp4").stat().st_mtime == proxy_mtime  # kept, not rendered again
    assert "final_720p.mp4" in result.summary and "proxy.mp4" not in result.summary
    timeline = Timeline.model_validate_json((edit / "timeline.json").read_text("utf-8"))
    assert timeline.presets == ["720p"]
    state = read_state(edit)
    assert state["renders"]["proxy"]["render_key"] == state["renders"]["720p"]["render_key"]
    # A replaced picture is a different source: the key changes and the renders are redone.
    time.sleep(1.1)
    picture = files.folder / "06_images" / "scene_02.png"
    picture.write_bytes(picture.read_bytes())
    again = run(stage.run(make_ctx(files, render={"default_presets": ["720p"],
                                                  "x264_preset": "ultrafast"})))
    assert "proxy.mp4" in again.summary and "final_720p.mp4" in again.summary


@requires_ffmpeg
def test_intro_and_outro_clips_are_joined(render_tmp: Path) -> None:
    import subprocess

    files = write_project(render_tmp, music=False, popups=False)
    clip = render_tmp / "intro.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "color=c=red:s=320x180:d=1:r=30", "-pix_fmt", "yuv420p", str(clip)],
        check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    ctx = make_ctx(files, render={"default_presets": [], "x264_preset": "ultrafast"})
    stage = EditStage()
    run(stage.run(ctx))
    timeline = Timeline.model_validate_json((files.edit_dir / "timeline.json").read_text("utf-8"))
    timeline.intro.path = str(clip)
    timeline.outro.path = str(clip)
    edits = {"timeline": timeline.model_dump(mode="json")}
    result = run(stage.apply_edits(make_ctx(files, edits=edits)))
    assert result is not None
    info = ff.probe_media(files.edit_dir / "proxy.mp4")
    assert info.duration_s == pytest.approx(timeline.duration_s + 2.0, abs=0.3)
    assert {g["id"]: g["passed"] for g in result.gate_results}["edit.duration"] is True
    state = json.loads((files.edit_dir / "render_state.json").read_text("utf-8"))
    expected = timeline.duration_s + 2.0
    assert state["renders"]["proxy"]["expected_s"] == pytest.approx(expected, abs=0.05)

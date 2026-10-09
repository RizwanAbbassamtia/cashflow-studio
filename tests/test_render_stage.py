"""The edit stage around the renderer: plain errors for missing files, the edit gates, the
render and caption settings on /api/settings, and the two new doctor checks. No FFmpeg
needed except where marked."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cashcow_studio import doctor
from cashcow_studio.config import Settings, load_settings
from cashcow_studio.pipeline.stages.base import StageError
from cashcow_studio.pipeline.stages.edit import (
    EditStage,
    allowed_presets,
    final_file_name,
    load_sources,
    options_from,
)
from cashcow_studio.policy import gates
from cashcow_studio.render.presets import load_render_config
from conftest import AppEnv
from test_render_fixtures import (
    ffmpeg_available,
    make_ctx,
    make_render_tmp_fixture,
    write_project,
)

render_tmp = make_render_tmp_fixture()


def run(coro):
    return asyncio.run(coro)


# Missing inputs -------------------------------------------------------------------------------


def test_missing_files_give_plain_english_errors(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    folder = files.folder
    (folder / "06_images" / "scene_02.png").unlink()
    with pytest.raises(StageError, match=r"No picture for scene\(s\) 2"):
        load_sources(folder)
    (folder / "05_voice" / "voice.wav").unlink()
    files2 = write_project(render_tmp / "two", music=False)
    (files2.folder / "05_voice" / "voice.wav").unlink()
    with pytest.raises(StageError, match="voice.wav"):
        load_sources(files2.folder)
    (files2.folder / "05_voice" / "timing.json").unlink()
    with pytest.raises(StageError, match="timing.json"):
        load_sources(files2.folder)
    (files2.folder / "04_storyboard" / "storyboard.json").unlink()
    with pytest.raises(StageError, match="storyboard step has not produced"):
        load_sources(files2.folder)
    assert final_file_name("proxy") == "proxy.mp4" and final_file_name("1080p") == "final_1080p.mp4"


def test_invalid_edits_are_refused_before_any_work(render_tmp: Path) -> None:
    files = write_project(render_tmp, music=False)
    with pytest.raises(StageError, match="not valid"):
        run(EditStage().run(make_ctx(files, edits={"captions_enabled": "maybe"})))
    with pytest.raises(StageError, match="not valid"):
        run(EditStage().apply_edits(make_ctx(files, edits={"timeline": {"nonsense": 1}})))
    assert run(EditStage().apply_edits(make_ctx(files))) is None  # no edits: nothing to do


def test_presets_follow_the_settings_and_the_4k_switch() -> None:
    config = load_render_config()
    warnings: list[str] = []
    assert allowed_presets(["1080p", "720p", "1080p"], config, False, warnings) == ["1080p", "720p"]
    assert allowed_presets(["2160p", "720p"], config, False, warnings) == ["720p"]
    assert warnings and "4K is switched off" in warnings[0]
    assert allowed_presets(["2160p"], config, True, []) == ["2160p"]
    assert allowed_presets(["proxy", ""], config, True, []) == []
    with pytest.raises(StageError, match="not a render size"):
        allowed_presets(["8k"], config, True, [])
    options = options_from(Settings(render={"default_presets": ["720p"], "x264_preset": "fast",
                                            "enable_4k": True},
                                    captions={"enabled": False, "style": "yellow"}))
    assert options.default_presets == ["720p"] and options.x264_preset == "fast"
    assert options.enable_4k and not options.captions_enabled and options.caption_style == "yellow"
    assert options_from(object()).default_presets == ["1080p"]


# Gates -----------------------------------------------------------------------------------------


def test_edit_gates() -> None:
    results = {r.id: r for r in gates.evaluate("edit", {
        "music_path": None, "music_license_ok": False,
        "renders": [{"preset": "proxy", "expected_s": 10.0, "actual_s": 10.4}],
        "true_peak_dbtp": None,
    })}
    assert results["edit.music_license"].passed and "No background music" in results[
        "edit.music_license"].detail
    assert results["edit.duration"].passed and results["edit.audio_peaks"].passed
    assert results["edit.music_license"].severity == "warn"
    assert results["edit.duration"].severity == "block"

    results = {r.id: r for r in gates.evaluate("edit", {
        "music_path": "D:/music/calm.mp3", "music_license_ok": False,
        "renders": [{"preset": "proxy", "expected_s": 10.0, "actual_s": 10.4},
                    {"preset": "1080p", "expected_s": 10.0, "actual_s": 12.0},
                    {"preset": "720p", "expected_s": 10.0, "actual_s": None}],
        "true_peak_dbtp": -0.4,
    })}
    assert not results["edit.music_license"].passed
    assert "calm.mp3.license.txt" in results["edit.music_license"].detail
    assert not results["edit.duration"].passed
    assert "1080p (12.0 s instead of 10.0 s)" in results["edit.duration"].detail
    assert "720p (length unknown)" in results["edit.duration"].detail
    assert not results["edit.audio_peaks"].passed and "-0.4" in results["edit.audio_peaks"].detail
    assert gates.blocking_reasons(list(results.values())) == [
        "Rendered length matches the timeline: " + results["edit.duration"].detail
    ]
    empty = {r.id: r for r in gates.evaluate("edit", {"renders": []})}
    assert not empty["edit.duration"].passed and "No video was rendered" in empty[
        "edit.duration"].detail


# Settings ------------------------------------------------------------------------------------


def test_render_and_caption_settings_round_trip(client: TestClient, app_env: AppEnv) -> None:
    body = client.get("/api/settings").json()
    assert body["render"] == {"default_presets": ["1080p"], "x264_preset": "medium",
                              "enable_4k": False}
    assert body["captions"] == {"enabled": True, "style": "bold-white"}

    saved = client.put("/api/settings", json={
        "render": {"default_presets": ["720p", "1080p", "720p"], "x264_preset": "fast",
                   "enable_4k": True},
        "captions": {"enabled": False, "style": "yellow"},
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["render"] == {"default_presets": ["720p", "1080p"],
                                      "x264_preset": "fast", "enable_4k": True}
    assert saved.json()["captions"] == {"enabled": False, "style": "yellow"}
    on_disk = json.loads(app_env.settings_file.read_text(encoding="utf-8"))
    assert on_disk["render"]["x264_preset"] == "fast" and on_disk["captions"]["style"] == "yellow"
    # Saving another section keeps these.
    client.put("/api/settings", json={"voice": {"speaking_rate_wpm": 160}})
    again = client.get("/api/settings").json()
    assert again["render"]["enable_4k"] is True and again["captions"]["enabled"] is False
    reloaded = load_settings()
    assert reloaded.render.default_presets == ["720p", "1080p"]
    assert reloaded.captions.style == "yellow"
    assert client.app.state.engine.settings.render.x264_preset == "fast"

    bad = client.put("/api/settings", json={"render": {"x264_preset": "warp-speed"}})
    assert bad.status_code == 422 and "render settings" in bad.json()["detail"]
    bad = client.put("/api/settings", json={"render": {"default_presets": ["4k"]}})
    assert bad.status_code == 422
    bad = client.put("/api/settings", json={"captions": {"style": ""}})
    assert bad.status_code == 422 and "caption settings" in bad.json()["detail"]
    assert client.get("/api/settings").json()["render"]["x264_preset"] == "fast"  # untouched


def test_a_broken_render_section_falls_back_to_defaults(app_env: AppEnv) -> None:
    app_env.app_data_dir.mkdir(parents=True, exist_ok=True)
    app_env.settings_file.write_text(
        json.dumps({"render": {"x264_preset": 42}, "captions": {"style": "clean"}}), "utf-8"
    )
    settings = load_settings()
    assert settings.render.x264_preset == "medium"
    assert settings.captions.style == "clean"


# Doctor ----------------------------------------------------------------------------------------


def test_doctor_fonts_check(monkeypatch: pytest.MonkeyPatch, render_tmp: Path) -> None:
    result = doctor.check_fonts()
    assert result.status == "ok" and "6 Noto fonts" in result.detail

    monkeypatch.setenv("CCS_FONTS_DIR", str(render_tmp / "missing"))
    result = doctor.check_fonts()
    assert result.status == "fail" and result.fix_hint

    partial = render_tmp / "partial"
    partial.mkdir()
    monkeypatch.setenv("CCS_FONTS_DIR", str(partial))
    assert doctor.check_fonts().status == "fail"
    source = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSans[wdth,wght].ttf"
    shutil.copy(source, partial / source.name)
    result = doctor.check_fonts()
    assert result.status == "warn" and "Noto Sans JP" in result.detail and result.fix_hint


def test_doctor_ffmpeg_filters_check(app_env: AppEnv, monkeypatch: pytest.MonkeyPatch,
                                     tmp_path: Path) -> None:
    settings = load_settings()
    if ffmpeg_available():
        result = doctor.check_ffmpeg_filters(settings)
        assert result.status == "ok" and "libass" in result.detail
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    result = doctor.check_ffmpeg_filters(load_settings())
    assert result.status == "fail" and "FFmpeg was not found" in result.detail
    assert result.fix_hint


def test_doctor_report_lists_the_new_checks(client: TestClient) -> None:
    ids = [check["id"] for check in client.get("/api/doctor").json()["checks"]]
    assert ids.index("ffmpeg_filters") == ids.index("ffprobe") + 1
    assert ids.index("fonts") == ids.index("ffmpeg_filters") + 1

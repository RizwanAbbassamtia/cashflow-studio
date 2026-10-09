"""Doctor checks: every check returns a result, even when a tool is missing or a check breaks."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cashcow_studio import doctor
from cashcow_studio.config import load_settings
from conftest import AppEnv

EXPECTED_IDS = [
    "python_version",
    "ffmpeg",
    "ffprobe",
    "ffmpeg_filters",
    "fonts",
    "deno",
    "yt_dlp",
    "webview2",
    "shared_dir",
    "projects_dir",
    "exports_dir",
    "anthropic_key",
    "voice_key",
    "image_key",
    "disk_space",
]


@pytest.fixture
def no_tools(app_env: AppEnv, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An environment where no external tool can be found."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    for name in ("FFMPEG_PATH", "FFPROBE_PATH", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name in doctor.VOICE_KEYS + doctor.IMAGE_KEYS:
        monkeypatch.delenv(name, raising=False)


def test_every_check_returns_a_result_without_tools(no_tools: None) -> None:
    report = doctor.run_all(load_settings())
    assert [check.id for check in report.checks] == EXPECTED_IDS
    by_id = {check.id: check for check in report.checks}

    assert by_id["ffmpeg"].status == "fail"
    assert by_id["ffmpeg"].fix_hint
    assert by_id["ffprobe"].status == "fail"
    assert by_id["ffmpeg_filters"].status == "fail"  # no ffmpeg, so no filters to check
    assert by_id["fonts"].status == "ok"  # the vendored Noto fonts need no tool
    assert by_id["deno"].status == "warn"
    assert by_id["anthropic_key"].status == "warn"
    assert by_id["voice_key"].status == "warn"
    assert by_id["image_key"].status == "warn"
    assert by_id["python_version"].status == "ok"
    assert by_id["shared_dir"].status == "ok"  # CCS_SHARED_DIR is set, so not the default
    assert by_id["projects_dir"].status == "ok"
    assert by_id["exports_dir"].status == "ok"
    assert by_id["disk_space"].status in {"ok", "warn"}
    assert report.ok is False  # ffmpeg failed
    for check in report.checks:
        assert check.status in {"ok", "warn", "fail"}
        assert check.name
        if check.status != "ok":
            assert check.fix_hint, check.id


def test_default_shared_dir_is_a_warning(
    app_env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CCS_SHARED_DIR")
    report = doctor.run_all(load_settings())
    shared = next(check for check in report.checks if check.id == "shared_dir")
    assert shared.status == "warn"
    assert str(app_env.app_data_dir / "shared") in shared.detail


def test_a_crashing_check_becomes_a_fail_result(
    app_env: AppEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("registry on fire")

    monkeypatch.setattr(shutil, "which", explode)
    monkeypatch.setattr(shutil, "disk_usage", explode)
    report = doctor.run_all(load_settings())
    by_id = {check.id: check for check in report.checks}
    assert by_id["ffmpeg"].status == "fail"
    assert "registry on fire" in by_id["ffmpeg"].detail
    assert by_id["disk_space"].status == "fail"
    assert "RuntimeError" in by_id["disk_space"].detail
    assert report.ok is False


def test_key_checks_never_reveal_values(app_env: AppEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-ant-secret-value-1234"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "fish-secret-value-5678")
    report = doctor.run_all(load_settings())
    by_id = {check.id: check for check in report.checks}
    assert by_id["anthropic_key"].status == "ok"
    assert by_id["voice_key"].status == "ok"
    assert "FISH_AUDIO_API_KEY" in by_id["voice_key"].detail
    dumped = report.model_dump_json()
    assert secret not in dumped
    assert "fish-secret-value-5678" not in dumped


def test_ffmpeg_path_setting_is_honoured(app_env: AppEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    real = shutil.which("ffmpeg")
    if not real:
        pytest.skip("FFmpeg is not installed on this machine")
    monkeypatch.setenv("FFMPEG_PATH", real)
    monkeypatch.setenv("PATH", "")
    settings = load_settings()
    assert settings.ffmpeg_path == real
    result = doctor.check_ffmpeg(settings)
    assert result.status == "ok"
    assert "ffmpeg version" in result.detail


def test_doctor_endpoint_shape(client: TestClient) -> None:
    response = client.get("/api/doctor")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"ok", "checks"}
    assert isinstance(body["ok"], bool)
    assert [check["id"] for check in body["checks"]] == EXPECTED_IDS
    for check in body["checks"]:
        assert set(check) == {"id", "name", "status", "detail", "fix_hint"}


def test_system_info_shape(client: TestClient, app_env: AppEnv) -> None:
    body = client.get("/api/system/info").json()
    assert set(body) == {
        "version",
        "platform",
        "python",
        "app_data_dir",
        "shared_dir",
        "shared_dir_is_default",
        "projects_dir",
        "exports_dir",
    }
    assert body["app_data_dir"] == str(app_env.app_data_dir)
    assert body["shared_dir_is_default"] is False
    assert body["python"].startswith("3.")


def test_doctor_cli_table_lists_every_check(app_env: AppEnv) -> None:
    from cashcow_studio.cli import format_report

    report = doctor.run_all(load_settings())
    text = format_report(report)
    for check in report.checks:
        assert check.name in text
    assert text.startswith("STATUS")

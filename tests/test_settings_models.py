"""Settings > Models and providers (docs/M1-M2-CONTRACT.md section 11): the nested ``llm``,
``research``, ``pipeline`` and ``voice`` objects on GET/PUT /api/settings, the read-only
provider status list, how a save reaches the running pipeline engine, and how settings.json
keeps every section when another one is saved."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from cashcow_studio.app import create_app
from cashcow_studio.config import Settings, load_settings
from cashcow_studio.pipeline.bootstrap import build_engine, max_parallel_projects
from conftest import AppEnv


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM", "RESEARCH", "IMAGE", "VOICE"):
        monkeypatch.setenv(f"CCS_{name}_PROVIDER", "mock")
    monkeypatch.delenv("CCS_MAX_PARALLEL_PROJECTS", raising=False)


def test_get_returns_nested_defaults_and_provider_rows(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert body["llm"]["models"]["title"] == "claude-opus-5-5"
    assert body["llm"]["models"]["storyboard"] == "claude-sonnet-5-5"
    assert body["llm"]["effort"]["script"] == "high"
    assert body["research"] == {"provider": "yt-dlp", "llm_rerank": False}
    assert body["pipeline"] == {"max_parallel_projects": 2}
    assert body["voice"] == {"speaking_rate_wpm": 150}
    kinds = [row["kind"] for row in body["providers"]]
    assert kinds == ["llm", "research", "image", "voice"]
    for row in body["providers"]:
        assert row["status"] in {"ready", "mock", "missing_key", "not_configured", "error"}
        assert row["name"] and isinstance(row["detail"], str)


def test_provider_rows_never_carry_key_values(client: TestClient) -> None:
    secret = "sk-ant-api03-SECRETVALUE-abcdefgh-WXYZ"
    saved = client.put("/api/settings", json={"keys": {"ANTHROPIC_API_KEY": secret}})
    assert saved.status_code == 200
    body = client.get("/api/settings")
    assert secret not in body.text
    llm_row = next(row for row in body.json()["providers"] if row["kind"] == "llm")
    assert llm_row["status"] in {"ready", "mock"}  # the key is set now


def test_put_saves_each_section_and_keeps_the_others(client: TestClient, app_env: AppEnv) -> None:
    saved = client.put(
        "/api/settings",
        json={"llm": {"models": {"title": "claude-sonnet-5-5"}, "effort": {"title": "low"}}},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["llm"]["models"]["title"] == "claude-sonnet-5-5"
    assert saved.json()["llm"]["effort"]["title"] == "low"
    assert saved.json()["llm"]["models"]["script"] == "claude-opus-5-5"  # untouched default

    saved = client.put(
        "/api/settings",
        json={
            "research": {"provider": "mock", "llm_rerank": True},
            "pipeline": {"max_parallel_projects": 3},
            "voice": {"speaking_rate_wpm": 170},
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["research"] == {"provider": "mock", "llm_rerank": True}
    assert body["pipeline"] == {"max_parallel_projects": 3}
    assert body["voice"] == {"speaking_rate_wpm": 170}

    on_disk = json.loads(app_env.settings_file.read_text(encoding="utf-8"))
    assert on_disk["llm"]["models"]["title"] == "claude-sonnet-5-5"
    assert on_disk["research"]["provider"] == "mock"
    assert on_disk["pipeline"]["max_parallel_projects"] == 3
    assert on_disk["voice"]["speaking_rate_wpm"] == 170

    # Saving a folder afterwards keeps the model settings (and the other way round).
    folder_save = client.put("/api/settings", json={"projects_dir": str(app_env.projects_dir)})
    assert folder_save.status_code == 200
    on_disk = json.loads(app_env.settings_file.read_text(encoding="utf-8"))
    assert on_disk["projects_dir"] == str(app_env.projects_dir)
    assert on_disk["research"]["provider"] == "mock"
    assert client.get("/api/settings").json()["voice"]["speaking_rate_wpm"] == 170

    # A fresh start reads the sections back.
    reloaded = load_settings()
    assert reloaded.research.provider == "mock" and reloaded.research_provider == "mock"
    assert reloaded.pipeline.max_parallel_projects == 3
    assert reloaded.voice.speaking_rate_wpm == 170
    assert reloaded.llm.models["title"] == "claude-sonnet-5-5"


def test_invalid_nested_values_are_rejected_in_plain_english(
    client: TestClient, app_env: AppEnv
) -> None:
    bad = client.put("/api/settings", json={"pipeline": {"max_parallel_projects": 0}})
    assert bad.status_code == 422
    assert "pipeline settings" in bad.json()["detail"]

    bad = client.put("/api/settings", json={"llm": {"models": {"title": "gpt-4"}}})
    assert bad.status_code == 422
    assert "gpt-4" in bad.json()["detail"] and "claude-opus-5-5" in bad.json()["detail"]

    bad = client.put("/api/settings", json={"research": {"provider": "scraper"}})
    assert bad.status_code == 422

    bad = client.put("/api/settings", json={"voice": {"speaking_rate_wpm": 20}})
    assert bad.status_code == 422
    assert not app_env.settings_file.exists()  # nothing was written


def test_a_broken_section_on_disk_falls_back_to_defaults(app_env: AppEnv) -> None:
    app_env.app_data_dir.mkdir(parents=True, exist_ok=True)
    app_env.settings_file.write_text(
        json.dumps({"pipeline": {"max_parallel_projects": 99}, "research": {"provider": "mock"}}),
        encoding="utf-8",
    )
    settings = load_settings()
    assert settings.pipeline.max_parallel_projects == 2  # the bad section was ignored
    assert settings.research.provider == "mock"  # the good one was kept


def test_saving_reaches_the_running_engine(offline: None, app_env: AppEnv) -> None:
    with TestClient(create_app()) as client:
        engine = client.app.state.engine
        assert engine.providers["llm"].provider == "mock"
        assert engine.providers["research"].name == "mock"
        assert engine.max_parallel == 2

        saved = client.put(
            "/api/settings",
            json={
                "llm": {"models": {"title": "claude-haiku-4-5"}},
                "pipeline": {"max_parallel_projects": 4},
            },
        )
        assert saved.status_code == 200, saved.text
        # The client was rebuilt with the new per-task model; the parallel limit moved too.
        assert engine.providers["llm"].config.model_for("title") == "claude-haiku-4-5"
        assert engine.providers["llm"].config.model_for("script") == "claude-opus-5-5"
        assert engine.max_parallel == 4
        # The settings object the routers and the engine share is the same one.
        assert client.app.state.settings is engine.settings
        assert engine.settings.pipeline.max_parallel_projects == 4


def test_bootstrap_wires_every_provider_and_stage(offline: None, app_env: AppEnv) -> None:
    settings = Settings()
    engine = build_engine(settings)
    assert {"llm", "research", "image", "voice"} <= set(engine.providers)
    assert engine.providers["llm"].provider == "mock"
    assert engine.providers["research"].name == "mock"
    assert engine.providers["image"].id == "mock" and engine.providers["voice"].id == "mock"
    assert {stage.value for stage in engine.stages} >= {"research", "title", "script", "storyboard"}


def test_max_parallel_env_wins_over_settings(
    offline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(pipeline={"max_parallel_projects": 5})
    assert max_parallel_projects(settings) == 5
    monkeypatch.setenv("CCS_MAX_PARALLEL_PROJECTS", "1")
    assert max_parallel_projects(settings) == 1
    monkeypatch.setenv("CCS_MAX_PARALLEL_PROJECTS", "not-a-number")
    assert max_parallel_projects(settings) == 5

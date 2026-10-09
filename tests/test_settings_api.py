"""/api/settings: masked keys, deletion, name validation, .env and settings.json on disk."""

from __future__ import annotations

import json
import os

from fastapi.testclient import TestClient

from conftest import AppEnv

TEST_KEY = "CFS_TEST_ANTHROPIC_KEY_FOR_PYTEST"


def test_get_lists_known_keys_unset(client: TestClient, app_env: AppEnv) -> None:
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["shared_dir"] == str(app_env.shared_dir)
    assert body["projects_dir"] == str(app_env.projects_dir)
    assert body["exports_dir"] == str(app_env.exports_dir)
    assert body["shared_dir_is_default"] is False
    keys = body["keys"]
    assert "ANTHROPIC_API_KEY" in keys and "FISH_AUDIO_API_KEY" in keys
    assert keys["ANTHROPIC_API_KEY"] == {"set": False, "masked": ""}


def test_keys_are_masked_and_written_to_env_file(client: TestClient, app_env: AppEnv) -> None:
    secret = "sk-ant-api03-abcdefghijklmnop-WXYZ"
    response = client.put("/api/settings", json={"keys": {"ANTHROPIC_API_KEY": secret}})
    assert response.status_code == 200, response.text
    status = response.json()["keys"]["ANTHROPIC_API_KEY"]
    assert status == {"set": True, "masked": "sk-...WXYZ"}
    assert secret not in response.text

    assert app_env.env_file.is_file()
    text = app_env.env_file.read_text(encoding="utf-8")
    assert f"ANTHROPIC_API_KEY={secret}" in text
    assert os.environ["ANTHROPIC_API_KEY"] == secret  # reloaded for the providers

    # GET shows the same masked state and still no raw value.
    again = client.get("/api/settings")
    assert again.json()["keys"]["ANTHROPIC_API_KEY"]["masked"] == "sk-...WXYZ"
    assert secret not in again.text


def test_short_values_mask_to_empty_string(client: TestClient) -> None:
    response = client.put("/api/settings", json={"keys": {"MINIMAX_GROUP_ID": "1234567"}})
    assert response.json()["keys"]["MINIMAX_GROUP_ID"] == {"set": True, "masked": ""}
    exact = client.put("/api/settings", json={"keys": {"MINIMAX_GROUP_ID": "12345678"}})
    assert exact.json()["keys"]["MINIMAX_GROUP_ID"]["masked"] == "123...5678"


def test_null_deletes_a_key(client: TestClient, app_env: AppEnv) -> None:
    client.put(
        "/api/settings", json={"keys": {TEST_KEY: "abcdefghijkl", "FAL_KEY": "fal-12345678"}}
    )
    assert os.environ.get(TEST_KEY) == "abcdefghijkl"

    response = client.put("/api/settings", json={"keys": {TEST_KEY: None}})
    assert response.status_code == 200
    keys = response.json()["keys"]
    assert TEST_KEY not in keys  # not a known key, so it disappears once removed
    assert keys["FAL_KEY"]["set"] is True
    text = app_env.env_file.read_text(encoding="utf-8")
    assert TEST_KEY not in text
    assert "FAL_KEY=fal-12345678" in text
    assert TEST_KEY not in os.environ

    cleared = client.put("/api/settings", json={"keys": {"FAL_KEY": None}})
    assert cleared.json()["keys"]["FAL_KEY"] == {"set": False, "masked": ""}


def test_invalid_key_names_are_rejected(client: TestClient, app_env: AppEnv) -> None:
    secret = "abcdefghijkl-SECRET-VALUE"
    for bad in ("lowercase_key", "1STARTS_WITH_DIGIT", "HAS-HYPHEN", "HAS SPACE", ""):
        response = client.put("/api/settings", json={"keys": {bad: secret}})
        assert response.status_code == 422, bad
        assert response.headers["content-type"].startswith("application/json")
        assert secret not in response.text, bad  # the error never echoes the value
        assert "not a valid key name" in response.json()["detail"]
    assert not app_env.env_file.exists()


def test_paths_are_saved_and_can_go_back_to_default(
    client: TestClient, app_env: AppEnv, tmp_path
) -> None:
    new_shared = tmp_path / "drive" / "CashflowStudio"
    response = client.put("/api/settings", json={"shared_dir": str(new_shared)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["shared_dir"] == str(new_shared)
    assert body["shared_dir_is_default"] is False
    assert (new_shared / "channels").is_dir()
    saved = json.loads(app_env.settings_file.read_text(encoding="utf-8"))
    assert saved["shared_dir"] == str(new_shared)
    assert saved["projects_dir"] == str(app_env.projects_dir)

    info = client.get("/api/system/info").json()
    assert info["shared_dir"] == str(new_shared)
    assert client.get("/api/channels").json() == []  # the store follows the new folder

    reset = client.put("/api/settings", json={"shared_dir": None})
    assert reset.json()["shared_dir_is_default"] is True
    assert reset.json()["shared_dir"] == str(app_env.app_data_dir / "shared")
    assert json.loads(app_env.settings_file.read_text(encoding="utf-8"))["shared_dir"] is None


def test_relative_folder_is_rejected(client: TestClient) -> None:
    response = client.put("/api/settings", json={"projects_dir": "relative/folder"})
    assert response.status_code == 422
    assert "full folder path" in response.json()["detail"]

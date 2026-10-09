"""Shared fixtures: every test gets a fresh app with its own temporary data folders.

The ``CFS_*`` environment variables are set before ``create_app()`` runs, which is how the
backend learns where to keep settings, keys and channels.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cashflow_studio.app import create_app

# Short names keep the temp paths well under the Windows 260-character limit.
pytest_plugins: list[str] = []


@dataclass
class AppEnv:
    app_data_dir: Path
    shared_dir: Path
    projects_dir: Path
    exports_dir: Path

    @property
    def channels_dir(self) -> Path:
        return self.shared_dir / "channels"

    @property
    def env_file(self) -> Path:
        return self.app_data_dir / ".env"

    @property
    def settings_file(self) -> Path:
        return self.app_data_dir / "settings.json"


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppEnv]:
    env = AppEnv(
        app_data_dir=tmp_path / "app",
        shared_dir=tmp_path / "shared",
        projects_dir=tmp_path / "projects",
        exports_dir=tmp_path / "exports",
    )
    monkeypatch.setenv("CFS_APP_DATA_DIR", str(env.app_data_dir))
    monkeypatch.setenv("CFS_SHARED_DIR", str(env.shared_dir))
    monkeypatch.setenv("CFS_PROJECTS_DIR", str(env.projects_dir))
    monkeypatch.setenv("CFS_EXPORTS_DIR", str(env.exports_dir))
    monkeypatch.delenv("CFS_PORT", raising=False)
    monkeypatch.delenv("CFS_FRONTEND_DIST", raising=False)
    # Keys written to .env are loaded into os.environ by the app; restore it afterwards.
    snapshot = os.environ.copy()
    yield env
    for name in list(os.environ):
        if name not in snapshot:
            del os.environ[name]
    os.environ.update(snapshot)


@pytest.fixture
def client(app_env: AppEnv) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def channel_payload(name: str = "Kind Ledger", **overrides: Any) -> dict[str, Any]:
    """A valid channel body as the Channel Setup form would post it (no slug)."""
    body: dict[str, Any] = {
        "channel": {
            "name": name,
            "url": "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx",
            "language": "English",
            "niche": "Kindness and emotional stories",
            "formats": "both",
            "brand_colors": ["#1F3864", "#ffc000"],
            "owner": "Imran",
        },
        "competitors": [
            {
                "name": "Human Ember",
                "url": "https://www.youtube.com/channel/UCFjva5hxOFoj2ViNgSuEQJg",
                "priority": 1,
                "why": "Same niche, 3x our views",
            },
            {"name": "The Gentle Hour", "url": "https://www.youtube.com/@thegentlehour"},
        ],
        "frameworks": [
            {
                "type": "title",
                "name": "Title Writing Prompt",
                "path": "channels/kind-ledger/frameworks/title.txt",
            }
        ],
        "voice": {"tool": "fish_audio", "api_key_env": "FISH_AUDIO_API_KEY"},
        "images": {"tool": "google_gemini", "api_key_env": "GEMINI_API_KEY"},
        "reviewer": "Imran",
    }
    body.update(overrides)
    return body

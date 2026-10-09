"""Runtime settings: where the app keeps its data and which port and tools it uses.

Values come from, in order of priority:

1. environment variables: ``CCS_APP_DATA_DIR``, ``CCS_SHARED_DIR``, ``CCS_PROJECTS_DIR``,
   ``CCS_EXPORTS_DIR``, ``CCS_PORT`` (prefix ``CCS_``) and the plain ``FFMPEG_PATH`` /
   ``FFPROBE_PATH``;
2. ``<app_data_dir>/settings.json``, written by the Settings screen;
3. the defaults below.

``<app_data_dir>/.env`` holds API keys; it is loaded into the process environment before the
settings are read, so a tool path saved there counts too.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .storage.settings_store import SettingsStore

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"
ENV_FILE = ".env"
PATH_KEYS = ("shared_dir", "projects_dir", "exports_dir")
NESTED_KEYS = ("llm", "research", "pipeline", "voice")
"""The nested objects of settings.json from docs/M1-M2-CONTRACT.md section 11."""
HOST = "127.0.0.1"

ResearchProviderName = Literal["yt-dlp", "mock"]
EffortLevel = Literal["low", "medium", "high"]


class LlmSettings(BaseModel):
    """Per-task overrides of ``config/llm.yaml`` (same keys: ``models``, ``effort``)."""

    models: dict[str, str] = {}
    effort: dict[str, EffortLevel] = {}
    max_tokens: dict[str, int] = {}


class ResearchSettings(BaseModel):
    provider: ResearchProviderName = "yt-dlp"
    """Where competitor videos come from; ``CCS_RESEARCH_PROVIDER`` wins when set."""
    llm_rerank: bool = False
    """Let Claude re-rank the top candidates by fit to the niche (one small call per scan)."""


class PipelineSettings(BaseModel):
    max_parallel_projects: int = Field(default=2, ge=1, le=8)


class VoiceSettings(BaseModel):
    speaking_rate_wpm: int | None = Field(default=None, ge=80, le=260)
    """Words per minute for every language; ``None`` means the table in config/voice.yaml."""


NESTED_MODELS: dict[str, type[BaseModel]] = {
    "llm": LlmSettings,
    "research": ResearchSettings,
    "pipeline": PipelineSettings,
    "voice": VoiceSettings,
}


def _home() -> Path:
    profile = os.environ.get("USERPROFILE") if sys.platform == "win32" else None
    return Path(profile) if profile else Path.home()


def default_app_data_dir() -> Path:
    """``%LOCALAPPDATA%/CashCowStudio`` on Windows, ``~/.local/share/CashCowStudio`` elsewhere."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else _home() / "AppData" / "Local"
        return base / "CashCowStudio"
    return Path.home() / ".local" / "share" / "CashCowStudio"


def default_videos_dir() -> Path:
    """``%USERPROFILE%/Videos/CashCowStudio`` on Windows, ``~/Videos/CashCowStudio`` elsewhere."""
    return _home() / "Videos" / "CashCowStudio"


def default_projects_dir() -> Path:
    return default_videos_dir() / "projects"


def default_exports_dir() -> Path:
    return default_videos_dir() / "exports"


def _expand(value: Any) -> Any:
    """Expand ``~`` and ``%VAR%`` / ``$VAR`` in a path string; blank strings become ``None``."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return Path(os.path.expandvars(os.path.expanduser(text)))
    return value


class Settings(BaseSettings):
    """Effective configuration for one running app."""

    model_config = SettingsConfigDict(
        env_prefix="CCS_",
        extra="ignore",
        validate_by_name=True,
        validate_by_alias=True,
        validate_assignment=True,
    )

    app_data_dir: Path = Field(default_factory=default_app_data_dir)
    shared_dir: Path | None = Field(
        default=None, description="synced shared folder; None means <app_data_dir>/shared"
    )
    projects_dir: Path = Field(default_factory=default_projects_dir)
    exports_dir: Path = Field(default_factory=default_exports_dir)
    port: int = Field(default=8765, ge=1, le=65535)
    ffmpeg_path: str = Field(default="", validation_alias="FFMPEG_PATH")
    ffprobe_path: str = Field(default="", validation_alias="FFPROBE_PATH")

    # Models and providers (settings.json nested keys, Settings > Models and providers).
    llm: LlmSettings = LlmSettings()
    research: ResearchSettings = ResearchSettings()
    pipeline: PipelineSettings = PipelineSettings()
    voice: VoiceSettings = VoiceSettings()

    @field_validator("app_data_dir", mode="before")
    @classmethod
    def _app_data_dir_default(cls, value: Any) -> Any:
        expanded = _expand(value)
        return default_app_data_dir() if expanded is None else expanded

    @field_validator("shared_dir", mode="before")
    @classmethod
    def _shared_dir_blank_is_none(cls, value: Any) -> Any:
        return _expand(value)

    @field_validator("projects_dir", mode="before")
    @classmethod
    def _projects_dir_default(cls, value: Any) -> Any:
        expanded = _expand(value)
        return default_projects_dir() if expanded is None else expanded

    @field_validator("exports_dir", mode="before")
    @classmethod
    def _exports_dir_default(cls, value: Any) -> Any:
        expanded = _expand(value)
        return default_exports_dir() if expanded is None else expanded

    @field_validator("ffmpeg_path", "ffprobe_path", mode="before")
    @classmethod
    def _tool_path_strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    # Derived locations -------------------------------------------------------------------

    @property
    def shared_dir_is_default(self) -> bool:
        """True when no shared folder was chosen and the app falls back to its own folder."""
        return self.shared_dir is None

    @property
    def resolved_shared_dir(self) -> Path:
        return self.shared_dir if self.shared_dir is not None else self.app_data_dir / "shared"

    @property
    def channels_dir(self) -> Path:
        return self.resolved_shared_dir / "channels"

    @property
    def settings_file(self) -> Path:
        return self.app_data_dir / SETTINGS_FILE

    @property
    def env_file(self) -> Path:
        return self.app_data_dir / ENV_FILE

    @property
    def research_provider(self) -> str:
        """The research provider id the research package understands (``ytdlp`` or ``mock``)."""
        return "mock" if self.research.provider == "mock" else "ytdlp"

    @property
    def max_parallel_projects(self) -> int:
        return self.pipeline.max_parallel_projects

    @property
    def speaking_rate_override(self) -> int | None:
        """A speaking rate chosen in Settings, or ``None`` for the per-language table."""
        return self.voice.speaking_rate_wpm

    def paths_as_dict(self) -> dict[str, str | None]:
        """The user's folder choices in the form saved to settings.json."""
        return {
            "shared_dir": str(self.shared_dir) if self.shared_dir is not None else None,
            "projects_dir": str(self.projects_dir),
            "exports_dir": str(self.exports_dir),
        }

    def nested_as_dict(self) -> dict[str, dict[str, Any]]:
        """The ``llm``, ``research``, ``pipeline`` and ``voice`` objects of settings.json."""
        return {key: getattr(self, key).model_dump(mode="json") for key in NESTED_KEYS}

    def ensure_dirs(self) -> list[str]:
        """Create the folders the app needs. Never raises; returns plain-English problems."""
        problems: list[str] = []
        for folder in (
            self.app_data_dir,
            self.resolved_shared_dir,
            self.channels_dir,
            self.projects_dir,
            self.exports_dir,
        ):
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                problems.append(f"Could not create the folder {folder}: {exc}")
        return problems


def nested_overrides(saved: dict[str, Any]) -> dict[str, BaseModel]:
    """Validate the nested objects of settings.json one by one.

    A section that does not validate (a hand-edited file, an older app version) is logged and
    replaced by its defaults; it never stops the app from starting.
    """
    overrides: dict[str, BaseModel] = {}
    for key, model in NESTED_MODELS.items():
        value = saved.get(key)
        if not isinstance(value, dict):
            continue
        try:
            overrides[key] = model.model_validate(value)
        except ValidationError as exc:
            log.warning(
                "Ignoring the '%s' section of settings.json (using defaults): %s", key, exc
            )
    return overrides


def load_settings() -> Settings:
    """Build the effective settings: env vars win, then settings.json, then defaults."""
    bootstrap = Settings()
    store = SettingsStore(bootstrap.app_data_dir)
    # Keys and tool paths the user saved in the Settings screen.
    store.load_into_environ()
    saved = store.read_paths()
    overrides: dict[str, Any] = {}
    for key in PATH_KEYS:
        if os.environ.get(f"CCS_{key.upper()}"):
            continue  # an environment variable always wins over settings.json
        if key in saved:
            overrides[key] = saved[key]
    overrides.update(nested_overrides(store.read_sections()))
    return Settings(**overrides)

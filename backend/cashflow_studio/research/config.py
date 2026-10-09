"""Research settings from ``config/research.yaml`` with the same defaults built in.

``load_research_config()`` reads the YAML file in the repo's ``config`` folder (or
``$CFS_CONFIG_DIR``); a missing or broken file means the defaults below, never a crash.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..models.research import Tab

log = logging.getLogger(__name__)

CONFIG_FILE = "research.yaml"


class LabelThresholds(BaseModel):
    one_of_ten: float = 10.0
    strong: float = 5.0
    notable: float = 3.0


class ExclusionConfig(BaseModel):
    min_age_days: float = 3
    long_min_seconds: int = 240
    long_max_seconds: int = 2400
    shorts_max_seconds: int = 180
    live_streams: bool = True
    premieres: bool = True
    used_before: bool = True


class PickerConfig(BaseModel):
    strategy: str = "top_outlier_fresh"
    fresh_days: int = 90
    llm_rerank_top: int = Field(default=10, ge=1, le=50)


class ResearchConfig(BaseModel):
    tabs: list[Tab] = ["videos", "shorts"]
    max_videos_per_channel: int = Field(default=200, ge=1, le=2000)
    cache_minutes: int = Field(default=60, ge=0)
    neighbours_before: int = Field(default=10, ge=0)
    neighbours_after: int = Field(default=10, ge=0)
    baseline_min_age_days: float = 7
    labels: LabelThresholds = LabelThresholds()
    exclude: ExclusionConfig = ExclusionConfig()
    picker: PickerConfig = PickerConfig()
    sleep_interval_requests: float = Field(default=0.75, ge=0)
    backoff_minutes: list[float] = [10, 20, 40, 60]
    http_timeout_seconds: float = Field(default=20, gt=0)
    transcript_fallback_languages: list[str] = ["en"]

    @property
    def backoff_seconds(self) -> list[float]:
        return [minutes * 60 for minutes in self.backoff_minutes]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def config_dir() -> Path:
    override = os.environ.get("CFS_CONFIG_DIR", "").strip()
    return Path(override) if override else repo_root() / "config"


def research_config_path() -> Path:
    return config_dir() / CONFIG_FILE


def load_research_config(path: Path | None = None) -> ResearchConfig:
    """The file's values over the defaults; problems are logged and the defaults used."""
    path = path or research_config_path()
    data = _read_yaml(path)
    if not data:
        return ResearchConfig()
    try:
        return ResearchConfig.model_validate(data)
    except ValidationError as exc:
        log.warning("Ignoring %s: %s", path, exc)
        return ResearchConfig()


@lru_cache(maxsize=1)
def cached_research_config() -> ResearchConfig:
    return load_research_config()


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is a declared dependency
        log.warning("pyyaml is not installed; using the built-in research defaults")
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        log.warning("Could not read %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}

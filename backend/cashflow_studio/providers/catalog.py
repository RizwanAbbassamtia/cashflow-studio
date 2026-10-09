"""The provider catalogue: ``config/providers.yaml`` on top of built-in defaults.

The YAML lists every voice and image tool the app knows, with its capabilities, prices,
model ids and the *name* of the environment variable that holds its key (never a value).
The Settings page shows these entries; the adapters read their capabilities from them.

Loading never raises: a missing file means the built-in defaults, a broken entry is skipped
with a log line and the rest of the file still counts. Entries in the file replace built-in
entries with the same id.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .image import gemini as gemini_image
from .image import mock as mock_image
from .image.base import ImageCapabilities
from .voice import ai33 as ai33_voice
from .voice import mock as mock_voice
from .voice.base import ProviderCapabilities

log = logging.getLogger(__name__)

CONFIG_FILE_NAME = "providers.yaml"
CONFIG_DIR_ENV = "CFS_CONFIG_DIR"


def repo_root() -> Path:
    """``F:/CashflowStudio`` when running from the repo (``backend/cashflow_studio/providers``)."""
    return Path(__file__).resolve().parents[3]


def config_dir() -> Path:
    """``<repo>/config``, or the folder named by ``CFS_CONFIG_DIR``."""
    override = os.environ.get(CONFIG_DIR_ENV, "").strip()
    return Path(override) if override else repo_root() / "config"


def config_file() -> Path:
    return config_dir() / CONFIG_FILE_NAME


class ProviderCatalog(BaseModel):
    schema_version: int = 1
    voice: dict[str, ProviderCapabilities] = {}
    image: dict[str, ImageCapabilities] = {}
    source: str = "built-in"
    """Path of the file that was read, or ``built-in``."""

    def voice_capabilities(self, provider_id: str) -> ProviderCapabilities | None:
        return self.voice.get(provider_id)

    def image_capabilities(self, provider_id: str) -> ImageCapabilities | None:
        return self.image.get(provider_id)


def builtin_catalog() -> ProviderCatalog:
    """What the app knows without any file: the mocks, the ai33 stub and the Gemini adapter."""
    return ProviderCatalog(
        voice={
            mock_voice.MOCK_ID: mock_voice.default_capabilities(),
            ai33_voice.AI33_ID: ai33_voice.default_capabilities(),
        },
        image={
            mock_image.MOCK_ID: mock_image.default_capabilities(),
            gemini_image.GEMINI_ID: gemini_image.default_capabilities(),
        },
    )


def load_catalog(path: Path | None = None) -> ProviderCatalog:
    """Built-in defaults updated from ``config/providers.yaml`` (or ``path``). Never raises."""
    catalog = builtin_catalog()
    file = Path(path) if path is not None else config_file()
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        log.info("No provider catalogue at %s; using the built-in defaults.", file)
        return catalog
    try:
        import yaml
    except ImportError:  # pragma: no cover - pyyaml is a dependency
        log.warning("PyYAML is not installed; using the built-in provider defaults.")
        return catalog
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        log.warning("The provider catalogue %s could not be read (%s); using defaults.", file, exc)
        return catalog
    if not isinstance(data, dict):
        log.warning("The provider catalogue %s is not a mapping; using defaults.", file)
        return catalog

    version = data.get("schema_version", 1)
    if isinstance(version, int):
        catalog.schema_version = version
    _merge_section(catalog.voice, data.get("voice"), ProviderCapabilities, "voice", file)
    _merge_section(catalog.image, data.get("image"), ImageCapabilities, "image", file)
    catalog.source = str(file)
    return catalog


def _merge_section(
    target: dict[str, Any], section: Any, model: type[BaseModel], kind: str, file: Path
) -> None:
    if section is None:
        return
    if not isinstance(section, dict):
        log.warning("The '%s' section of %s must be a mapping; it was ignored.", kind, file)
        return
    for provider_id, raw in section.items():
        key = str(provider_id).strip().lower()
        if not isinstance(raw, dict):
            log.warning("Entry %s.%s in %s must be a mapping; it was skipped.", kind, key, file)
            continue
        try:
            target[key] = model.model_validate({**raw, "id": key, "kind": kind})
        except ValidationError as exc:
            log.warning(
                "Entry %s.%s in %s is invalid and was skipped: %s", kind, key, file, exc
            )

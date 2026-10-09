"""Reads and writes the per-user files in the app data folder.

* ``settings.json`` keeps the folder choices made in the Settings screen.
* ``.env`` keeps API keys as ``NAME=value`` lines. The file stays on this PC and is never
  copied to the shared folder. Values are loaded into the process environment so providers
  can read them; the API only ever returns a masked form.

Every read-modify-write of these two files runs under :data:`LOCK`. FastAPI serves the
settings endpoint from a thread pool, so two quick saves in the Settings screen can overlap;
without the lock the second save would silently undo the first.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PATH_KEYS = ("shared_dir", "projects_dir", "exports_dir")
SECTION_KEYS = ("llm", "research", "pipeline", "voice")
"""Nested objects in settings.json (Settings > Models and providers); see config.py."""

# Real keys are a few hundred characters at most. Windows refuses an environment entry over
# 32767 characters, and a saved value that long would stop the app from starting.
MAX_KEY_VALUE_LENGTH = 4096
MAX_ENVIRON_ENTRY_LENGTH = 32767

# Names the Settings screen may not save as keys. The app's own ``CCS_*`` settings in ``.env``
# would silently override the folders chosen in Settings > Paths after the next start; the
# other names steer Windows or Python itself. A hand-edited ``.env`` with such a line is
# still read, and the Settings screen can still clear it.
APP_SETTING_NAMES = frozenset(
    {
        "CCS_APP_DATA_DIR",
        "CCS_SHARED_DIR",
        "CCS_PROJECTS_DIR",
        "CCS_EXPORTS_DIR",
        "CCS_PORT",
        "CCS_FRONTEND_DIST",
    }
)
RESERVED_KEY_NAMES = frozenset(
    {
        "APPDATA",
        "COMPUTERNAME",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "OS",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
)

# Re-entrant so the API endpoint can hold it around a whole update that calls the methods
# below, which take it again.
LOCK = threading.RLock()

# Rows the Settings screen always shows; other names can be added freely.
KNOWN_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "FAL_KEY",
    "REPLICATE_API_TOKEN",
    "IDEOGRAM_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_GROUP_ID",
    "CARTESIA_API_KEY",
    "INWORLD_API_KEY",
    "FISH_AUDIO_API_KEY",
    "AZURE_SPEECH_KEY",
    "AZURE_SPEECH_REGION",
)
VOICE_KEYS: tuple[str, ...] = (
    "MINIMAX_API_KEY",
    "CARTESIA_API_KEY",
    "INWORLD_API_KEY",
    "FISH_AUDIO_API_KEY",
    "AZURE_SPEECH_KEY",
)
IMAGE_KEYS: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "FAL_KEY",
    "REPLICATE_API_TOKEN",
    "IDEOGRAM_API_KEY",
)

ENV_HEADER = (
    "# CashCow Studio API keys. This file belongs to your Windows account only.\n"
    "# Never copy it into the shared folder or send it to anyone.\n"
)


class InvalidKeyName(ValueError):
    """A key name that does not match ``^[A-Z][A-Z0-9_]*$`` or may not be saved as a key."""


class InvalidKeyValue(ValueError):
    """A key value that cannot be stored on one ``.env`` line and in the environment."""


def _name_for_message(name: object) -> str:
    """The name quoted for an error message, or a stand-in when it is long enough to be a
    pasted key value (the message must never repeat a secret)."""
    text = str(name)
    return f"'{text}'" if len(text) <= 40 else "That name"


def validate_key_name(name: str) -> str:
    """Check the shape of a key name: capitals, digits and underscores, starting with a letter."""
    if not isinstance(name, str) or not KEY_NAME_RE.match(name):
        raise InvalidKeyName(
            f"{_name_for_message(name)} is not a valid key name. Use capital letters, digits "
            "and underscores, starting with a letter, for example ANTHROPIC_API_KEY."
        )
    return name


def check_key_name_settable(name: str) -> str:
    """A valid key name that the Settings screen is also allowed to write a value for."""
    validate_key_name(name)
    if name in APP_SETTING_NAMES:
        raise InvalidKeyName(
            f"'{name}' is one of the app's own settings, not an API key. Choose the folders "
            "in Settings > Paths instead."
        )
    if name in RESERVED_KEY_NAMES:
        raise InvalidKeyName(
            f"'{name}' is a Windows or Python system setting, not an API key, so it cannot be "
            "saved here."
        )
    return name


def validate_key_value(name: str, value: str) -> str:
    """Trim a value and check that it fits on one ``.env`` line and in the environment.

    The message names the key but never repeats the value.
    """
    if not isinstance(value, str):
        raise InvalidKeyValue(f"The value for {name} must be text.")
    clean = value.strip()
    if len(clean) > MAX_KEY_VALUE_LENGTH:
        raise InvalidKeyValue(
            f"The value for {name} is too long ({len(clean)} characters; the most is "
            f"{MAX_KEY_VALUE_LENGTH}). Check that only the key itself was pasted."
        )
    if any(ch < " " or ch == "\x7f" for ch in clean):
        raise InvalidKeyValue(
            f"The value for {name} contains a line break or another hidden control character. "
            "Paste the key again as one line of plain text."
        )
    return clean


def environ_can_hold(name: str, value: str) -> bool:
    """True when ``os.environ[name] = value`` would succeed on Windows."""
    return "\x00" not in value and len(name) + 1 + len(value) <= MAX_ENVIRON_ENTRY_LENGTH


def mask(value: str) -> str:
    """``first 3 + '...' + last 4`` characters, or an empty string if shorter than 8."""
    if not value or len(value) < 8:
        return ""
    return f"{value[:3]}...{value[-4:]}"


def parse_env(text: str) -> dict[str, str]:
    """Parse ``NAME=value`` lines. Blank values and unknown line shapes are skipped."""
    keys: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if KEY_NAME_RE.match(name) and value:
            keys[name] = value
    return keys


def atomic_write_text(path: Path, text: str, *, private: bool = False) -> None:
    """Write a file in one step: a temp file next to it, then an atomic replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, prefix=".tmp-", delete=False
    )
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if private:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass  # Windows only knows the read-only flag; the folder is per-user anyway
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


class SettingsStore:
    """The two per-user files under ``app_data_dir``."""

    def __init__(self, app_data_dir: Path | str) -> None:
        self.app_data_dir = Path(app_data_dir)
        self.settings_file = self.app_data_dir / "settings.json"
        self.env_file = self.app_data_dir / ".env"

    # settings.json --------------------------------------------------------------------

    def read_all(self) -> dict[str, Any]:
        """The whole settings.json mapping. Missing or unreadable file -> empty dict."""
        try:
            data: Any = json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def read_paths(self) -> dict[str, str | None]:
        """Folder choices saved earlier. Missing or unreadable file -> empty dict."""
        data = self.read_all()
        paths: dict[str, str | None] = {}
        for key in PATH_KEYS:
            value = data.get(key)
            if key in data and (value is None or isinstance(value, str)):
                paths[key] = value
        return paths

    def read_sections(self) -> dict[str, dict[str, Any]]:
        """The nested ``llm`` / ``research`` / ``pipeline`` / ``voice`` objects that exist."""
        data = self.read_all()
        return {
            key: value
            for key, value in data.items()
            if key in SECTION_KEYS and isinstance(value, dict)
        }

    def _write_merged(self, changes: dict[str, Any]) -> None:
        """Replace the given top-level keys and keep every other key in the file."""
        with LOCK:
            data = self.read_all()
            data.update(changes)
            atomic_write_text(self.settings_file, json.dumps(data, indent=2) + "\n")

    def write_paths(self, paths: dict[str, str | None]) -> None:
        """Save the three folders; the nested model settings in the file are kept."""
        self._write_merged({key: paths.get(key) for key in PATH_KEYS})

    def write_sections(self, sections: dict[str, dict[str, Any]]) -> None:
        """Save one or more nested objects whole; the folders and other sections are kept."""
        clean = {
            key: value
            for key, value in sections.items()
            if key in SECTION_KEYS and isinstance(value, dict)
        }
        if clean:
            self._write_merged(clean)

    # .env -----------------------------------------------------------------------------

    def read_keys(self) -> dict[str, str]:
        """Raw key values from ``.env``. Internal use only; never return these from the API."""
        try:
            text = self.env_file.read_text(encoding="utf-8")
        except OSError:
            return {}
        return parse_env(text)

    def write_keys(self, keys: dict[str, str]) -> None:
        for name in keys:
            validate_key_name(name)
        lines = [f"{name}={keys[name]}" for name in sorted(keys)]
        with LOCK:
            atomic_write_text(self.env_file, ENV_HEADER + "\n".join(lines) + "\n", private=True)

    def update_keys(self, changes: dict[str, str | None]) -> dict[str, str]:
        """Apply ``{NAME: value | None}``; ``None`` (or blank) removes the key.

        Every name and value is checked before anything is written, so one bad entry leaves
        ``.env`` exactly as it was. The file is then replaced in one step and reloaded into
        the process environment, all under :data:`LOCK` so overlapping saves cannot lose each
        other's change.
        """
        cleaned: dict[str, str | None] = {}
        for name, value in changes.items():
            validate_key_name(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                cleaned[name] = None
                continue
            check_key_name_settable(name)
            cleaned[name] = validate_key_value(name, value)

        with LOCK:
            current = self.read_keys()
            removed: list[str] = []
            for name, value in cleaned.items():
                if value is None:
                    current.pop(name, None)
                    removed.append(name)
                else:
                    current[name] = value
            self.write_keys(current)
            for name in removed:
                if name not in RESERVED_KEY_NAMES:  # never unset PATH and friends
                    os.environ.pop(name, None)
            self.load_into_environ()
            return current

    def load_into_environ(self) -> dict[str, str]:
        """Put every key from ``.env`` into ``os.environ`` (the file wins over stale values).

        An entry the environment refuses (a hidden control character or an overlong line in a
        hand-edited file) is skipped and logged by name, never by value, so a damaged ``.env``
        cannot stop the app from starting. :meth:`unloadable_keys` lists such entries and the
        doctor reports them.
        """
        with LOCK:
            keys = self.read_keys()
            loaded: dict[str, str] = {}
            for name, value in keys.items():
                try:
                    os.environ[name] = value
                except (ValueError, OSError):
                    log.warning(
                        "The key %s in %s could not be loaded. Clear it and paste it again in "
                        "Settings > API keys.",
                        name,
                        self.env_file,
                    )
                    continue
                loaded[name] = value
            return loaded

    def unloadable_keys(self) -> list[str]:
        """Names of keys in ``.env`` whose values the process environment cannot hold."""
        return sorted(
            name
            for name, value in self.read_keys().items()
            if not environ_can_hold(name, value)
        )

    def key_status(self) -> dict[str, dict[str, Any]]:
        """``{NAME: {set, masked}}`` for the known keys plus any extra ones in ``.env``."""
        keys = self.read_keys()
        names = list(KNOWN_KEYS) + sorted(set(keys) - set(KNOWN_KEYS))
        return {
            name: {"set": name in keys, "masked": mask(keys.get(name, ""))} for name in names
        }

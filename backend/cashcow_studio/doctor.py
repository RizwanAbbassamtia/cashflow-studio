"""Health checks shown on the Settings page and by ``ccs doctor``.

Every check returns a :class:`CheckResult` and never raises: an unexpected error becomes a
``fail`` result whose ``detail`` holds the error text. Key values are never included.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .config import Settings
from .storage.settings_store import IMAGE_KEYS, VOICE_KEYS, SettingsStore

Status = Literal["ok", "warn", "fail"]

MIN_PYTHON = (3, 12)
MIN_FREE_GB = 20
TOOL_TIMEOUT_SECONDS = 10
WEBVIEW2_CLIENT_ID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_DOWNLOAD = "https://developer.microsoft.com/microsoft-edge/webview2/"


class CheckResult(BaseModel):
    id: str
    name: str
    status: Status
    detail: str = ""
    fix_hint: str = ""


class DoctorReport(BaseModel):
    ok: bool
    checks: list[CheckResult]


# Helpers ------------------------------------------------------------------------------------


def _run_version(executable: str, flag: str = "-version") -> str:
    """First line of ``<tool> -version``. Raises on timeout or a failed start."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [executable, flag],
        capture_output=True,
        text=True,
        timeout=TOOL_TIMEOUT_SECONDS,
        creationflags=flags,
        check=False,
    )
    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    first = output[0].strip() if output else ""
    if completed.returncode != 0 and not first:
        raise RuntimeError(f"{executable} exited with code {completed.returncode}")
    return first or "version unknown"


def _find_tool(name: str, configured: str, sibling_of: str | None = None) -> str | None:
    """Full path of a tool: the configured path, then a sibling of ``sibling_of``, then PATH."""
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    if sibling_of:
        suffix = ".exe" if sys.platform == "win32" else ""
        candidates.append(str(Path(sibling_of).with_name(f"{name}{suffix}")))
    candidates.append(name)
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _writable(folder: Path) -> tuple[bool, str]:
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Cannot create the folder {folder}: {exc}"
    probe = folder / f".cfs-write-test-{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, f"Cannot write inside {folder}: {exc}"
    return True, f"{folder} is ready."


def _nearest_existing(folder: Path) -> Path:
    current = folder
    while not current.exists():
        parent = current.parent
        if parent == current:
            break
        current = parent
    return current


def _key_set(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def _unloadable_keys(settings: Settings | None) -> set[str]:
    """Keys saved in ``.env`` that the process environment refused (see SettingsStore)."""
    if settings is None:
        return set()
    return set(SettingsStore(settings.app_data_dir).unloadable_keys())


UNLOADABLE_FIX = (
    "In Settings > API keys clear that key and paste it again as one line of plain text."
)


# Checks -------------------------------------------------------------------------------------


def check_python_version() -> CheckResult:
    version = platform.python_version()
    if sys.version_info >= MIN_PYTHON:
        return CheckResult(
            id="python_version", name="Python", status="ok", detail=f"Python {version}"
        )
    return CheckResult(
        id="python_version",
        name="Python",
        status="fail",
        detail=f"Python {version} is too old.",
        fix_hint="Install Python 3.12 from python.org and reinstall the app.",
    )


def check_ffmpeg(settings: Settings) -> CheckResult:
    configured = settings.ffmpeg_path or os.environ.get("FFMPEG_PATH", "")
    found = _find_tool("ffmpeg", configured)
    if not found:
        return CheckResult(
            id="ffmpeg",
            name="FFmpeg",
            status="fail",
            detail="FFmpeg was not found on this PC.",
            fix_hint="Install FFmpeg (for example: winget install Gyan.FFmpeg) or set "
            "FFMPEG_PATH to the full path of ffmpeg.exe.",
        )
    return CheckResult(
        id="ffmpeg", name="FFmpeg", status="ok", detail=f"{_run_version(found)} ({found})"
    )


def check_ffprobe(settings: Settings) -> CheckResult:
    configured = settings.ffprobe_path or os.environ.get("FFPROBE_PATH", "")
    ffmpeg = settings.ffmpeg_path or os.environ.get("FFMPEG_PATH", "")
    sibling = shutil.which(ffmpeg) if ffmpeg else None
    found = _find_tool("ffprobe", configured, sibling_of=sibling)
    if not found:
        return CheckResult(
            id="ffprobe",
            name="FFprobe",
            status="fail",
            detail="FFprobe was not found on this PC. It normally comes with FFmpeg.",
            fix_hint="Install FFmpeg (for example: winget install Gyan.FFmpeg) or set "
            "FFPROBE_PATH to the full path of ffprobe.exe.",
        )
    return CheckResult(
        id="ffprobe", name="FFprobe", status="ok", detail=f"{_run_version(found)} ({found})"
    )


REQUIRED_FFMPEG_FILTERS: dict[str, str] = {
    "subtitles": "libass (burnt-in captions)",
    "xfade": "scene transitions",
    "zoompan": "camera moves",
    "sidechaincompress": "music ducking",
    "loudnorm": "loudness normalisation",
}
FULL_BUILD_HINT = (
    "Install the full FFmpeg build (for example: winget install Gyan.FFmpeg, the 'full' "
    "variant with libass) or point FFMPEG_PATH at one."
)


def check_ffmpeg_filters(settings: Settings) -> CheckResult:
    """The filters the edit stage needs (``ffmpeg -filters``)."""
    configured = settings.ffmpeg_path or os.environ.get("FFMPEG_PATH", "")
    found = _find_tool("ffmpeg", configured)
    if not found:
        return CheckResult(
            id="ffmpeg_filters",
            name="FFmpeg filters",
            status="fail",
            detail="FFmpeg was not found, so its filters could not be checked.",
            fix_hint=FULL_BUILD_HINT,
        )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [found, "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TOOL_TIMEOUT_SECONDS,
        creationflags=flags,
        check=False,
    )
    present: set[str] = set()
    for line in (completed.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and "->" in parts[2]:
            present.add(parts[1])
    if not present:
        return CheckResult(
            id="ffmpeg_filters",
            name="FFmpeg filters",
            status="fail",
            detail=f"{found} did not list its filters (exit code {completed.returncode}).",
            fix_hint=FULL_BUILD_HINT,
        )
    missing = [name for name in REQUIRED_FFMPEG_FILTERS if name not in present]
    if missing:
        listed = ", ".join(f"{name} ({REQUIRED_FFMPEG_FILTERS[name]})" for name in missing)
        return CheckResult(
            id="ffmpeg_filters",
            name="FFmpeg filters",
            status="fail",
            detail=f"This FFmpeg build lacks: {listed}. Videos cannot be rendered with it.",
            fix_hint=FULL_BUILD_HINT,
        )
    return CheckResult(
        id="ffmpeg_filters",
        name="FFmpeg filters",
        status="ok",
        detail="libass, xfade, zoompan, sidechaincompress and loudnorm are available.",
    )


def check_fonts() -> CheckResult:
    """The Noto fonts in ``assets/fonts`` that captions and popups draw with."""
    from .render.fonts import FONT_FILES, fonts_dir, licence_present, missing_fonts

    folder = fonts_dir()
    if not folder.is_dir():
        return CheckResult(
            id="fonts",
            name="Fonts",
            status="fail",
            detail=f"The fonts folder {folder} is missing. Captions and popups need it.",
            fix_hint="Reinstall the app, or copy the assets/fonts folder from the repository "
            "(or set CCS_FONTS_DIR to a folder with the Noto fonts).",
        )
    missing = missing_fonts()
    if len(missing) == len(FONT_FILES):
        return CheckResult(
            id="fonts",
            name="Fonts",
            status="fail",
            detail=f"No Noto font was found in {folder}.",
            fix_hint="Copy the assets/fonts folder from the repository into the app folder.",
        )
    if missing:
        return CheckResult(
            id="fonts",
            name="Fonts",
            status="warn",
            detail=f"Missing in {folder}: {', '.join(missing)}. Captions in those languages "
            "fall back to a system font.",
            fix_hint="Copy the missing font files from the repository's assets/fonts folder.",
        )
    licence = "" if licence_present() else " (the OFL.txt licence file is missing)"
    return CheckResult(
        id="fonts",
        name="Fonts",
        status="ok",
        detail=f"{len(FONT_FILES)} Noto fonts in {folder}{licence}.",
    )


def check_deno() -> CheckResult:
    found = shutil.which("deno")
    if not found:
        return CheckResult(
            id="deno",
            name="Deno",
            status="warn",
            detail="Deno was not found. YouTube downloads (yt-dlp) need it.",
            fix_hint="Install Deno (for example: winget install DenoLand.Deno) and restart "
            "the app.",
        )
    return CheckResult(
        id="deno",
        name="Deno",
        status="ok",
        detail=f"{_run_version(found, '--version')} ({found})",
    )


def check_yt_dlp() -> CheckResult:
    if importlib.util.find_spec("yt_dlp") is None:
        return CheckResult(
            id="yt_dlp",
            name="yt-dlp",
            status="warn",
            detail="The yt-dlp module is not installed. Competitor research needs it.",
            fix_hint="Run: python -m pip install yt-dlp (inside the app's Python).",
        )
    try:
        version = importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown version"
    return CheckResult(id="yt_dlp", name="yt-dlp", status="ok", detail=f"yt-dlp {version}")


def webview2_version() -> str | None:
    """Version of the Microsoft Edge WebView2 runtime from the registry, or ``None``."""
    if sys.platform != "win32":
        return None
    import winreg

    client = rf"Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_ID}"
    locations = (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{client}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{client}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{client}"),
    )
    for root, subkey in locations:
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        text = str(value).strip()
        if text and text != "0.0.0.0":
            return text
    return None


def check_webview2() -> CheckResult:
    if sys.platform != "win32":
        return CheckResult(
            id="webview2",
            name="WebView2 runtime",
            status="ok",
            detail=f"Not needed on {platform.system()}.",
        )
    version = webview2_version()
    if version:
        return CheckResult(
            id="webview2",
            name="WebView2 runtime",
            status="ok",
            detail=f"Microsoft Edge WebView2 {version}",
        )
    return CheckResult(
        id="webview2",
        name="WebView2 runtime",
        status="warn",
        detail="The Microsoft Edge WebView2 runtime was not found. The app will open in your "
        "web browser instead of its own window.",
        fix_hint=f"Install the Evergreen WebView2 runtime from {WEBVIEW2_DOWNLOAD}",
    )


def check_shared_dir(settings: Settings) -> CheckResult:
    folder = settings.resolved_shared_dir
    ok, detail = _writable(folder)
    if not ok:
        return CheckResult(
            id="shared_dir",
            name="Shared folder",
            status="fail",
            detail=detail,
            fix_hint="Pick a folder you can write to in Settings > Paths, for example the "
            "synced Google Drive folder.",
        )
    if settings.shared_dir_is_default:
        return CheckResult(
            id="shared_dir",
            name="Shared folder",
            status="warn",
            detail=f"No shared folder is set, so channels are kept locally in {folder}.",
            fix_hint="In Settings > Paths choose the team's synced Google Drive folder so "
            "everyone sees the same channels.",
        )
    return CheckResult(id="shared_dir", name="Shared folder", status="ok", detail=detail)


def _check_folder(check_id: str, name: str, folder: Path) -> CheckResult:
    ok, detail = _writable(folder)
    if ok:
        return CheckResult(id=check_id, name=name, status="ok", detail=detail)
    return CheckResult(
        id=check_id,
        name=name,
        status="fail",
        detail=detail,
        fix_hint="Pick a folder on a local drive you can write to in Settings > Paths.",
    )


def check_projects_dir(settings: Settings) -> CheckResult:
    return _check_folder("projects_dir", "Projects folder", settings.projects_dir)


def check_exports_dir(settings: Settings) -> CheckResult:
    return _check_folder("exports_dir", "Exports folder", settings.exports_dir)


def check_anthropic_key(settings: Settings | None = None) -> CheckResult:
    if "ANTHROPIC_API_KEY" in _unloadable_keys(settings):
        return CheckResult(
            id="anthropic_key",
            name="Anthropic API key",
            status="warn",
            detail="ANTHROPIC_API_KEY is saved but could not be loaded: the saved value "
            "contains characters that cannot be used.",
            fix_hint=UNLOADABLE_FIX,
        )
    if _key_set("ANTHROPIC_API_KEY"):
        return CheckResult(
            id="anthropic_key", name="Anthropic API key", status="ok", detail="Set."
        )
    return CheckResult(
        id="anthropic_key",
        name="Anthropic API key",
        status="warn",
        detail="ANTHROPIC_API_KEY is not set. Scripts and titles need it.",
        fix_hint="Add your Anthropic API key in Settings > API keys.",
    )


def _check_key_group(
    check_id: str,
    name: str,
    names: tuple[str, ...],
    what: str,
    settings: Settings | None = None,
) -> CheckResult:
    broken = [key for key in names if key in _unloadable_keys(settings)]
    if broken:
        return CheckResult(
            id=check_id,
            name=name,
            status="warn",
            detail=f"Saved but could not be loaded: {', '.join(broken)}. The saved value "
            "contains characters that cannot be used.",
            fix_hint=UNLOADABLE_FIX,
        )
    present = [key for key in names if _key_set(key)]
    if present:
        return CheckResult(
            id=check_id, name=name, status="ok", detail="Set: " + ", ".join(present)
        )
    return CheckResult(
        id=check_id,
        name=name,
        status="warn",
        detail=f"No {what} key is set (none of {', '.join(names)}).",
        fix_hint=f"Add the key for your {what} tool in Settings > API keys.",
    )


def check_voice_key(settings: Settings | None = None) -> CheckResult:
    return _check_key_group("voice_key", "Voice API key", VOICE_KEYS, "voice", settings)


def check_image_key(settings: Settings | None = None) -> CheckResult:
    return _check_key_group("image_key", "Image API key", IMAGE_KEYS, "image", settings)


def check_disk_space(settings: Settings) -> CheckResult:
    target = _nearest_existing(settings.projects_dir)
    usage = shutil.disk_usage(target)
    free_gb = usage.free / 1024**3
    detail = f"{free_gb:.1f} GB free on the drive of {settings.projects_dir}."
    if free_gb >= MIN_FREE_GB:
        return CheckResult(id="disk_space", name="Disk space", status="ok", detail=detail)
    return CheckResult(
        id="disk_space",
        name="Disk space",
        status="warn",
        detail=detail + f" At least {MIN_FREE_GB} GB is recommended for video work.",
        fix_hint="Free up space or move the projects folder to a bigger drive in "
        "Settings > Paths.",
    )


# Runner -------------------------------------------------------------------------------------


def _safe(check_id: str, name: str, fn: Callable[[], CheckResult]) -> CheckResult:
    try:
        return fn()
    except Exception as exc:  # a check must never take the app down
        return CheckResult(
            id=check_id,
            name=name,
            status="fail",
            detail=f"{type(exc).__name__}: {exc}",
            fix_hint="This check hit an unexpected error. Restart the app and try again.",
        )


def run_all(settings: Settings) -> DoctorReport:
    """Run every check in the order the contract lists them."""
    checks = [
        _safe("python_version", "Python", check_python_version),
        _safe("ffmpeg", "FFmpeg", lambda: check_ffmpeg(settings)),
        _safe("ffprobe", "FFprobe", lambda: check_ffprobe(settings)),
        _safe("ffmpeg_filters", "FFmpeg filters", lambda: check_ffmpeg_filters(settings)),
        _safe("fonts", "Fonts", check_fonts),
        _safe("deno", "Deno", check_deno),
        _safe("yt_dlp", "yt-dlp", check_yt_dlp),
        _safe("webview2", "WebView2 runtime", check_webview2),
        _safe("shared_dir", "Shared folder", lambda: check_shared_dir(settings)),
        _safe("projects_dir", "Projects folder", lambda: check_projects_dir(settings)),
        _safe("exports_dir", "Exports folder", lambda: check_exports_dir(settings)),
        _safe("anthropic_key", "Anthropic API key", lambda: check_anthropic_key(settings)),
        _safe("voice_key", "Voice API key", lambda: check_voice_key(settings)),
        _safe("image_key", "Image API key", lambda: check_image_key(settings)),
        _safe("disk_space", "Disk space", lambda: check_disk_space(settings)),
    ]
    return DoctorReport(ok=all(check.status != "fail" for check in checks), checks=checks)

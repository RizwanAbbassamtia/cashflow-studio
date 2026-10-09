"""``config/render.yaml`` as Python objects: frame sizes per preset, encoder settings, audio
loudness targets, ducking, popup and caption sizes.

Everything has a built-in default, so a missing or partial file still renders. Settings >
Render decides the x264 speed, which presets a project renders by default and whether 4K is
allowed; those arrive through the stage, not from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from ..llm.config import load_yaml

RENDER_FILE = "render.yaml"
X264_PRESETS: tuple[str, ...] = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
)
DEFAULT_X264_PRESET = "medium"
PROXY_ID = "proxy"


def _even(value: float) -> int:
    rounded = int(round(value))
    return max(rounded - (rounded % 2), 2)


def _pair(value: Any, fallback: tuple[int, int]) -> tuple[int, int]:
    if isinstance(value, list | tuple) and len(value) == 2:
        try:
            return _even(int(value[0])), _even(int(value[1]))
        except (TypeError, ValueError):
            return fallback
    return fallback


def _float(data: dict[str, Any], key: str, fallback: float) -> float:
    try:
        return float(data.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def _int(data: dict[str, Any], key: str, fallback: int) -> int:
    try:
        return int(data.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class RenderPreset:
    id: str
    landscape: tuple[int, int]
    portrait: tuple[int, int]
    crf: int = 20
    x264_preset: str | None = None
    """``None`` means the configured default (Settings > Render)."""
    canvas_scale: float = 1.5
    max_canvas_px: int = 2880
    requires_enable_4k: bool = False

    def size(self, aspect: str) -> tuple[int, int]:
        return self.portrait if aspect == "9:16" else self.landscape

    def canvas(self, aspect: str) -> tuple[int, int]:
        """The working size the pictures are fitted to before the camera move: the frame
        times ``canvas_scale``, capped at ``max_canvas_px`` on the long side."""
        width, height = self.size(aspect)
        scale = max(1.0, float(self.canvas_scale))
        long_side = max(width, height) * scale
        if long_side > self.max_canvas_px:
            scale = self.max_canvas_px / max(width, height)
        return _even(width * scale), _even(height * scale)


@dataclass(frozen=True)
class PopupLook:
    """Popup sizes at 1080p (scaled by ``min(width, height) / 1080`` per preset)."""

    font_px: int = 64
    padding_px: int = 24
    radius_px: int = 18
    margin_px: int = 60
    max_width_share: float = 0.8
    fade_s: float = 0.3
    slide_s: float = 0.35
    slide_px: int = 120
    lead_s: float = 0.3
    max_show_s: float = 4.0
    end_margin_s: float = 0.2
    min_show_s: float = 0.6


@dataclass(frozen=True)
class RenderConfig:
    fps: int = 30
    tail_s: float = 0.8
    scale_flags: str = "lanczos"
    x264_preset: str = DEFAULT_X264_PRESET
    crf: int = 20
    x264_profile: str = "high"
    pix_fmt: str = "yuv420p"
    movflags: str = "+faststart"
    audio_codec: str = "aac"
    audio_bitrate_kbps: int = 192
    audio_sample_rate: int = 48000
    loudnorm_i: float = -16.0
    loudnorm_tp: float = -1.5
    loudnorm_lra: float = 11.0
    duck_threshold: float = 0.03
    duck_ratio: float = 8.0
    duck_attack_ms: float = 300.0
    duck_release_ms: float = 800.0
    music_gain_db: float = -18.0
    music_fade_in_s: float = 1.0
    music_fade_out_s: float = 2.0
    transition_max_share: float = 0.25
    transition_min_frames: int = 2
    captions_margin_v_px: int = 60
    captions_outline: int = 2
    captions_shadow: int = 0
    popups: PopupLook = PopupLook()
    presets: dict[str, RenderPreset] = field(default_factory=dict)
    proxy: RenderPreset = RenderPreset(
        PROXY_ID, (854, 480), (480, 854), crf=30, x264_preset="ultrafast", canvas_scale=1.0,
        max_canvas_px=1280,
    )

    def preset(self, preset_id: str) -> RenderPreset:
        if preset_id == PROXY_ID:
            return self.proxy
        try:
            return self.presets[preset_id]
        except KeyError:
            choices = ", ".join(self.presets)
            raise ValueError(
                f"'{preset_id}' is not a render size this app knows. Choose one of: {choices}."
            ) from None

    def available_presets(self, enable_4k: bool = False) -> list[str]:
        return [p.id for p in self.presets.values() if enable_4k or not p.requires_enable_4k]

    def x264_preset_for(self, preset: RenderPreset, override: str | None = None) -> str:
        if preset.x264_preset:
            return preset.x264_preset
        if override and override in X264_PRESETS:
            return override
        return self.x264_preset


DEFAULT_PRESETS: dict[str, RenderPreset] = {
    "720p": RenderPreset("720p", (1280, 720), (720, 1280), 20, None, 2.0, 2560),
    "1080p": RenderPreset("1080p", (1920, 1080), (1080, 1920), 20, None, 1.5, 2880),
    "2160p": RenderPreset("2160p", (3840, 2160), (2160, 3840), 20, None, 1.0, 3840, True),
}


def _preset_from(preset_id: str, raw: Any, fallback: RenderPreset) -> RenderPreset:
    data = raw if isinstance(raw, dict) else {}
    x264 = data.get("x264_preset")
    return RenderPreset(
        id=preset_id,
        landscape=_pair(data.get("landscape"), fallback.landscape),
        portrait=_pair(data.get("portrait"), fallback.portrait),
        crf=_int(data, "crf", fallback.crf),
        x264_preset=str(x264) if x264 in X264_PRESETS else fallback.x264_preset,
        canvas_scale=_float(data, "canvas_scale", fallback.canvas_scale),
        max_canvas_px=_int(data, "max_canvas_px", fallback.max_canvas_px),
        requires_enable_4k=bool(data.get("requires_enable_4k", fallback.requires_enable_4k)),
    )


def config_from(data: dict[str, Any]) -> RenderConfig:
    """A :class:`RenderConfig` from the parsed YAML mapping (defaults fill every gap)."""
    base = RenderConfig()
    x264 = data.get("x264") if isinstance(data.get("x264"), dict) else {}
    audio = data.get("audio") if isinstance(data.get("audio"), dict) else {}
    loudnorm = audio.get("loudnorm") if isinstance(audio.get("loudnorm"), dict) else {}
    ducking = data.get("ducking") if isinstance(data.get("ducking"), dict) else {}
    music = data.get("music") if isinstance(data.get("music"), dict) else {}
    transitions = data.get("transitions") if isinstance(data.get("transitions"), dict) else {}
    captions = data.get("captions") if isinstance(data.get("captions"), dict) else {}
    popups_raw = data.get("popups") if isinstance(data.get("popups"), dict) else {}
    look = PopupLook()
    popups = PopupLook(
        font_px=_int(popups_raw, "font_px", look.font_px),
        padding_px=_int(popups_raw, "padding_px", look.padding_px),
        radius_px=_int(popups_raw, "radius_px", look.radius_px),
        margin_px=_int(popups_raw, "margin_px", look.margin_px),
        max_width_share=_float(popups_raw, "max_width_share", look.max_width_share),
        fade_s=_float(popups_raw, "fade_s", look.fade_s),
        slide_s=_float(popups_raw, "slide_s", look.slide_s),
        slide_px=_int(popups_raw, "slide_px", look.slide_px),
        lead_s=_float(popups_raw, "lead_s", look.lead_s),
        max_show_s=_float(popups_raw, "max_show_s", look.max_show_s),
        end_margin_s=_float(popups_raw, "end_margin_s", look.end_margin_s),
        min_show_s=_float(popups_raw, "min_show_s", look.min_show_s),
    )
    presets_raw = data.get("presets") if isinstance(data.get("presets"), dict) else {}
    presets: dict[str, RenderPreset] = {}
    for preset_id, fallback in DEFAULT_PRESETS.items():
        presets[preset_id] = _preset_from(preset_id, presets_raw.get(preset_id), fallback)
    for preset_id, raw in presets_raw.items():
        key = str(preset_id)
        if key not in presets and key != PROXY_ID:
            presets[key] = _preset_from(key, raw, DEFAULT_PRESETS["1080p"])
    x264_preset = str(x264.get("preset") or base.x264_preset)
    if x264_preset not in X264_PRESETS:
        x264_preset = base.x264_preset
    return RenderConfig(
        fps=max(1, _int(data, "fps", base.fps)),
        tail_s=max(0.0, _float(data, "tail_s", base.tail_s)),
        scale_flags=str(data.get("scale_flags") or base.scale_flags),
        x264_preset=x264_preset,
        crf=_int(x264, "crf", base.crf),
        x264_profile=str(x264.get("profile") or base.x264_profile),
        pix_fmt=str(x264.get("pix_fmt") or base.pix_fmt),
        movflags=str(x264.get("movflags") or base.movflags),
        audio_codec=str(audio.get("codec") or base.audio_codec),
        audio_bitrate_kbps=_int(audio, "bitrate_kbps", base.audio_bitrate_kbps),
        audio_sample_rate=_int(audio, "sample_rate", base.audio_sample_rate),
        loudnorm_i=_float(loudnorm, "i", base.loudnorm_i),
        loudnorm_tp=_float(loudnorm, "tp", base.loudnorm_tp),
        loudnorm_lra=_float(loudnorm, "lra", base.loudnorm_lra),
        duck_threshold=_float(ducking, "threshold", base.duck_threshold),
        duck_ratio=_float(ducking, "ratio", base.duck_ratio),
        duck_attack_ms=_float(ducking, "attack_ms", base.duck_attack_ms),
        duck_release_ms=_float(ducking, "release_ms", base.duck_release_ms),
        music_gain_db=_float(music, "gain_db", base.music_gain_db),
        music_fade_in_s=_float(music, "fade_in_s", base.music_fade_in_s),
        music_fade_out_s=_float(music, "fade_out_s", base.music_fade_out_s),
        transition_max_share=_float(
            transitions, "max_share_of_shorter_scene", base.transition_max_share
        ),
        transition_min_frames=_int(transitions, "min_frames", base.transition_min_frames),
        captions_margin_v_px=_int(captions, "margin_v_px", base.captions_margin_v_px),
        captions_outline=_int(captions, "outline", base.captions_outline),
        captions_shadow=_int(captions, "shadow", base.captions_shadow),
        popups=popups,
        presets=presets,
        proxy=_preset_from(PROXY_ID, data.get("proxy"), base.proxy),
    )


@lru_cache(maxsize=1)
def load_render_config() -> RenderConfig:
    return config_from(load_yaml(RENDER_FILE))


def clear_caches() -> None:
    load_render_config.cache_clear()

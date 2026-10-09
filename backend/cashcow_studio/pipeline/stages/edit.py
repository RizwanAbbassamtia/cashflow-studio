"""Edit stage: the storyboard, the voice timing and the pictures become ``07_edit/timeline.json``
and rendered videos (``proxy.mp4`` always, ``final_<preset>.mp4`` per requested preset).

Files first (docs/M3-M4-CONTRACT.md section 3): the timeline, ``captions.ass``, the popup
PNGs and ``render.log`` are written before the stage is marked done. ``timeline.json`` is the
source of truth: it is built from the earlier stages' files, reused on a later run when
nothing it was built from changed (so a person may edit it by hand and press Run), and
replaced whole by the ``timeline`` edit. A finished render is kept while what it was made
from is unchanged (the timeline without its preset list, the popup switch, the x264 speed and
the source files, hashed into ``render_key`` in ``render_state.json``), so adding a preset
only renders the new size; only missing or stale renders are made again.

Edits (approve or redo): ``{presets: [...]}``, ``{music_path}`` (``""`` = no music),
``{captions_enabled}``, ``{popups_enabled}``, ``{timeline: Timeline}``, ``{rebuild: true}``.
Gates: ``edit.music_license`` (warn), ``edit.duration`` (block), ``edit.audio_peaks`` (warn).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ...models.project import StageName
from ...models.storyboard import StoryboardDoc
from ...models.timeline import (
    TIMELINE_FILE,
    EditApproveEdits,
    EditReviewPayload,
    EditSceneView,
    RenderOutput,
    Timeline,
    TimelineSummary,
)
from ...policy import gates
from ...render import ffmpeg as ff
from ...render.captions import ASS_FILE, rules_for, write_ass
from ...render.music import has_licence, resolve_track, track_infos
from ...render.popups import brand_colour, popup_position, popup_scale, render_popup_png
from ...render.presets import PROXY_ID, RenderConfig, RenderPreset, load_render_config
from ...render.timeline_builder import (
    BuildRequest,
    SceneSource,
    TimelineBuildError,
    Timing,
    build_timeline,
    read_timing,
    resolve_scene_images,
)
from ...storage.settings_store import atomic_write_text
from .base import GateBlocked, StageContext, StageError, StageResult, run_in_thread
from .storyboard import read_storyboard

log = logging.getLogger(__name__)

STAGE_DIR_NAME = "07_edit"
STATE_FILE = "render_state.json"
RENDER_LOG = "render.log"
REVIEW_PAYLOAD_FILE = "review_payload.json"
POPUPS_DIR = "popups"
PROXY_FILE = "proxy.mp4"
DEFAULT_PRESET = "1080p"


def final_file_name(preset_id: str) -> str:
    return PROXY_FILE if preset_id == PROXY_ID else f"final_{preset_id}.mp4"


def file_url(project_id: str, relative: str) -> str:
    return f"/api/projects/{project_id}/files/{relative.replace(chr(92), '/')}"


def utc_stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# Sources -----------------------------------------------------------------------------------------


@dataclass
class Sources:
    storyboard: StoryboardDoc
    timing: Timing
    images: list[SceneSource]
    voice: Path
    newest_mtime: float


def load_sources(folder: Path) -> Sources:
    """Everything the timeline is built from, or a plain-English :class:`StageError`."""
    folder = Path(folder)
    storyboard = read_storyboard(folder / "04_storyboard")
    if storyboard is None:
        raise StageError("The storyboard step has not produced a storyboard yet. Finish it first.")
    if not storyboard.scenes:
        raise StageError("The storyboard has no scenes, so there is nothing to edit.")
    voice_dir = folder / "05_voice"
    try:
        timing = read_timing(voice_dir / "timing.json")
        images = resolve_scene_images(folder, storyboard)
    except TimelineBuildError as exc:
        raise StageError(str(exc)) from exc
    voice = voice_dir / "voice.wav"
    if not voice.is_file():
        raise StageError(
            "The voice step has not produced 05_voice/voice.wav yet. Finish it first (or put "
            "your own recording there as voice.wav)."
        )
    watched = [
        folder / "04_storyboard" / "storyboard.json",
        voice_dir / "timing.json",
        voice,
        folder / "06_images" / "images.json",
        *[source.image_abs for source in images],
    ]
    newest = 0.0
    for path in watched:
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return Sources(storyboard, timing, images, voice, newest)


# State on disk -----------------------------------------------------------------------------


def read_state(stage_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((stage_dir / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(stage_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_stamp()
    atomic_write_text(stage_dir / STATE_FILE, json.dumps(state, indent=2, default=str) + "\n")


def read_timeline(stage_dir: Path) -> Timeline | None:
    path = stage_dir / TIMELINE_FILE
    if not path.is_file():
        return None
    try:
        return Timeline.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        log.warning("timeline.json could not be read and will be rebuilt: %s", exc)
        return None


def write_timeline(stage_dir: Path, timeline: Timeline) -> tuple[Path, bool]:
    """Write ``timeline.json`` only when its content changed. Returns ``(path, changed)``."""
    path = stage_dir / TIMELINE_FILE
    text = timeline.model_dump_json(indent=2) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return path, False
    except OSError:
        pass
    atomic_write_text(path, text)
    return path, True


def render_key_for(
    timeline: Timeline,
    *,
    popups_enabled: bool,
    x264_preset: str | None,
    sources_mtime: float,
) -> str:
    """A short hash of everything a finished render depends on: the timeline without its
    preset list (adding a preset must not throw finished files away), the popup switch, the
    x264 speed and the newest source file (a replaced picture or voice renders again)."""
    content = timeline.model_dump(mode="json", exclude={"presets"})
    blob = json.dumps(
        {
            "timeline": content,
            "popups": bool(popups_enabled),
            "x264": x264_preset or "",
            "sources": int(sources_mtime),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# Settings ----------------------------------------------------------------------------------------


@dataclass
class EditOptions:
    default_presets: list[str] = field(default_factory=lambda: [DEFAULT_PRESET])
    x264_preset: str | None = None
    enable_4k: bool = False
    captions_enabled: bool = True
    caption_style: str = "bold-white"


def options_from(settings: Any) -> EditOptions:
    render = getattr(settings, "render", None)
    captions = getattr(settings, "captions", None)
    options = EditOptions()
    presets = getattr(render, "default_presets", None)
    if isinstance(presets, list | tuple) and presets:
        options.default_presets = [str(p) for p in presets]
    x264 = getattr(render, "x264_preset", None)
    options.x264_preset = str(x264) if isinstance(x264, str) and x264 else None
    options.enable_4k = bool(getattr(render, "enable_4k", False))
    enabled = getattr(captions, "enabled", None)
    if isinstance(enabled, bool):
        options.captions_enabled = enabled
    style = getattr(captions, "style", None)
    if isinstance(style, str) and style:
        options.caption_style = style
    return options


def allowed_presets(
    wanted: list[str], config: RenderConfig, enable_4k: bool, warnings: list[str]
) -> list[str]:
    """The presets of ``wanted`` this app can render; unknown ones raise, 4K without the
    setting is dropped with a warning."""
    result: list[str] = []
    for preset_id in wanted:
        name = str(preset_id).strip()
        if not name or name in result:
            continue
        if name == PROXY_ID:
            continue
        if name not in config.presets:
            choices = ", ".join(config.presets)
            raise StageError(
                f"'{name}' is not a render size this app knows. Choose one of: {choices}."
            )
        if config.presets[name].requires_enable_4k and not enable_4k:
            warnings.append(
                f"{name} was not rendered: 4K is switched off in Settings > Render."
            )
            continue
        result.append(name)
    return result


# Popups ------------------------------------------------------------------------------------------


def popup_assets(
    timeline: Timeline,
    preset: RenderPreset,
    *,
    stage_dir: Path,
    config: RenderConfig,
    box_colour: str,
    font_family: str,
    captions_on: bool,
    enabled: bool,
) -> list[ff.PopupAsset]:
    """The popup PNGs for one preset, positioned in its frame (above the caption area)."""
    if not enabled or not timeline.popups:
        return []
    width, height = preset.size(timeline.aspect)
    scale = popup_scale(width, height)
    look = config.popups
    reserved = 0
    if captions_on and timeline.captions.cues:
        style = timeline.captions.style
        reserved = round((style.size * style.max_lines * 1.4 + config.captions_margin_v_px) * scale)
    out_dir = stage_dir / POPUPS_DIR / preset.id
    assets: list[ff.PopupAsset] = []
    for number, popup in enumerate(timeline.popups):
        png = out_dir / f"popup_{number:02d}.png"
        popup_w, popup_h = render_popup_png(
            popup.text, png, scale=scale, look=look, box_colour=box_colour,
            font_family=font_family, frame_width=width,
        )
        x, y = popup_position(
            popup.position, (popup_w, popup_h), (width, height),
            margin_px=round(look.margin_px * scale), bottom_reserved_px=reserved,
        )
        assets.append(ff.PopupAsset(popup, png, popup_w, popup_h, x, y))
    return assets


# Review payload --------------------------------------------------------------------------------


def _render_rows(
    project_id: str, timeline: Timeline, state: dict[str, Any], config: RenderConfig,
    enable_4k: bool,
) -> list[RenderOutput]:
    renders = state.get("renders") if isinstance(state.get("renders"), dict) else {}
    rows: list[RenderOutput] = []
    for preset_id in [PROXY_ID, *config.available_presets(enable_4k=True)]:
        raw = renders.get(preset_id) if isinstance(renders.get(preset_id), dict) else {}
        path = raw.get("path") if isinstance(raw.get("path"), str) else None
        status = str(raw.get("status") or "not_rendered")
        if status == "done" and not path:
            status = "not_rendered"
        if preset_id not in timeline.presets and preset_id != PROXY_ID and status == "not_rendered":
            if config.presets[preset_id].requires_enable_4k and not enable_4k:
                continue
        rows.append(
            RenderOutput(
                preset=preset_id,
                status=status,  # type: ignore[arg-type]
                path=path,
                play_url=file_url(project_id, path) if path and status == "done" else None,
                progress=100.0 if status == "done" else None,
                error=raw.get("error") if isinstance(raw.get("error"), str) else None,
                duration_s=raw.get("duration_s"),
                size_bytes=raw.get("size_bytes"),
                width=raw.get("width"),
                height=raw.get("height"),
                rendered_at=raw.get("rendered_at"),
            )
        )
    return rows


def build_payload(
    project_id: str,
    timeline: Timeline,
    state: dict[str, Any],
    *,
    config: RenderConfig,
    enable_4k: bool,
    music_folder: str | None,
    warnings: list[str],
    gate_results: list[dict[str, Any]],
    timing_source: str | None = None,
) -> dict[str, Any]:
    renders = _render_rows(project_id, timeline, state, config, enable_4k)
    proxy = next((r for r in renders if r.preset == PROXY_ID and r.status == "done"), None)
    popup_by_scene: dict[int, str] = {}
    for popup in timeline.popups:
        popup_by_scene.setdefault(popup.scene, popup.text)
    scenes = [
        EditSceneView(
            index=scene.index,
            start_s=scene.start_s,
            end_s=scene.end_s,
            duration_s=round(scene.end_s - scene.start_s, 3),
            image_path=scene.image,
            image_url=file_url(project_id, scene.image),
            transition=scene.transition_out.type,
            transition_s=scene.transition_out.duration_s,
            motion=scene.motion.preset,
            popup_text=popup_by_scene.get(scene.index),
            locked=scene.locked,
        )
        for scene in timeline.scenes
    ]
    payload = EditReviewPayload(
        proxy_path=proxy.path if proxy else None,
        proxy_url=proxy.play_url if proxy else None,
        timeline=timeline,
        summary=TimelineSummary(
            scene_count=len(timeline.scenes),
            duration_s=timeline.duration_s,
            transitions=timeline.transition_types,
            popup_count=len(timeline.popups),
            caption_cues=len(timeline.captions.cues),
            fps=timeline.fps,
            width=timeline.width,
            height=timeline.height,
            aspect=timeline.aspect,
        ),
        scenes=scenes,
        renders=renders,
        presets=list(timeline.presets),
        available_presets=config.available_presets(enable_4k=True),
        enable_4k=enable_4k,
        music_tracks=track_infos(music_folder),
        music_path=timeline.music.path,
        music_license_ok=timeline.music.license_ok,
        captions_enabled=timeline.captions.enabled,
        popups_enabled=bool(state.get("popups_enabled", True)),
        warnings=list(warnings),
        gate_results=list(gate_results),
        render_log_path=f"{STAGE_DIR_NAME}/{RENDER_LOG}",
        timing_source=timing_source,
    )
    return payload.model_dump(mode="json")


def load_review_payload(folder: Path, project_id: str, music_folder: str | None = None,
                        enable_4k: bool = False) -> dict[str, Any]:
    """The review payload rebuilt from the files (for a server restart or a test)."""
    stage_dir = Path(folder) / STAGE_DIR_NAME
    timeline = read_timeline(stage_dir)
    if timeline is None:
        return {}
    state = read_state(stage_dir)
    return build_payload(
        project_id, timeline, state, config=load_render_config(), enable_4k=enable_4k,
        music_folder=music_folder, warnings=list(state.get("warnings") or []),
        gate_results=list(state.get("gate_results") or []),
    )


# The stage -----------------------------------------------------------------------------------


class EditStage:
    name = StageName.edit

    async def run(self, ctx: StageContext) -> StageResult:
        return await self._produce(ctx, dict(ctx.edits or {}))

    async def apply_edits(self, ctx: StageContext) -> StageResult | None:
        """Approval edits: apply them to the timeline and render what is missing."""
        if not ctx.edits:
            return None
        return await self._produce(ctx, dict(ctx.edits))

    async def _produce(self, ctx: StageContext, raw_edits: dict[str, Any]) -> StageResult:
        started = time.monotonic()
        edits = _parse_edits(raw_edits)
        project = ctx.project
        stage_dir = ctx.stage_dir(StageName.edit)
        config = load_render_config()
        options = options_from(ctx.settings)
        warnings: list[str] = []
        notes: list[str] = []

        await ctx.report("Reading the storyboard, the voice timing and the pictures", 2)
        sources = await asyncio.to_thread(load_sources, ctx.folder)
        state = read_state(stage_dir)
        if edits.popups_enabled is not None:
            state["popups_enabled"] = bool(edits.popups_enabled)
        popups_enabled = bool(state.get("popups_enabled", True))

        timeline, how = self._decide_timeline(ctx, edits, sources, stage_dir, config, options,
                                              warnings)
        notes.append(how)
        self._apply_simple_edits(ctx, edits, timeline, config, options, warnings)
        if not timeline.scenes:
            raise StageError("The timeline has no scenes.")
        for number, popup in enumerate(timeline.popups):
            popup.png = f"{STAGE_DIR_NAME}/{POPUPS_DIR}/ref/popup_{number:02d}.png"
        timeline_path, changed = write_timeline(stage_dir, timeline)
        if changed:
            notes.append("timeline.json written.")

        await ctx.report("Writing the captions and popups", 6)
        ass_path = stage_dir / ASS_FILE
        if timeline.captions.cues:
            write_ass(
                ass_path, timeline.captions.cues, timeline.captions.style, timeline.aspect,
                margin_v_px=config.captions_margin_v_px, shadow=config.captions_shadow,
            )
        language = str(project.language)
        font_family = rules_for(language).popup_font
        box_colour = brand_colour(list(ctx.channel.channel.brand_colors))
        reference = RenderPreset("ref", (1920, 1080), (1080, 1920))
        await run_in_thread(
            ctx,
            partial(
                popup_assets, timeline, reference, stage_dir=stage_dir, config=config,
                box_colour=box_colour, font_family=font_family,
                captions_on=timeline.captions.enabled, enabled=True,
            ),
        )

        if ctx.cancelled:
            raise StageError("The edit step was stopped before rendering started.")
        targets = [config.proxy] + [config.presets[p] for p in timeline.presets]
        render_key = render_key_for(
            timeline, popups_enabled=popups_enabled, x264_preset=options.x264_preset,
            sources_mtime=sources.newest_mtime,
        )
        renders_state: dict[str, Any] = (
            state.get("renders") if isinstance(state.get("renders"), dict) else {}
        )
        state["renders"] = renders_state
        to_render: list[RenderPreset] = []
        for preset in targets:
            output = stage_dir / final_file_name(preset.id)
            entry = renders_state.get(preset.id)
            if not isinstance(entry, dict):
                entry = {}
            fresh = (
                output.is_file()
                and entry.get("status") == "done"
                and entry.get("render_key") == render_key
            )
            if fresh:
                notes.append(f"{output.name} is up to date and was kept.")
            else:
                to_render.append(preset)
        loop = asyncio.get_running_loop()

        def report(message: str, pct: float) -> None:
            try:
                asyncio.run_coroutine_threadsafe(ctx.report(message, pct), loop)
            except RuntimeError:
                ctx.progress(message, pct)

        results = await run_in_thread(
            ctx,
            partial(
                self._render_all, ctx, timeline, to_render, stage_dir, config, options,
                box_colour, font_family, popups_enabled, state, report, render_key,
            ),
        )
        state.setdefault("popups_enabled", popups_enabled)
        timing_source = sources.timing.source

        # Gates ------------------------------------------------------------------------
        rendered = [
            {
                "preset": pid,
                "expected_s": entry.get("expected_s"),
                "actual_s": entry.get("duration_s"),
            }
            for pid, entry in renders_state.items()
            if isinstance(entry, dict) and entry.get("status") == "done"
            and (pid == PROXY_ID or pid in timeline.presets)
        ]
        gate_context = {
            "music_path": timeline.music.path,
            "music_license_ok": timeline.music.license_ok,
            "renders": rendered,
            "true_peak_dbtp": state.get("true_peak_dbtp"),
        }
        gate_results = gates.to_dicts(gates.evaluate("edit", gate_context))
        state["gate_results"] = gate_results
        state["warnings"] = warnings
        write_state(stage_dir, state)
        payload = build_payload(
            project.id, timeline, state, config=config, enable_4k=options.enable_4k,
            music_folder=ctx.channel.channel.music_folder or None, warnings=warnings,
            gate_results=gate_results, timing_source=timing_source,
        )
        blocking = gates.blocking_reasons(gates.evaluate("edit", gate_context))
        if blocking and not edits.override_gates:
            atomic_write_text(
                stage_dir / REVIEW_PAYLOAD_FILE, json.dumps(payload, indent=2, default=str) + "\n"
            )
            raise GateBlocked(blocking)
        outputs = [timeline_path] + [
            stage_dir / final_file_name(preset.id) for preset in targets
            if (stage_dir / final_file_name(preset.id)).is_file()
        ]
        if timeline.captions.cues:
            outputs.append(ass_path)
        elapsed = time.monotonic() - started
        made = ", ".join(r.path.name for r in results) or "nothing new"
        await ctx.report("Edit ready for review", 100)
        return StageResult(
            outputs=outputs,
            summary=(
                f"{len(timeline.scenes)} scenes, {timeline.duration_s:g} s, "
                f"{len(timeline.popups)} popups, {len(timeline.captions.cues)} caption cues; "
                f"rendered {made} in {elapsed:.0f} s."
            ),
            needs_review_payload=payload,
            gate_results=gate_results,
        )

    # Decisions ------------------------------------------------------------------------

    def _decide_timeline(
        self,
        ctx: StageContext,
        edits: EditApproveEdits,
        sources: Sources,
        stage_dir: Path,
        config: RenderConfig,
        options: EditOptions,
        warnings: list[str],
    ) -> tuple[Timeline, str]:
        project = ctx.project
        if edits.timeline is not None:
            timeline = edits.timeline
            timeline.project_id = project.id
            self._check_scene_images(ctx.folder, timeline)
            return timeline, "Timeline replaced by the reviewer."
        existing = read_timeline(stage_dir)
        stale = True
        if existing is not None and not edits.rebuild:
            try:
                stale = (stage_dir / TIMELINE_FILE).stat().st_mtime < sources.newest_mtime
            except OSError:
                stale = True
            if existing.project_id != project.id or len(existing.scenes) != len(
                sources.storyboard.scenes
            ):
                stale = True
        if existing is not None and not stale:
            self._check_scene_images(ctx.folder, existing)
            return existing, (
                "Reusing timeline.json (edit it by hand or redo with 'rebuild' to start over)."
            )
        presets = allowed_presets(options.default_presets, config, options.enable_4k, warnings)
        if edits.presets is not None:
            presets = allowed_presets(edits.presets, config, options.enable_4k, warnings)
        first = config.presets.get(presets[0] if presets else DEFAULT_PRESET)
        if first is None:
            first = config.presets[DEFAULT_PRESET]
        aspect = sources.storyboard.aspect
        width, height = first.size(aspect)
        music_choice: str | None = None
        if edits.changes_music:
            music_choice = (edits.music_path or "").strip()
        request = BuildRequest(
            project_id=project.id,
            fmt=project.format,
            aspect=aspect,
            language=str(project.language),
            folder=ctx.folder,
            storyboard=sources.storyboard,
            timing=sources.timing,
            sources=sources.images,
            width=width,
            height=height,
            proxy=config.proxy.size(aspect),
            presets=presets,
            fps=config.fps,
            voice_path="05_voice/voice.wav",
            music_folder=ctx.channel.channel.music_folder or None,
            music_choice=music_choice,
            brand_colours=list(ctx.channel.channel.brand_colors),
            captions_enabled=options.captions_enabled,
            caption_style_name=options.caption_style,
            popups_enabled=True,
            config=config,
        )
        try:
            timeline, build_warnings = build_timeline(request)
        except TimelineBuildError as exc:
            raise StageError(str(exc)) from exc
        warnings.extend(build_warnings)
        reason = "rebuilt" if existing is not None else "built"
        return timeline, (
            f"Timeline {reason} from the storyboard, the voice timing and the pictures."
        )

    @staticmethod
    def _check_scene_images(folder: Path, timeline: Timeline) -> None:
        missing = [
            scene.index + 1 for scene in timeline.scenes
            if not ff.resolve_path(folder, scene.image).is_file()
        ]
        if missing:
            listed = ", ".join(str(n) for n in missing[:12])
            raise StageError(
                f"The timeline names pictures that do not exist for scene(s) {listed}."
            )

    def _apply_simple_edits(
        self,
        ctx: StageContext,
        edits: EditApproveEdits,
        timeline: Timeline,
        config: RenderConfig,
        options: EditOptions,
        warnings: list[str],
    ) -> None:
        wanted = timeline.presets if edits.presets is None else edits.presets
        timeline.presets = allowed_presets(wanted, config, options.enable_4k, warnings)
        if edits.captions_enabled is not None:
            timeline.captions.enabled = bool(edits.captions_enabled)
        if edits.changes_music and edits.timeline is None:
            chosen = (edits.music_path or "").strip()
            if not chosen:
                timeline.music.path = None
                timeline.music.license_ok = False
            else:
                track = resolve_track(ctx.channel.channel.music_folder or None, chosen)
                if track is None:
                    raise StageError(
                        f"The music track '{chosen}' was not found. Choose one from the "
                        "channel's music folder."
                    )
                timeline.music.path = str(track)
                timeline.music.license_ok = has_licence(track)
        if timeline.music.path and not timeline.music.license_ok:
            name = Path(timeline.music.path).name
            message = f"The music track {name} has no licence file next to it."
            if not any(name in w for w in warnings):
                warnings.append(message)

    # Rendering (worker thread) -----------------------------------------------------------

    def _render_all(
        self,
        ctx: StageContext,
        timeline: Timeline,
        presets: list[RenderPreset],
        stage_dir: Path,
        config: RenderConfig,
        options: EditOptions,
        box_colour: str,
        font_family: str,
        popups_enabled: bool,
        state: dict[str, Any],
        report: ff.ProgressCallback,
        render_key: str = "",
    ) -> list[ff.RenderResult]:
        renders_state: dict[str, Any] = state["renders"]
        results: list[ff.RenderResult] = []
        ffmpeg = ff.find_ffmpeg(ctx.settings)
        ffprobe = ff.find_ffprobe(ctx.settings)
        ass_path = stage_dir / ASS_FILE
        log_path = stage_dir / RENDER_LOG
        peaks: list[float] = []
        for preset in presets:
            if ctx.cancelled:
                raise StageError("The edit step was stopped.")
            output = stage_dir / final_file_name(preset.id)
            renders_state[preset.id] = {
                "preset": preset.id, "status": "rendering", "path": None, "error": None,
            }
            write_state(stage_dir, state)
            try:
                assets = popup_assets(
                    timeline, preset, stage_dir=stage_dir, config=config, box_colour=box_colour,
                    font_family=font_family, captions_on=timeline.captions.enabled,
                    enabled=popups_enabled,
                )
                plan = ff.build_plan(
                    timeline, preset, folder=ctx.folder, config=config, popups=assets,
                    ass_path=ass_path if ass_path.is_file() else None,
                    captions=timeline.captions.enabled, x264_preset=options.x264_preset,
                )
                report(f"Rendering {preset.id} 0%", 0.0)
                result = ff.run_render(
                    plan, output, work_dir=stage_dir, log_path=log_path, progress=report,
                    cancel=ctx.cancel, ffmpeg=ffmpeg, ffprobe=ffprobe,
                )
            except ff.RenderCancelled as exc:
                renders_state[preset.id] = {
                    "preset": preset.id, "status": "failed", "path": None, "error": str(exc),
                }
                write_state(stage_dir, state)
                raise StageError(str(exc)) from exc
            except ff.RenderError as exc:
                renders_state[preset.id] = {
                    "preset": preset.id, "status": "failed", "path": None, "error": str(exc),
                }
                write_state(stage_dir, state)
                raise StageError(str(exc)) from exc
            relative = f"{STAGE_DIR_NAME}/{output.name}"
            renders_state[preset.id] = {
                "preset": preset.id,
                "status": "done",
                "path": relative,
                "error": None,
                "duration_s": result.duration_s,
                "expected_s": plan.total_s,
                "size_bytes": result.size_bytes,
                "width": result.width,
                "height": result.height,
                "seconds_taken": result.seconds_taken,
                "popups_enabled": popups_enabled,
                "render_key": render_key,
                "rendered_at": utc_stamp(),
            }
            write_state(stage_dir, state)
            results.append(result)
            peak = ff.measure_true_peak(output, ffmpeg, cancel=ctx.cancel)
            if peak is not None:
                peaks.append(peak)
        if ctx.cancelled:
            raise StageError("The edit step was stopped.")
        if peaks:
            state["true_peak_dbtp"] = max(peaks)
        elif results:
            state["true_peak_dbtp"] = None
        return results


def _parse_edits(raw: dict[str, Any]) -> EditApproveEdits:
    try:
        return EditApproveEdits.model_validate(raw or {})
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ())) or "edits"
        raise StageError(
            f"The edit changes are not valid ({where}): {first.get('msg', 'invalid value')}"
        ) from exc

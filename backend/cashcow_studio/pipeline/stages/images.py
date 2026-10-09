"""Images stage: one text-free picture per storyboard scene, checked and deduplicated.

Reads ``04_storyboard/storyboard.json`` and the channel's ``images`` settings; writes
``06_images/scene_NN.png``, ``06_images/style_sheet.png``, ``06_images/images.json``
(:class:`ImagesDoc`) and keeps rejected tries in ``06_images/rejected/``. The storyboard's
``scene.image`` fields are updated so the Storyboard Board shows the pictures.

For every scene (docs/M3-M4-CONTRACT.md section 2):

1. prompt = ``<style guide>. <scene description>. No text, no logos, no watermarks.`` plus
   the reviewer's note and, on a retry, the reason the last picture was rejected;
2. the image tool generates at its largest size for the aspect, with the style sheet and
   the previous scene's picture as references when it takes them;
3. the picture's perceptual hash is compared with the channel's earlier pictures and the
   scenes before it: a Hamming distance of 6 or less is a reuse, regenerated with a new seed;
4. the vision model (``LLMClient.analyze_image``) judges it: a mismatch, text, a real
   person, a logo or a score under 5 rejects it; up to 3 regenerations, then the scene has
   no picture and the ``images.qa`` gate blocks the stage.

Locks and redo: a scene whose record is locked or approved is never regenerated unless an
edit names it. A plain redo after a complete run regenerates every other scene; a run after a
failure or a block keeps the accepted pictures and only makes the missing ones. Edits
(``{regenerate: [{scene, note}], uploads: [{scene, path}], lock: [scene]}``) are applied by
``apply_edits`` on approval, and are honoured by ``run`` when they travel with a redo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from ...images import phash as phash_mod
from ...images.config import LARGEST, ImagesConfig, images_config, size_choice
from ...images.hash_store import ImageHashRow, ImageHashStore, month_start_iso
from ...images.qa import check_image, join_reasons, qa_prompt, verdict_text
from ...images.style_sheet import (
    STYLE_SHEET_FILE,
    ensure_style_sheet,
    list_reference_images,
    promote_scene_image,
    reference_folder,
)
from ...llm.client import LLMError
from ...models.images import (
    ImageAttempt,
    ImagesApproveEdits,
    ImagesBudget,
    ImagesDoc,
    ImagesReviewPayload,
    ImagesReviewScene,
    SceneImageRecord,
    StyleSheetSource,
    scene_file_name,
)
from ...models.project import StageName
from ...models.storyboard import Scene, StoryboardDoc
from ...policy import gates
from ...providers.base import ProviderError
from ...providers.image.base import ImageRequest, ImageResult, pixel_size
from ...storage.settings_store import atomic_write_text
from .base import (
    GateBlocked,
    StageContext,
    StageError,
    StageResult,
    run_in_thread,
)
from .storyboard import (
    DEFAULT_STYLE_GUIDE,
    read_storyboard,
    strip_prompt_wrapping,
    write_storyboard,
)

log = logging.getLogger(__name__)

IMAGES_FILE = "images.json"
REJECTED_DIR = "rejected"
STAGE_FOLDER = "06_images"
FILE_URL = "/api/projects/{project_id}/files/{path}"
COST_KIND = "images"
REVIEW_PAYLOAD_FILE = "review_payload.json"

Action = Literal["keep", "generate", "check"]


class ImagesRunError(StageError):
    """A stage failure that already carries the money spent so far (kind ``images``)."""


@dataclass
class ScenePlan:
    scene: Scene
    action: Action
    previous: SceneImageRecord | None = None
    note: str = ""
    """Reviewer note for this scene (from ``regenerate`` edits)."""
    why: str = ""
    """Why the scene is (re)generated, for the record's notes."""


@dataclass
class RunState:
    """Everything one run (or one edit application) needs, built by :func:`prepare`."""

    ctx: StageContext
    config: ImagesConfig
    storyboard: StoryboardDoc
    previous: ImagesDoc | None
    provider: Any
    llm: Any
    store: ImageHashStore
    stage_dir: Path
    aspect: str
    size: str
    model: str
    style_guide: str
    negative_rules: str
    min_score: int
    max_retries: int
    max_distance: int
    history: list[ImageHashRow]
    style_sheet: Path | None = None
    style_source: StyleSheetSource = "none"
    accepted_hashes: list[tuple[int, str]] = field(default_factory=list)
    previous_image: Path | None = None
    cost_images: float = 0.0
    cost_qa: float = 0.0
    generated_now: int = 0
    warnings: list[str] = field(default_factory=list)
    doc: ImagesDoc | None = None
    partial: SceneImageRecord | None = None
    """The scene record of a run that stopped half-way (a failed check), saved so a resume
    checks the picture that exists instead of making it again."""

    @property
    def project_id(self) -> str:
        return self.ctx.project.id

    @property
    def channel_slug(self) -> str:
        return self.ctx.project.channel_slug

    @property
    def total_cost(self) -> float:
        return round(self.cost_images + self.cost_qa, 6)


# Prompts ----------------------------------------------------------------------------------------


def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip()


def bare_scene_prompt(scene: Scene, style_guide: str) -> str:
    """The storyboard's picture description without the style prefix and the Avoid suffix."""
    body = strip_prompt_wrapping(scene.image_prompt, style_guide)
    return body or _clean(scene.image_prompt).rstrip(".")


def assemble_prompt(style_guide: str, description: str, suffix: str, extras: list[str]) -> str:
    """``<style guide>. <description>. <suffix> <extras...>`` with tidy punctuation."""
    parts = [_clean(style_guide).rstrip("."), _clean(description).rstrip("."), _clean(suffix)]
    text = ". ".join(p for p in parts[:2] if p)
    text = f"{text}. {parts[2]}" if parts[2] else f"{text}."
    for extra in extras:
        extra = _clean(extra)
        if extra:
            text += " " + extra.rstrip(".") + "."
    return text


def seed_for(project_id: str, scene: int, attempt: int) -> int:
    """A stable seed per project, scene and try, so a retry really changes the picture."""
    digest = hashlib.sha256(f"{project_id}|{scene}|{attempt}".encode()).hexdigest()
    return int(digest[:8], 16) % 2_147_483_647


def file_url(project_id: str, relative: str | None) -> str | None:
    if not relative:
        return None
    return FILE_URL.format(project_id=project_id, path=relative.replace("\\", "/"))


# Files ------------------------------------------------------------------------------------------


def read_images_doc(stage_dir: Path) -> ImagesDoc | None:
    path = stage_dir / IMAGES_FILE
    if not path.is_file():
        return None
    try:
        return ImagesDoc.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        log.warning("Ignoring an unreadable %s: %s", path, exc)
        return None


def write_images_doc(stage_dir: Path, doc: ImagesDoc) -> Path:
    path = stage_dir / IMAGES_FILE
    atomic_write_text(path, doc.model_dump_json(indent=2) + "\n")
    return path


def rejected_path(stage_dir: Path, scene: int, attempt: int) -> Path:
    folder = stage_dir / REJECTED_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"scene_{scene + 1:02d}_try{attempt}.png"


def relative_to_project(folder: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(folder.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def move_to_rejected(stage_dir: Path, path: Path, scene: int, attempt: int) -> Path | None:
    """Keep a rejected picture in ``rejected/`` so a person can look at it; never raises."""
    target = rejected_path(stage_dir, scene, attempt)
    try:
        if target.exists():
            target.unlink()
        shutil.move(str(path), str(target))
        return target
    except OSError as exc:
        log.warning("Could not move %s to %s: %s", path, target, exc)
        return path if path.exists() else None


def sync_storyboard(folder: Path, doc: ImagesDoc) -> None:
    """Write the pictures back into ``storyboard.json`` (``scene.image``); never raises."""
    try:
        storyboard = read_storyboard(folder / "04_storyboard")
    except StageError as exc:
        log.warning("Storyboard not updated with the pictures: %s", exc)
        return
    if storyboard is None:
        return
    records = {r.scene: r for r in doc.scenes}
    for scene in storyboard.scenes:
        record = records.get(scene.index)
        if record is None:
            continue
        scene.image.path = f"{STAGE_FOLDER}/{record.file}" if record.file else None
        if record.status == "approved":
            scene.image.status = "approved"
        elif record.has_image:
            scene.image.status = "generated"
        elif record.status == "rejected":
            scene.image.status = "rejected"
        else:
            scene.image.status = "pending"
        scene.image.qa = record.qa.model_dump() if record.qa else None
    try:
        write_storyboard(folder / "04_storyboard", storyboard)
    except OSError as exc:
        log.warning("Storyboard not updated with the pictures: %s", exc)


# Preparation ------------------------------------------------------------------------------------


def _provider(ctx: StageContext) -> Any:
    provider = ctx.providers.get("image")
    if provider is None:
        raise StageError(
            "No image tool is set up. Check Settings > Models and providers, or use the mock "
            "images (CCS_IMAGE_PROVIDER=mock) for an offline run."
        )
    return provider


def _llm(ctx: StageContext) -> Any:
    client = ctx.providers.get("llm")
    if client is None or not hasattr(client, "analyze_image"):
        raise StageError(
            "No writing model is set up to check the pictures. Check Settings > Models and "
            "providers."
        )
    return client


def choose_size(provider: Any, config: ImagesConfig) -> str:
    """The size to ask for: the tool's largest for the aspect, unless a size is pinned."""
    wanted = size_choice(config)
    capabilities = getattr(provider, "capabilities", None)
    if wanted.strip().lower() == LARGEST:
        if capabilities is None:
            return "2K"
        return capabilities.largest_size(config.max_size_label)
    return wanted.strip().upper() if wanted.strip().upper() in ("1K", "2K", "4K") else wanted


def choose_model(ctx: StageContext, provider: Any, config: ImagesConfig) -> str:
    """Channel ``images.model`` wins, then ``config/images.yaml``, then the tool's default."""
    channel_model = _clean(ctx.channel.images.model)
    if channel_model:
        return channel_model
    provider_id = str(getattr(provider, "id", "") or "")
    from_config = config.model_for(provider_id)
    if from_config:
        return from_config
    capabilities = getattr(provider, "capabilities", None)
    return str(getattr(capabilities, "default_model", "") or "")


def parse_edits(raw: dict[str, Any] | None) -> ImagesApproveEdits:
    if not raw:
        return ImagesApproveEdits()
    known = {k: v for k, v in raw.items() if k in ("regenerate", "uploads", "lock")}
    try:
        return ImagesApproveEdits.model_validate(known)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise StageError(f"The image edits are not valid ({where}): {first['msg']}") from exc


def prepare(ctx: StageContext) -> RunState:
    storyboard = read_storyboard(ctx.folder / "04_storyboard")
    if storyboard is None or not storyboard.scenes:
        raise StageError("The storyboard step has not produced scenes yet. Finish it first.")
    config = images_config()
    provider = _provider(ctx)
    llm = _llm(ctx)
    stage_dir = ctx.stage_dir(StageName.images)
    size = choose_size(provider, config)
    try:
        pixel_size(storyboard.aspect, size)
    except ValueError as exc:
        raise StageError(f"The image size setting is not usable: {exc}") from exc
    style_guide = _clean(storyboard.style_guide or ctx.channel.images.style_guide
                         or DEFAULT_STYLE_GUIDE)
    negative = _clean(storyboard.negative_rules or ctx.channel.images.negative_rules)
    qa_params = gates.params("images.qa")
    variety_params = gates.params("images.variety")
    store = ImageHashStore(ctx.settings.app_data_dir)
    return RunState(
        ctx=ctx,
        config=config,
        storyboard=storyboard,
        previous=read_images_doc(stage_dir),
        provider=provider,
        llm=llm,
        store=store,
        stage_dir=stage_dir,
        aspect=storyboard.aspect,
        size=size,
        model=choose_model(ctx, provider, config),
        style_guide=style_guide,
        negative_rules=negative,
        min_score=int(qa_params.get("min_score", 5)),
        max_retries=int(qa_params.get("max_retries", 3)),
        max_distance=int(variety_params.get("max_distance", 6)),
        history=store.accepted(ctx.project.channel_slug, exclude_project=ctx.project.id),
    )


def references_for(state: RunState) -> list[Path]:
    """The style sheet and the previous scene's picture, when the tool takes references."""
    capabilities = getattr(state.provider, "capabilities", None)
    limit = int(getattr(capabilities, "max_reference_images", 0) or 0)
    if limit <= 0:
        return []
    refs: list[Path] = []
    if state.config.reference_style_sheet and state.style_sheet and state.style_sheet.is_file():
        refs.append(state.style_sheet)
    previous = state.previous_image
    if state.config.reference_previous_scene and previous and previous.is_file() \
            and previous not in refs:
        refs.append(previous)
    return refs[:limit]


# Planning ---------------------------------------------------------------------------------------


def plan_scenes(
    storyboard: StoryboardDoc,
    previous: ImagesDoc | None,
    edits: ImagesApproveEdits,
    style_guide: str,
    stage_dir: Path,
) -> list[ScenePlan]:
    """Decide per scene: keep the picture, check an existing file, or generate.

    * named in ``regenerate``: generate (with the note);
    * locked or approved before: keep (unless the file is gone);
    * a previous run that was incomplete (a failure, a block) or edits that name other
      scenes: keep the accepted pictures whose scene text did not change;
    * a previous complete run with no edits (a plain redo): generate again;
    * a file in ``06_images`` that no record knows (a person put it there): check it.
    """
    notes = {e.scene: e.note for e in edits.regenerate}
    complete = (
        previous is not None
        and len(previous.scenes) == len(storyboard.scenes)
        and previous.accepted_count == len(previous.scenes)
    )
    plain_redo = complete and edits.is_empty
    plans: list[ScenePlan] = []
    for scene in storyboard.scenes:
        prev = previous.record_for(scene.index) if previous else None
        current_prompt = bare_scene_prompt(scene, style_guide)
        if scene.index in notes:
            plans.append(ScenePlan(scene, "generate", prev, notes[scene.index],
                                   "Regenerated at the reviewer's request."))
            continue
        if prev is not None and (prev.locked or prev.status == "approved") and prev.file:
            if (stage_dir / prev.file).is_file():
                plans.append(ScenePlan(scene, "keep", prev))
            else:
                plans.append(ScenePlan(scene, "generate", prev, "",
                                       "The approved picture file was missing."))
            continue
        unchecked = (
            prev is not None and prev.file is not None and prev.qa is None
            and prev.source != "upload" and prev.status != "rejected"
            and (stage_dir / prev.file).is_file() and not plain_redo
        )
        if unchecked:
            plans.append(ScenePlan(scene, "check", prev, "", "Checked after an interrupted run."))
            continue
        if prev is not None and prev.has_image and prev.file and not plain_redo:
            if (stage_dir / prev.file).is_file() and prev.scene_prompt == current_prompt:
                plans.append(ScenePlan(scene, "keep", prev))
                continue
            why = ("The scene text changed since the picture was made."
                   if prev.scene_prompt != current_prompt else "The picture file was missing.")
            plans.append(ScenePlan(scene, "generate", prev, "", why))
            continue
        if prev is None and (stage_dir / scene_file_name(scene.index)).is_file():
            plans.append(ScenePlan(scene, "check", None, "",
                                   "Found in 06_images; checked like a generated picture."))
            continue
        why = "Made again on redo." if (prev is not None and plain_redo) else ""
        plans.append(ScenePlan(scene, "generate", prev, "", why))
    return plans


# Threads and cancellation -----------------------------------------------------------------------
# ``run_in_thread`` (shared by every stage) lives in ``base.py``; it waits for the worker
# thread when the run is cancelled so no file is written into a moved folder.


def _check_cancelled(ctx: StageContext) -> None:
    if ctx.cancelled:
        raise StageError("Stopped: the project was archived or the app was closed.")


# Generation of one scene --------------------------------------------------------------------------


async def generate_file(state: RunState, request: ImageRequest) -> ImageResult:
    ctx = state.ctx

    def work() -> ImageResult:
        _check_cancelled(ctx)
        return state.provider.generate(request)

    try:
        return await run_in_thread(ctx, work)
    except ProviderError as exc:
        raise ImagesRunError(
            f"The image tool could not make scene {int(request.scene_id or 0)}: {exc}",
            cost_usd=state.total_cost, cost_kind=COST_KIND,
        ) from exc
    except (OSError, ValueError) as exc:
        raise ImagesRunError(
            f"Scene {request.scene_id} could not be saved: {exc}",
            cost_usd=state.total_cost, cost_kind=COST_KIND,
        ) from exc


def duplicate_of(state: RunState, scene: int, hash_hex: str) -> str | None:
    """Plain note when the picture looks like one already used; ``None`` when it is new."""
    best: tuple[int, str] | None = None
    for other_scene, other_hash in state.accepted_hashes:
        if other_scene == scene:
            continue
        distance = phash_mod.hamming(hash_hex, other_hash)
        if best is None or distance < best[0]:
            best = (distance, f"scene #{other_scene + 1} of this video")
    for row in state.history:
        try:
            distance = phash_mod.hamming(hash_hex, row.hash)
        except ValueError:
            continue
        if best is None or distance < best[0]:
            best = (distance, f"scene #{row.scene + 1} of an earlier video ({row.project_id[:8]})")
    if best is not None and best[0] <= state.max_distance:
        return f"{best[1]} (distance {best[0]})"
    return None


def hash_of(path: Path) -> str | None:
    try:
        return phash_mod.phash_hex(path)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        log.warning("Could not hash %s: %s", path, exc)
        return None


def image_size_of(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, UnidentifiedImageError):
        return (0, 0)


async def produce_scene(
    state: RunState, plan: ScenePlan, position: int, total: int
) -> SceneImageRecord:
    """Generate (or check) until a picture is accepted or the tries run out."""
    ctx, scene = state.ctx, plan.scene
    index = scene.index
    description = bare_scene_prompt(scene, state.style_guide)
    negative = _clean(scene.negative_prompt) or state.negative_rules
    extras: list[str] = []
    for note in [plan.note, *ctx.notes]:
        if _clean(note):
            extras.append(state.config.note_prefix.format(note=_clean(note).rstrip(".")))
    record = SceneImageRecord(
        scene=index, status="pending", provider=str(getattr(state.provider, "id", "")),
        model=state.model, scene_prompt=description, negative_prompt=negative,
        locked=bool(plan.previous and plan.previous.locked),
    )
    if plan.why:
        record.notes.append(plan.why)
    target = state.stage_dir / scene_file_name(index)
    existing = target if plan.action == "check" and target.is_file() else None
    max_attempts = 1 + state.max_retries
    last_reason = ""
    for attempt in range(1, max_attempts + 1):
        _check_cancelled(ctx)
        base_pct = 10 + 85 * position / max(1, total)
        step_pct = base_pct + 85 / max(1, total) * (attempt - 1) / max_attempts
        retry_extra = (
            [state.config.retry_note.format(reason=last_reason)] if last_reason else []
        )
        prompt = assemble_prompt(state.style_guide, description, state.config.suffix,
                                 extras + retry_extra)
        seed = seed_for(state.project_id, index, attempt)
        cost = 0.0
        if existing is not None and attempt == 1:
            await ctx.report(f"Scene {index + 1} of {total}: checking the picture", step_pct)
            path = existing
            result: ImageResult | None = None
        else:
            await ctx.report(
                f"Scene {index + 1} of {total}: making the picture (try {attempt})", step_pct
            )
            request = ImageRequest(
                prompt=prompt, output_path=target, negative_prompt=negative, aspect=state.aspect,
                size=state.size, reference_images=references_for(state), seed=seed,
                model=state.model, scene_id=str(index + 1),
            )
            result = await generate_file(state, request)
            path = Path(result.path)
            cost = float(result.cost_usd or 0.0)
            state.cost_images += cost
            state.generated_now += 1
            record.cost_usd = round(record.cost_usd + cost, 6)
            record.provider = result.provider
            record.model = result.model
            record.provenance = result.provenance
        attempt_row = ImageAttempt(
            attempt=attempt, file=relative_to_project(ctx.folder, path), seed=seed, prompt=prompt,
            cost_usd=cost, created_at=datetime.now(UTC),
        )
        record.attempts = attempt
        record.prompt = prompt
        record.seed = seed if result is not None else None
        hash_hex = await run_in_thread(ctx, partial(hash_of, path))
        attempt_row.phash = hash_hex
        reason = ""
        if hash_hex is None:
            reason = "the picture file could not be read"
        else:
            same = duplicate_of(state, index, hash_hex)
            if same:
                reason = f"it looks the same as {same}"
        if not reason:
            try:
                qa, qa_cost = await check_image(
                    state.llm, path,
                    qa_prompt(description, negative, scene.narration,
                              retry_extra[0] if retry_extra else ""),
                    project_id=state.project_id,
                )
            except LLMError as exc:
                record.status = "generated"
                record.file = target.name if path == target else None
                record.error = f"The picture could not be checked: {exc}"
                record.history.append(attempt_row)
                record.width, record.height = image_size_of(path)
                state.partial = record
                raise ImagesRunError(
                    f"Scene {index + 1} was made but could not be checked: {exc} Press Run to "
                    "check it again without making it again.",
                    cost_usd=state.total_cost, cost_kind=COST_KIND,
                ) from exc
            state.cost_qa += qa_cost
            attempt_row.qa = qa
            record.qa = qa
            reasons = qa.rejection_reasons(state.min_score)
            if reasons:
                reason = join_reasons(reasons)
                if qa.artifacts:
                    reason += " (flaws: " + ", ".join(qa.artifacts[:3]) + ")"
        if not reason:
            attempt_row.accepted = True
            record.history.append(attempt_row)
            record.file = target.name
            record.status = "generated"
            record.phash = hash_hex
            record.width, record.height = image_size_of(path)
            record.error = None
            if hash_hex:
                state.store.record(state.channel_slug, state.project_id, index, hash_hex,
                                   status="accepted", file=relative_to_project(ctx.folder, path))
                state.accepted_hashes.append((index, hash_hex))
            state.previous_image = path
            if state.style_sheet is None:
                try:
                    state.style_sheet = await run_in_thread(
                        ctx,
                        partial(promote_scene_image, state.stage_dir, path,
                                state.config.style_sheet_max_side_px),
                    )
                    state.style_source = "first_scene"
                except (OSError, UnidentifiedImageError, ValueError) as exc:
                    state.warnings.append(f"The style sheet could not be made: {exc}")
            return record
        # Rejected: keep the file aside and try again with the reason in the prompt.
        last_reason = reason
        attempt_row.reason = reason
        if hash_hex:
            state.store.record(state.channel_slug, state.project_id, index, hash_hex,
                               status="rejected")
        kept = move_to_rejected(state.stage_dir, path, index, attempt)
        attempt_row.file = relative_to_project(ctx.folder, kept) if kept else None
        record.history.append(attempt_row)
        record.notes.append(f"Try {attempt} rejected: {reason}.")
        existing = None
    record.status = "rejected"
    record.file = None
    record.error = (
        f"No usable picture after {max_attempts} tries; the last one was rejected because "
        f"{last_reason}."
    )
    return record


def keep_record(state: RunState, plan: ScenePlan) -> SceneImageRecord:
    record = plan.previous.model_copy(deep=True)  # type: ignore[union-attr]
    path = state.stage_dir / str(record.file)
    if record.phash is None:
        record.phash = hash_of(path)
    if record.phash:
        state.accepted_hashes.append((record.scene, record.phash))
    state.previous_image = path
    if state.style_sheet is None and record.has_image:
        try:
            state.style_sheet = promote_scene_image(
                state.stage_dir, path, state.config.style_sheet_max_side_px
            )
            state.style_source = "first_scene"
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            state.warnings.append(f"The style sheet could not be made: {exc}")
    return record


def adopt_upload(state: RunState, scene: Scene, raw_path: str, note: str = "") -> SceneImageRecord:
    """A picture the reviewer chose: copied in as ``scene_NN.png``, approved without a check."""
    index = scene.index
    source = Path(raw_path).expanduser()
    if not source.is_absolute():
        source = state.ctx.folder / source
    if not source.is_file():
        raise StageError(f"The uploaded picture for scene {index + 1} was not found: {raw_path}")
    target = state.stage_dir / scene_file_name(index)
    try:
        with Image.open(source) as image:
            picture = image.convert("RGB")
        if target.is_file() and source.resolve() != target.resolve():
            move_to_rejected(state.stage_dir, target, index, 0)
        picture.save(target, "PNG")
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise StageError(
            f"The uploaded file for scene {index + 1} is not a usable picture: {exc}"
        ) from exc
    previous = state.previous.record_for(index) if state.previous else None
    record = SceneImageRecord(
        scene=index, file=target.name, status="approved", source="upload",
        provider="upload", model="", scene_prompt=bare_scene_prompt(scene, state.style_guide),
        locked=bool(previous and previous.locked), attempts=previous.attempts if previous else 0,
        history=list(previous.history) if previous else [],
    )
    record.width, record.height = image_size_of(target)
    record.phash = hash_of(target)
    record.notes.append("Picture uploaded by the reviewer" + (f": {note}" if note else "."))
    if record.phash:
        same = duplicate_of(state, index, record.phash)
        if same:
            record.duplicate_of = same
        state.store.record(state.channel_slug, state.project_id, index, record.phash,
                           status="accepted", file=f"{STAGE_FOLDER}/{target.name}")
        state.accepted_hashes.append((index, record.phash))
    return record


# Results ----------------------------------------------------------------------------------------


def duplicate_pairs(doc: ImagesDoc, max_distance: int) -> list[dict[str, Any]]:
    """Pairs of accepted pictures within ``max_distance`` bits (the variety gate)."""
    rows = [(r.scene, r.phash) for r in doc.scenes if r.has_image and r.phash]
    pairs: list[dict[str, Any]] = []
    for i, (scene_a, hash_a) in enumerate(rows):
        for scene_b, hash_b in rows[i + 1:]:
            distance = phash_mod.hamming(hash_a, hash_b)
            if distance <= max_distance:
                pairs.append({"scenes": [scene_a, scene_b], "distance": distance})
    return pairs


def gate_context(doc: ImagesDoc, max_distance: int) -> dict[str, Any]:
    return {
        "scenes": [
            {"scene": r.scene, "accepted": r.has_image, "attempts": r.attempts,
             "reason": r.error or ""}
            for r in doc.scenes
        ],
        "duplicates": duplicate_pairs(doc, max_distance),
        "monthly_budget": doc.budget.monthly_limit,
        "used_this_month": doc.budget.used_this_month,
        "generated_now": doc.budget.generated_now,
    }


def budget_for(state: RunState, planned: int) -> ImagesBudget:
    limit = state.ctx.channel.images.monthly_budget_images
    used = state.store.count_since(state.channel_slug, month_start_iso())
    budget = ImagesBudget(monthly_limit=limit, used_this_month=used)
    if isinstance(limit, int) and limit > 0 and used + planned > limit:
        budget.warning = (
            f"This month's image budget is {limit} pictures; {used} are used and this video "
            f"needs about {planned} more."
        )
    return budget


def review_payload(doc: ImagesDoc, storyboard: StoryboardDoc | None = None) -> dict[str, Any]:
    narrations = {s.index: s.narration for s in storyboard.scenes} if storyboard else {}
    scenes: list[ImagesReviewScene] = []
    for record in doc.scenes:
        if record.source == "upload" and record.has_image:
            verdict = "Uploaded by the reviewer"
        elif record.has_image:
            verdict = verdict_text(record.qa)
        elif record.status == "rejected":
            verdict = "No usable picture"
        else:
            verdict = "No picture yet"
        rejected = [
            file_url(doc.project_id, a.file) for a in record.history if not a.accepted and a.file
        ]
        scenes.append(ImagesReviewScene(
            scene=record.scene, number=record.scene + 1, file=record.file,
            image_url=file_url(doc.project_id, f"{STAGE_FOLDER}/{record.file}")
            if record.file else None,
            status=record.status, source=record.source, verdict=verdict, qa=record.qa,
            attempts=record.attempts, locked=record.locked,
            narration=narrations.get(record.scene, ""), prompt=record.scene_prompt,
            seed=record.seed, cost_usd=record.cost_usd, duplicate_of=record.duplicate_of,
            error=record.error, rejected_urls=[u for u in rejected if u],
        ))
    return ImagesReviewPayload(
        project_id=doc.project_id, aspect=doc.aspect, size=doc.size, provider=doc.provider,
        model=doc.model,
        style_sheet_url=file_url(doc.project_id, f"{STAGE_FOLDER}/{doc.style_sheet}")
        if doc.style_sheet else None,
        style_sheet_source=doc.style_sheet_source, scenes=scenes, accepted=doc.accepted_count,
        total=len(doc.scenes), cost_usd=doc.cost_usd, qa_cost_usd=doc.qa_cost_usd,
        budget=doc.budget, warnings=doc.warnings, gate_results=doc.gate_results,
        generated_at=doc.generated_at,
    ).model_dump(mode="json")


def write_review_payload(state: RunState, doc: ImagesDoc) -> Path:
    """Save the grid for a run that blocked or failed (the engine only saves the payload of
    a run that finished), so the review screen shows the rejected scene, not an old grid."""
    path = state.stage_dir / REVIEW_PAYLOAD_FILE
    payload = review_payload(doc, state.storyboard)
    try:
        atomic_write_text(path, json.dumps(payload, indent=2, default=str) + "\n")
    except (OSError, TypeError, ValueError) as exc:
        log.warning("Could not save the images review payload: %s", exc)
    return path


def load_review_payload(folder: Path) -> dict[str, Any]:
    """The review payload rebuilt from the files (for a server restart)."""
    doc = read_images_doc(folder / STAGE_FOLDER)
    if doc is None:
        return {}
    try:
        storyboard = read_storyboard(folder / "04_storyboard")
    except StageError:
        storyboard = None
    return review_payload(doc, storyboard)


def summary_of(doc: ImagesDoc) -> str:
    missing = len(doc.scenes) - doc.accepted_count
    text = f"{doc.accepted_count} of {len(doc.scenes)} scenes have a picture"
    if missing:
        text += f" ({missing} without one)"
    tries = sum(r.attempts for r in doc.scenes)
    text += f", {tries} tries, ${doc.cost_usd + doc.qa_cost_usd:.4f}."
    return text


def finish(state: RunState, doc: ImagesDoc) -> tuple[Path, list[dict[str, Any]]]:
    """Write the document and the storyboard's image fields, then judge the gates."""
    doc.generated_at = datetime.now(UTC)
    doc.cost_usd = round(state.cost_images, 6)
    doc.qa_cost_usd = round(state.cost_qa, 6)
    doc.budget.generated_now = state.generated_now
    has_sheet = bool(state.style_sheet and state.style_sheet.is_file())
    doc.style_sheet = STYLE_SHEET_FILE if has_sheet else None
    doc.style_sheet_source = state.style_source if doc.style_sheet else "none"
    extra = [doc.budget.warning] if doc.budget.warning else []
    doc.warnings = list(dict.fromkeys(state.warnings + extra))
    for record in doc.scenes:
        if record.has_image and record.phash:
            record.duplicate_of = None
    for pair in duplicate_pairs(doc, state.max_distance):
        a, b = pair["scenes"]
        record = doc.record_for(b)
        if record is not None and record.source != "upload":
            record.duplicate_of = f"scene #{a + 1} of this video (distance {pair['distance']})"
    results = gates.evaluate("images", gate_context(doc, state.max_distance))
    doc.gate_results = gates.to_dicts(results)
    path = write_images_doc(state.stage_dir, doc)
    sync_storyboard(state.ctx.folder, doc)
    return path, doc.gate_results


def new_doc(state: RunState) -> ImagesDoc:
    return ImagesDoc(
        project_id=state.project_id, channel_slug=state.channel_slug,
        format=state.ctx.project.format, aspect=state.aspect, size=state.size,  # type: ignore[arg-type]
        provider=str(getattr(state.provider, "id", "")), model=state.model,
        style_guide=state.style_guide, generated_at=datetime.now(UTC),
        notes=[n for n in state.ctx.notes if n.strip()],
    )


def setup_style_sheet(state: RunState) -> None:
    folder = reference_folder(state.ctx.channel.images.reference_folder,
                              state.ctx.settings.resolved_shared_dir)
    references = list_reference_images(folder, state.config.reference_extensions)
    if _clean(state.ctx.channel.images.reference_folder) and folder is None:
        state.warnings.append(
            "The channel's reference picture folder was not found; the first accepted scene "
            "picture is used as the style sheet instead."
        )
    state.style_sheet, state.style_source = ensure_style_sheet(
        state.stage_dir, references, state.config.style_sheet_max_side_px
    )


# The stage --------------------------------------------------------------------------------------


class ImagesStage:
    name = StageName.images

    async def run(self, ctx: StageContext) -> StageResult:
        await ctx.report("Reading the storyboard", 3)
        state = prepare(ctx)
        edits = parse_edits(ctx.edits)
        plans = plan_scenes(state.storyboard, state.previous, edits, state.style_guide,
                            state.stage_dir)
        uploads = {u.scene: u.path for u in edits.uploads}
        locks = set(edits.lock)
        doc = new_doc(state)
        state.doc = doc
        setup_style_sheet(state)
        to_make = sum(1 for p in plans if p.action != "keep" and p.scene.index not in uploads)
        doc.budget = budget_for(state, to_make)
        if doc.budget.warning:
            await ctx.report(doc.budget.warning, 5)
        total = len(plans)
        try:
            for position, plan in enumerate(plans):
                index = plan.scene.index
                if index in uploads:
                    record = await run_in_thread(
                        ctx, partial(adopt_upload, state, plan.scene, uploads[index])
                    )
                elif plan.action == "keep":
                    record = await run_in_thread(ctx, partial(keep_record, state, plan))
                    await ctx.report(f"Scene {index + 1} of {total}: keeping the picture",
                                     10 + 85 * position / max(1, total))
                else:
                    record = await produce_scene(state, plan, position, total)
                if index in locks:
                    record.locked = True
                    if record.has_image:
                        record.status = "approved"
                doc.scenes.append(record)
                write_images_doc(state.stage_dir, doc)  # files first, always
        except ImagesRunError:
            if state.partial is not None:
                doc.scenes.append(state.partial)
            finish(state, doc)
            write_review_payload(state, doc)
            raise
        await ctx.report("Checking the set of pictures", 96)
        path, gate_results = finish(state, doc)
        blocking = gates.blocking_reasons(
            [gates.GateResult.model_validate(g) for g in gate_results]
        )
        if blocking:
            # The engine keeps no payload for a blocked stage; the reviewer still needs the
            # grid with the rejected scene to upload a picture or make it again.
            write_review_payload(state, doc)
            raise GateBlocked(blocking, cost_usd=state.total_cost, cost_kind=COST_KIND)
        await ctx.report("Pictures ready for review", 100)
        outputs = [path] + [state.stage_dir / r.file for r in doc.scenes if r.file]
        if doc.style_sheet:
            outputs.append(state.stage_dir / doc.style_sheet)
        return StageResult(
            outputs=outputs,
            summary=summary_of(doc),
            cost_usd=state.total_cost,
            needs_review_payload=review_payload(doc, state.storyboard),
            gate_results=gate_results,
        )

    async def apply_edits(self, ctx: StageContext) -> StageResult | None:
        """Approval edits: uploads replace pictures, named scenes are made again, locks are
        set, and every accepted picture becomes approved (the stage is being approved)."""
        edits = parse_edits(ctx.edits)
        if edits.is_empty:
            return None
        state = prepare(ctx)
        if state.previous is None:
            raise StageError("The images step has not run yet, so there is nothing to edit.")
        doc = state.previous.model_copy(deep=True)
        state.doc = doc
        state.cost_images, state.cost_qa = doc.cost_usd, doc.qa_cost_usd
        state.style_sheet, state.style_source = (
            (state.stage_dir / doc.style_sheet, doc.style_sheet_source)
            if doc.style_sheet and (state.stage_dir / doc.style_sheet).is_file()
            else (None, "none")
        )
        scenes = {s.index: s for s in state.storyboard.scenes}
        records = {r.scene: r for r in doc.scenes}
        for record in doc.scenes:
            if record.has_image and record.phash:
                state.accepted_hashes.append((record.scene, record.phash))
        for upload in edits.uploads:
            scene = scenes.get(upload.scene)
            if scene is None:
                raise StageError(f"There is no scene {upload.scene + 1} in the storyboard.")
            records[upload.scene] = await run_in_thread(
                ctx, partial(adopt_upload, state, scene, upload.path)
            )
            state.accepted_hashes = [h for h in state.accepted_hashes if h[0] != upload.scene]
            if records[upload.scene].phash:
                state.accepted_hashes.append((upload.scene, records[upload.scene].phash))
        total = len(edits.regenerate)
        for position, item in enumerate(edits.regenerate):
            scene = scenes.get(item.scene)
            if scene is None:
                raise StageError(f"There is no scene {item.scene + 1} in the storyboard.")
            if item.scene in {u.scene for u in edits.uploads}:
                continue  # the upload wins over a regenerate of the same scene
            state.accepted_hashes = [h for h in state.accepted_hashes if h[0] != item.scene]
            previous = records.get(item.scene)
            if previous and previous.file and (state.stage_dir / previous.file).is_file():
                move_to_rejected(state.stage_dir, state.stage_dir / previous.file, item.scene,
                                 previous.attempts)
            plan = ScenePlan(scene, "generate", previous, item.note,
                             "Regenerated at the reviewer's request.")
            state.previous_image = None
            try:
                records[item.scene] = await produce_scene(state, plan, position, max(1, total))
            except ImagesRunError:
                if state.partial is not None:
                    records[state.partial.scene] = state.partial
                doc.scenes = [records[i] for i in sorted(records)]
                finish(state, doc)
                write_review_payload(state, doc)
                raise
        for index in edits.lock:
            record = records.get(index)
            if record is None:
                raise StageError(f"There is no scene {index + 1} to lock.")
            record.locked = True
        for record in records.values():
            if record.has_image:
                record.status = "approved"
        doc.scenes = [records[i] for i in sorted(records)]
        doc.budget.used_this_month = state.store.count_since(state.channel_slug,
                                                            month_start_iso())
        path, gate_results = finish(state, doc)
        return StageResult(
            outputs=[path],
            summary="Pictures edited by the reviewer: " + summary_of(doc),
            cost_usd=round(state.total_cost - state.previous.cost_usd - state.previous.qa_cost_usd,
                           6),
            needs_review_payload=review_payload(doc, state.storyboard),
            gate_results=gate_results,
        )

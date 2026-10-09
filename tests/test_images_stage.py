"""Images stage on the mocks: files and images.json, the review payload, QA rejection and
retry (FAIL_QA hooks), the rejected folder and the blocking gate, pHash dedupe against the
channel's history, locks and redo, approval edits (uploads, lock, regenerate), the style sheet
from a channel folder, the budget warning, resume after a failed check, cancellation."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from cashcow_studio.config import Settings
from cashcow_studio.images.hash_store import ImageHashStore
from cashcow_studio.images.phash import hamming
from cashcow_studio.llm import LLMError, MockLLMClient
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.images import ImagesDoc
from cashcow_studio.models.project import Project, ProjectSource, StageName
from cashcow_studio.models.storyboard import (
    Scene,
    SceneMotion,
    ScenePopup,
    SceneTransition,
    StoryboardDoc,
)
from cashcow_studio.pipeline.stages.base import GateBlocked, StageContext, StageError
from cashcow_studio.pipeline.stages.images import (
    IMAGES_FILE,
    ImagesRunError,
    ImagesStage,
    assemble_prompt,
    load_review_payload,
    plan_scenes,
    seed_for,
)
from cashcow_studio.pipeline.stages.storyboard import write_storyboard
from cashcow_studio.providers.image.mock import MockImageProvider

STYLE = "Cinematic photo-realism, warm light"
NEGATIVE = "no watermarks, no distorted hands"
SUFFIX = "No text, no logos, no watermarks."
PROMPTS = [
    "A diner at dawn, wide shot, empty parking lot",
    "A folded note under a plate, close up, soft window light",
    "A waitress sitting alone in a booth, medium shot, tears",
]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def small_images(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCS_IMAGE_SIZE", "1K")  # 1024x576 placeholders keep the tests quick


def storyboard_doc(project_id: str, prompts: list[str], fmt: str = "long") -> StoryboardDoc:
    scenes = []
    for i, prompt in enumerate(prompts):
        scenes.append(Scene(
            index=i, sentence_ids=[f"s-{i + 1:02d}"], narration=f"Narration for scene {i + 1}.",
            est_start_s=i * 10.0, est_end_s=(i + 1) * 10.0, est_duration_s=10.0,
            image_prompt=f"{STYLE}. {prompt}. Avoid: text, letters, {NEGATIVE}.",
            negative_prompt=f"text, letters, {NEGATIVE}",
            popup=ScenePopup(text=None), motion=SceneMotion(preset="zoom_in"),
            transition_out=SceneTransition(type="fade", duration_s=0.6),
        ))
    return StoryboardDoc(
        project_id=project_id, format=fmt, aspect="9:16" if fmt == "shorts" else "16:9",
        style_guide=STYLE, negative_rules=NEGATIVE, generated_at=datetime.now(UTC),
        model="mock", scenes=scenes,
    )


def make_ctx(
    app_env,
    *,
    project_id: str = "p1",
    prompts: list[str] | None = None,
    fmt: str = "long",
    images: dict[str, Any] | None = None,
    notes: list[str] | None = None,
    edits: dict[str, Any] | None = None,
    llm: Any = None,
) -> StageContext:
    settings = Settings()
    folder = app_env.projects_dir / project_id
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    project = Project(id=project_id, channel_slug="kind-ledger", topic_slug="diner",
                      title="The Diner Debt", format=fmt, created_at=now, updated_at=now,
                      folder=str(folder),
                      source=ProjectSource(kind="own_topic", topic_text="The Diner Debt"))
    channel = Channel.model_validate({
        "slug": "kind-ledger", "channel": {"name": "Kind Ledger"},
        "images": {"style_guide": STYLE, "negative_rules": NEGATIVE, **(images or {})},
    })
    doc = storyboard_doc(project_id, prompts or PROMPTS, fmt)
    write_storyboard(folder / "04_storyboard", doc)
    providers = {
        "image": MockImageProvider(),
        "llm": llm or MockLLMClient(app_data_dir=settings.app_data_dir),
    }
    return StageContext(project=project, channel=channel, settings=settings, folder=folder,
                        providers=providers, notes=notes or [], edits=edits or {})


def read_doc(ctx: StageContext) -> ImagesDoc:
    path = ctx.folder / "06_images" / IMAGES_FILE
    return ImagesDoc.model_validate_json(path.read_text(encoding="utf-8"))


def images_dir(ctx: StageContext) -> Path:
    return ctx.folder / "06_images"


def provider_of(ctx: StageContext) -> MockImageProvider:
    return ctx.providers["image"]


def close_to(colour: tuple[int, ...], expected: tuple[int, ...], tolerance: int = 4) -> bool:
    """JPEG round trips shift a flat colour by a unit or two."""
    return all(abs(a - b) <= tolerance for a, b in zip(colour, expected, strict=True))


def fresh_providers(ctx: StageContext) -> None:
    """New provider and model objects so call lists start empty."""
    ctx.providers["image"] = MockImageProvider()
    ctx.providers["llm"] = MockLLMClient(app_data_dir=ctx.settings.app_data_dir)


# Pure helpers -------------------------------------------------------------------------------------


def test_prompt_assembly_and_seeds() -> None:
    prompt = assemble_prompt(STYLE + ".", "A diner at dawn.", SUFFIX, ["Reviewer note: darker"])
    assert prompt == f"{STYLE}. A diner at dawn. {SUFFIX} Reviewer note: darker."
    assert assemble_prompt("", "A diner", SUFFIX, []) == f"A diner. {SUFFIX}"
    assert assemble_prompt(STYLE, "A diner", "", []) == f"{STYLE}. A diner."
    assert seed_for("p1", 0, 1) != seed_for("p1", 0, 2) != seed_for("p1", 1, 2)
    assert seed_for("p1", 0, 1) == seed_for("p1", 0, 1)
    assert 0 <= seed_for("x", 3, 4) < 2**31


# The stage ----------------------------------------------------------------------------------------


def test_run_generates_checks_and_writes_everything(app_env) -> None:
    ctx = make_ctx(app_env)
    result = run(ImagesStage().run(ctx))
    folder = images_dir(ctx)
    for name in ("scene_01.png", "scene_02.png", "scene_03.png", "style_sheet.png", IMAGES_FILE):
        assert (folder / name).is_file(), name
    with Image.open(folder / "scene_01.png") as image:
        assert image.size == (1024, 576)
    doc = read_doc(ctx)
    assert doc.project_id == "p1" and doc.aspect == "16:9" and doc.size == "1K"
    assert doc.provider == "mock" and doc.model == "mock-placeholder"
    assert doc.style_sheet == "style_sheet.png" and doc.style_sheet_source == "first_scene"
    assert [r.scene for r in doc.scenes] == [0, 1, 2]
    for record in doc.scenes:
        assert record.status == "generated" and record.file == f"scene_{record.scene + 1:02d}.png"
        assert record.qa is not None and record.qa.accepted() and record.attempts == 1
        assert record.phash and len(record.phash) == 16
        assert record.prompt.startswith(STYLE + ". ") and record.prompt.endswith(SUFFIX)
        assert record.scene_prompt == PROMPTS[record.scene]
        assert "no watermarks" in record.negative_prompt
        assert record.width == 1024 and record.height == 576
        assert record.history and record.history[0].accepted
    assert doc.accepted_count == 3 and doc.cost_usd == 0.0 and doc.warnings == []
    assert all(g["passed"] for g in doc.gate_results)
    assert {g["id"] for g in doc.gate_results} == {"images.qa", "images.variety", "images.budget"}

    # What the tool was asked: style guide first, suffix last, references grow scene by scene.
    calls = provider_of(ctx).calls
    assert [c.scene_id for c in calls] == ["1", "2", "3"]
    assert calls[0].prompt == f"{STYLE}. {PROMPTS[0]}. {SUFFIX}"
    assert calls[0].reference_images == []
    assert [p.name for p in calls[1].reference_images] == ["style_sheet.png", "scene_01.png"]
    assert [p.name for p in calls[2].reference_images] == ["style_sheet.png", "scene_02.png"]
    assert calls[0].aspect == "16:9" and calls[0].size == "1K" and calls[0].seed is not None
    assert len({c.seed for c in calls}) == 3
    # The vision check saw every picture with the scene description.
    qa_calls = [c for c in ctx.providers["llm"].calls if c["task"] == "image_qa"]
    assert len(qa_calls) == 3 and PROMPTS[1] in qa_calls[1]["prompt"]
    assert qa_calls[0]["size"] == (1024, 576)
    # The channel's history knows the three pictures.
    rows = ImageHashStore(ctx.settings.app_data_dir).accepted("kind-ledger")
    assert sorted(r.scene for r in rows) == [0, 1, 2]
    assert all(r.project_id == "p1" and r.file == f"06_images/scene_{r.scene + 1:02d}.png"
               for r in rows)
    # The storyboard shows the pictures.
    storyboard = json.loads((ctx.folder / "04_storyboard" / "storyboard.json").read_text("utf-8"))
    assert storyboard["scenes"][0]["image"] == {
        "path": "06_images/scene_01.png", "status": "generated",
        "qa": doc.scenes[0].qa.model_dump(),
    }
    # The result and the review payload.
    assert result.cost_usd == 0.0 and "3 of 3 scenes have a picture" in result.summary
    assert {p.name for p in result.outputs} >= {IMAGES_FILE, "scene_01.png", "style_sheet.png"}
    payload = result.needs_review_payload
    assert payload["stage"] == "images" and payload["cost_kind"] == "images"
    assert payload["accepted"] == 3 and payload["total"] == 3
    assert payload["style_sheet_url"] == "/api/projects/p1/files/06_images/style_sheet.png"
    first = payload["scenes"][0]
    assert first["image_url"] == "/api/projects/p1/files/06_images/scene_01.png"
    assert first["verdict"] == "Passed (8/10)" and first["attempts"] == 1
    assert first["locked"] is False and first["number"] == 1 and first["prompt"] == PROMPTS[0]
    assert first["narration"] == "Narration for scene 1."
    assert json.dumps(payload)
    assert load_review_payload(ctx.folder)["scenes"][2]["image_url"].endswith("scene_03.png")


def test_shorts_use_the_vertical_aspect(app_env) -> None:
    ctx = make_ctx(app_env, project_id="s1", fmt="shorts", prompts=PROMPTS[:2])
    run(ImagesStage().run(ctx))
    with Image.open(images_dir(ctx) / "scene_01.png") as image:
        assert image.size == (576, 1024)
    assert read_doc(ctx).aspect == "9:16"


def test_qa_rejection_is_retried_with_the_reason_and_kept_in_rejected(app_env) -> None:
    prompts = [PROMPTS[0], PROMPTS[1] + " FAIL_QA_ONCE", PROMPTS[2]]
    ctx = make_ctx(app_env, project_id="r1", prompts=prompts)
    result = run(ImagesStage().run(ctx))
    doc = read_doc(ctx)
    second = doc.scenes[1]
    assert second.status == "generated" and second.attempts == 2 and second.file == "scene_02.png"
    assert second.history[0].accepted is False and second.history[1].accepted is True
    assert "does not show what the scene asked for" in second.history[0].reason
    assert second.history[0].file == "06_images/rejected/scene_02_try1.png"
    assert (images_dir(ctx) / "rejected" / "scene_02_try1.png").is_file()
    assert any("Try 1 rejected" in n for n in second.notes)
    calls = provider_of(ctx).calls
    assert len(calls) == 4
    retry = calls[2]
    assert retry.scene_id == "2" and "was rejected because" in retry.prompt
    assert "does not show what the scene asked for" in retry.prompt
    assert retry.seed != calls[1].seed  # a new seed for the retry
    assert all(g["passed"] for g in result.gate_results if g["id"] == "images.qa")
    payload = result.needs_review_payload
    assert payload["scenes"][1]["attempts"] == 2
    assert payload["scenes"][1]["rejected_urls"] == [
        "/api/projects/r1/files/06_images/rejected/scene_02_try1.png"
    ]
    # A rejected try still counts for the budget, never for the dedupe.
    store = ImageHashStore(ctx.settings.app_data_dir)
    assert store.count_since("kind-ledger", "2000-01-01") == 4
    assert len(store.accepted("kind-ledger")) == 3


def test_qa_failures_block_after_the_retries_and_a_rerun_only_redoes_that_scene(app_env) -> None:
    prompts = [PROMPTS[0], PROMPTS[1] + " FAIL_QA_TEXT", PROMPTS[2]]
    ctx = make_ctx(app_env, project_id="b1", prompts=prompts)
    with pytest.raises(GateBlocked) as excinfo:
        run(ImagesStage().run(ctx))
    assert excinfo.value.cost_kind == "images"
    assert "scenes have no accepted picture" in str(excinfo.value)
    assert "#2" in str(excinfo.value) and "contains text" in str(excinfo.value)
    doc = read_doc(ctx)  # written before the gate blocked
    second = doc.scenes[1]
    assert second.status == "rejected" and second.file is None and second.attempts == 4
    assert second.error and "4 tries" in second.error
    rejected = sorted(p.name for p in (images_dir(ctx) / "rejected").iterdir())
    assert rejected == [f"scene_02_try{i}.png" for i in range(1, 5)]
    assert not (images_dir(ctx) / "scene_02.png").exists()
    assert doc.scenes[0].has_image and doc.scenes[2].has_image
    assert doc.style_sheet == "style_sheet.png"
    blocked = [g for g in doc.gate_results if g["id"] == "images.qa"]
    assert blocked and not blocked[0]["passed"]
    storyboard = json.loads((ctx.folder / "04_storyboard" / "storyboard.json").read_text("utf-8"))
    assert storyboard["scenes"][1]["image"]["status"] == "rejected"

    # The reviewer fixes the storyboard prompt and presses Run: only scene 2 is made again.
    write_storyboard(ctx.folder / "04_storyboard", storyboard_doc("b1", PROMPTS))
    fresh_providers(ctx)
    result = run(ImagesStage().run(ctx))
    assert [c.scene_id for c in provider_of(ctx).calls] == ["2"]
    doc = read_doc(ctx)
    assert doc.accepted_count == 3 and doc.scenes[1].attempts == 1
    assert doc.scenes[1].file == "scene_02.png"
    assert all(g["passed"] for g in result.gate_results if g["severity"] == "block")


def test_duplicate_of_an_earlier_video_is_regenerated_with_a_new_seed(app_env) -> None:
    ctx = make_ctx(app_env, project_id="d1")
    run(ImagesStage().run(ctx))
    first_hash = read_doc(ctx).scenes[0].phash
    assert first_hash
    # Pretend an earlier video of the channel used that very picture, then start over.
    store = ImageHashStore(ctx.settings.app_data_dir)
    store.forget_project("d1")
    store.record("kind-ledger", "older-project-0001", 5, first_hash, file="x.png")
    for path in images_dir(ctx).iterdir():
        if path.is_file():
            path.unlink()
    fresh_providers(ctx)
    result = run(ImagesStage().run(ctx))
    doc = read_doc(ctx)
    first = doc.scenes[0]
    assert first.status == "generated" and first.attempts == 2
    assert "looks the same as scene #6 of an earlier video (older-pr" in first.history[0].reason
    assert first.history[0].qa is None  # no money spent checking a duplicate
    assert (images_dir(ctx) / "rejected" / "scene_01_try1.png").is_file()
    assert first.phash and hamming(first.phash, first_hash) > 6
    calls = provider_of(ctx).calls
    assert calls[0].seed != calls[1].seed and calls[1].scene_id == "1"
    assert len([c for c in ctx.providers["llm"].calls if c["task"] == "image_qa"]) == 3
    assert all(g["passed"] for g in result.gate_results)
    # Within one video a second accepted picture that looks the same is only a warning.
    assert result.needs_review_payload["scenes"][0]["duplicate_of"] is None


def test_redo_keeps_approved_and_locked_pictures_and_names_scenes(app_env) -> None:
    ctx = make_ctx(app_env, project_id="l1")
    run(ImagesStage().run(ctx))
    before = {p.name: p.read_bytes() for p in images_dir(ctx).glob("scene_*.png")}
    # Lock scene 1 through the approval edits.
    ctx.edits = {"lock": [0]}
    result = run(ImagesStage().apply_edits(ctx))
    assert result is not None and "edited by the reviewer" in result.summary
    doc = read_doc(ctx)
    assert doc.scenes[0].locked and doc.scenes[0].status == "approved"
    assert all(r.status == "approved" for r in doc.scenes)  # approving approves every picture
    assert provider_of(ctx).calls[-1].scene_id == "3"  # nothing was made again

    # A plain redo: approved pictures stay, nothing else is approved any more, so with every
    # picture approved nothing is regenerated at all.
    fresh_providers(ctx)
    ctx.edits = {}
    ctx.notes = ["Make it moodier"]
    run(ImagesStage().run(ctx))
    assert provider_of(ctx).calls == []
    after = {p.name: p.read_bytes() for p in images_dir(ctx).glob("scene_*.png")}
    assert after == before

    # A redo that names scene 3 regenerates only that one, with the note in the prompt.
    fresh_providers(ctx)
    ctx.edits = {"regenerate": [{"scene": 2, "note": "Show the street at night"}]}
    run(ImagesStage().run(ctx))
    calls = provider_of(ctx).calls
    assert [c.scene_id for c in calls] == ["3"]
    assert "Reviewer note: Show the street at night." in calls[0].prompt
    assert "Reviewer note: Make it moodier." in calls[0].prompt
    doc = read_doc(ctx)
    assert doc.scenes[2].status == "generated" and doc.scenes[0].status == "approved"
    assert any("reviewer's request" in n for n in doc.scenes[2].notes)
    assert (images_dir(ctx) / "scene_01.png").read_bytes() == before["scene_01.png"]

    # Now a plain redo regenerates the unapproved scene 3 and keeps the approved two.
    fresh_providers(ctx)
    ctx.edits = {}
    run(ImagesStage().run(ctx))
    assert [c.scene_id for c in provider_of(ctx).calls] == ["3"]
    assert any("Made again on redo" in n for n in read_doc(ctx).scenes[2].notes)


def test_apply_edits_uploads_locks_and_regenerates(app_env) -> None:
    ctx = make_ctx(app_env, project_id="e1")
    run(ImagesStage().run(ctx))
    upload = images_dir(ctx) / "my_own_picture.jpg"
    Image.new("RGB", (640, 360), (200, 30, 30)).save(upload, "JPEG")
    fresh_providers(ctx)
    ctx.edits = {
        "uploads": [{"scene": 1, "path": "06_images/my_own_picture.jpg"}],
        "lock": [2],
        "regenerate": [{"scene": 0, "note": "wider"}],
        "override_gates": True,  # extra keys from the review panel are ignored
    }
    result = run(ImagesStage().apply_edits(ctx))
    assert result is not None
    doc = read_doc(ctx)
    uploaded = doc.scenes[1]
    assert uploaded.source == "upload" and uploaded.status == "approved"
    assert uploaded.file == "scene_02.png" and uploaded.width == 640 and uploaded.qa is None
    with Image.open(images_dir(ctx) / "scene_02.png") as image:
        assert image.format == "PNG" and close_to(image.getpixel((5, 5)), (200, 30, 30))
    assert (images_dir(ctx) / "rejected" / "scene_02_try0.png").is_file()  # the old one
    assert doc.scenes[2].locked and doc.scenes[2].status == "approved"
    calls = provider_of(ctx).calls
    assert [c.scene_id for c in calls] == ["1"] and "Reviewer note: wider." in calls[0].prompt
    assert doc.scenes[0].status == "approved" and doc.scenes[0].attempts == 1
    payload = result.needs_review_payload
    assert payload["scenes"][1]["verdict"] == "Uploaded by the reviewer"
    assert payload["scenes"][1]["image_url"] == "/api/projects/e1/files/06_images/scene_02.png"
    assert all(g["passed"] for g in result.gate_results if g["severity"] == "block")
    rows = {r.scene: r for r in ImageHashStore(ctx.settings.app_data_dir).accepted("kind-ledger")}
    assert rows[1].hash == uploaded.phash

    ctx.edits = {"uploads": [{"scene": 1, "path": "06_images/missing.png"}]}
    with pytest.raises(StageError, match="was not found"):
        run(ImagesStage().apply_edits(ctx))
    ctx.edits = {"regenerate": [{"scene": "two"}]}
    with pytest.raises(StageError, match="not valid"):
        run(ImagesStage().apply_edits(ctx))
    ctx.edits = {}
    assert run(ImagesStage().apply_edits(ctx)) is None


def test_channel_reference_folder_becomes_the_style_sheet(app_env) -> None:
    refs = app_env.shared_dir / "channels" / "kind-ledger" / "refs"
    refs.mkdir(parents=True)
    Image.new("RGB", (3000, 2000), (10, 120, 200)).save(refs / "look.jpg", "JPEG")
    (refs / "notes.txt").write_text("not a picture", encoding="utf-8")
    ctx = make_ctx(app_env, project_id="c1", prompts=PROMPTS[:2],
                   images={"reference_folder": "channels/kind-ledger/refs"})
    run(ImagesStage().run(ctx))
    sheet = images_dir(ctx) / "style_sheet.png"
    with Image.open(sheet) as image:
        assert image.size == (2048, 1365) and close_to(image.getpixel((5, 5)), (10, 120, 200))
    doc = read_doc(ctx)
    assert doc.style_sheet_source == "channel_reference" and doc.warnings == []
    calls = provider_of(ctx).calls
    assert [p.name for p in calls[0].reference_images] == ["style_sheet.png"]

    # A folder outside the shared folder is refused; a missing one is reported.
    outside = make_ctx(app_env, project_id="c2", prompts=PROMPTS[:1],
                       images={"reference_folder": "../../app"})
    run(ImagesStage().run(outside))
    assert read_doc(outside).style_sheet_source == "first_scene"
    assert any("was not found" in w for w in read_doc(outside).warnings)


def test_budget_warning_does_not_block(app_env) -> None:
    ctx = make_ctx(app_env, project_id="m1", images={"monthly_budget_images": 2})
    result = run(ImagesStage().run(ctx))
    doc = read_doc(ctx)
    assert doc.budget.monthly_limit == 2 and doc.budget.used_this_month == 0
    assert doc.budget.generated_now == 3
    assert doc.budget.warning and "budget is 2 pictures" in doc.budget.warning
    assert doc.budget.warning in doc.warnings
    budget = next(g for g in result.gate_results if g["id"] == "images.budget")
    assert not budget["passed"] and budget["severity"] == "warn" and "3 of 2" in budget["detail"]
    assert doc.accepted_count == 3


def test_failed_check_is_resumed_without_making_the_picture_again(app_env) -> None:
    class FlakyLLM(MockLLMClient):
        failures = 1

        async def analyze_image(self, *args: Any, **kwargs: Any) -> Any:
            if len([c for c in self.calls if c["task"] == "image_qa"]) == 1 and self.failures:
                self.failures -= 1
                raise LLMError("Could not reach Claude.")
            return await super().analyze_image(*args, **kwargs)

    ctx = make_ctx(app_env, project_id="f1", llm=FlakyLLM(app_data_dir=Settings().app_data_dir))
    with pytest.raises(ImagesRunError, match="could not be checked") as excinfo:
        run(ImagesStage().run(ctx))
    assert excinfo.value.cost_kind == "images"
    doc = read_doc(ctx)
    assert doc.scenes[0].has_image and doc.scenes[1].file == "scene_02.png"
    assert doc.scenes[1].qa is None and doc.scenes[1].error and len(doc.scenes) == 2
    second_bytes = (images_dir(ctx) / "scene_02.png").read_bytes()

    fresh_providers(ctx)
    run(ImagesStage().run(ctx))
    assert [c.scene_id for c in provider_of(ctx).calls] == ["3"]  # scene 2 was only checked
    doc = read_doc(ctx)
    assert doc.accepted_count == 3 and doc.scenes[1].qa is not None
    assert doc.scenes[1].seed is None and doc.scenes[1].attempts == 1
    assert (images_dir(ctx) / "scene_02.png").read_bytes() == second_bytes
    assert any("interrupted run" in n for n in doc.scenes[1].notes)


def test_hand_placed_files_are_checked_not_replaced(app_env) -> None:
    ctx = make_ctx(app_env, project_id="h1", prompts=PROMPTS[:2])
    folder = ctx.stage_dir(StageName.images)
    Image.new("RGB", (800, 450), (90, 90, 90)).save(folder / "scene_01.png")
    run(ImagesStage().run(ctx))
    assert [c.scene_id for c in provider_of(ctx).calls] == ["2"]
    doc = read_doc(ctx)
    assert doc.scenes[0].has_image and doc.scenes[0].width == 800
    assert any("Found in 06_images" in n for n in doc.scenes[0].notes)


def test_plan_resume_versus_redo(app_env) -> None:
    ctx = make_ctx(app_env, project_id="plan")
    run(ImagesStage().run(ctx))
    doc = read_doc(ctx)
    storyboard = storyboard_doc("plan", PROMPTS)
    from cashcow_studio.models.images import ImagesApproveEdits

    complete = plan_scenes(storyboard, doc, ImagesApproveEdits(), STYLE, images_dir(ctx))
    assert [p.action for p in complete] == ["generate"] * 3  # plain redo of a complete run
    partial = doc.model_copy(deep=True)
    partial.scenes[2].status = "rejected"
    partial.scenes[2].file = None
    resumed = plan_scenes(storyboard, partial, ImagesApproveEdits(), STYLE, images_dir(ctx))
    assert [p.action for p in resumed] == ["keep", "keep", "generate"]
    changed = storyboard_doc("plan", [PROMPTS[0], "Something new", PROMPTS[2]])
    replanned = plan_scenes(changed, partial, ImagesApproveEdits(), STYLE, images_dir(ctx))
    assert [p.action for p in replanned] == ["keep", "generate", "generate"]
    assert "scene text changed" in replanned[1].why


def test_missing_storyboard_and_provider_are_plain_errors(app_env) -> None:
    ctx = make_ctx(app_env, project_id="none")
    (ctx.folder / "04_storyboard" / "storyboard.json").unlink()
    with pytest.raises(StageError, match="storyboard step has not produced"):
        run(ImagesStage().run(ctx))
    ctx = make_ctx(app_env, project_id="noimg")
    del ctx.providers["image"]
    with pytest.raises(StageError, match="No image tool"):
        run(ImagesStage().run(ctx))
    ctx = make_ctx(app_env, project_id="nollm")
    del ctx.providers["llm"]
    with pytest.raises(StageError, match="check the pictures"):
        run(ImagesStage().run(ctx))


def test_cancelled_run_stops_before_writing(app_env) -> None:
    ctx = make_ctx(app_env, project_id="cancel")
    ctx.cancel.set()
    with pytest.raises(StageError, match="Stopped"):
        run(ImagesStage().run(ctx))
    assert not list(images_dir(ctx).glob("scene_*.png"))

"""The images stage inside the real engine, through the HTTP API, with every provider mocked:
a project runs research..voice on auto, parks at images awaiting_review with pictures on disk
and a review payload with image URLs, and an approval with edits (lock, regenerate) is applied
by the stage's hook. Nothing touches the network."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from cashcow_studio.app import create_app
from conftest import AppEnv, channel_payload

SLUG = "kind-ledger"


@pytest.fixture
def api(app_env: AppEnv, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for name in ("LLM", "RESEARCH", "IMAGE", "VOICE"):
        monkeypatch.setenv(f"CCS_{name}_PROVIDER", "mock")
    monkeypatch.setenv("CCS_IMAGE_SIZE", "1K")
    with TestClient(create_app()) as client:
        stages = {stage.value for stage in client.app.state.engine.stages}
        if not {"title", "script", "storyboard", "voice", "images"} <= stages:
            pytest.skip(f"stages missing from this build: {stages}")
        response = client.post("/api/channels", json=channel_payload("Kind Ledger"))
        assert response.status_code == 201, response.text
        yield client


def wait_for(
    client: TestClient, project_id: str, predicate: Callable[[dict[str, Any]], bool],
    timeout: float = 120.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/projects/{project_id}").json()
        if predicate(body):
            return body
        failed = {n: s["error"] for n, s in body["stages"].items() if s["status"] == "failed"}
        assert not failed, f"a stage failed: {failed}"
        if time.monotonic() > deadline:
            statuses = {n: s["status"] for n, s in body["stages"].items()}
            raise AssertionError(f"timed out; stages: {statuses}")
        time.sleep(0.05)


def test_images_stage_runs_in_the_engine_and_applies_edits(api: TestClient) -> None:
    created = api.post("/api/projects", json={
        "channel_slug": SLUG, "format": "long",
        "source": {"kind": "own_topic", "topic_text": "The waiter who never forgot a face"},
        "stage_mode_overrides": {"title": "auto", "script": "auto", "storyboard": "auto",
                                 "voice": "auto", "images": "review"},
    }).json()
    project = wait_for(
        api, created["id"],
        lambda b: b["stages"]["images"]["status"] == "awaiting_review",
    )
    folder = Path(project["folder"])
    images = folder / "06_images"
    assert (images / "images.json").is_file() and (images / "style_sheet.png").is_file()
    doc = json.loads((images / "images.json").read_text(encoding="utf-8"))
    assert doc["scenes"] and all(s["status"] == "generated" for s in doc["scenes"])
    for record in doc["scenes"]:
        assert (images / record["file"]).is_file()
    assert project["costs"]["images_usd"] == 0.0
    history = "\n".join(project["stages"]["images"]["history"])
    assert "scenes have a picture" in project["stages"]["images"]["summary"]
    assert "Finished" in history
    gate_ids = {g["id"] for g in project["stages"]["images"]["gate_results"]}
    assert gate_ids == {"images.qa", "images.variety", "images.budget"}

    payload = api.get(f"/api/projects/{created['id']}/stage/images").json()
    assert payload["stage"] == "images" and payload["cost_kind"] == "images"
    assert payload["accepted"] == payload["total"] == len(doc["scenes"])
    first = payload["scenes"][0]
    assert first["image_url"] == f"/api/projects/{created['id']}/files/06_images/scene_01.png"
    assert first["verdict"].startswith("Passed") and first["locked"] is False
    storyboard = json.loads((folder / "04_storyboard" / "storyboard.json").read_text("utf-8"))
    assert storyboard["scenes"][0]["image"]["path"] == "06_images/scene_01.png"

    # Approve with edits: lock scene 1, make scene 2 again with a note.
    response = api.post(f"/api/projects/{created['id']}/stage/images/approve", json={
        "by": "Imran", "notes": "good",
        "edits": {"lock": [0], "regenerate": [{"scene": 1, "note": "brighter"}]},
    })
    assert response.status_code == 200, response.text
    project = wait_for(api, created["id"], lambda b: b["stages"]["images"]["status"] == "done")
    doc = json.loads((images / "images.json").read_text(encoding="utf-8"))
    assert doc["scenes"][0]["locked"] is True
    assert all(s["status"] == "approved" for s in doc["scenes"])
    assert doc["scenes"][1]["attempts"] == 1 and "brighter" in doc["scenes"][1]["prompt"]
    assert any("reviewer's request" in n for n in doc["scenes"][1]["notes"])
    assert "Edits applied by Imran: lock, regenerate" in "\n".join(
        project["stages"]["images"]["history"]
    )
    payload = api.get(f"/api/projects/{created['id']}/stage/images").json()
    assert payload["scenes"][0]["locked"] is True and payload["scenes"][0]["status"] == "approved"


def wait_for_status(
    client: TestClient, project_id: str, stage: str, wanted: str, timeout: float = 120.0,
) -> dict[str, Any]:
    """Like ``wait_for`` but the watched stage itself may end up ``failed``."""
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/projects/{project_id}").json()
        if body["stages"][stage]["status"] == wanted:
            return body
        others = {
            n: s["error"] for n, s in body["stages"].items()
            if s["status"] == "failed" and n != stage
        }
        assert not others, f"another stage failed: {others}"
        if time.monotonic() > deadline:
            statuses = {n: s["status"] for n, s in body["stages"].items()}
            raise AssertionError(f"timed out; stages: {statuses}")
        time.sleep(0.05)


def test_blocked_images_stage_serves_the_rejected_scene_and_accepts_an_upload(
    api: TestClient, tmp_path: Path,
) -> None:
    """When images.qa blocks the stage the review payload still lists the rejected scene (not
    an empty or stale grid), and approving with an upload for it passes the gate."""
    created = api.post("/api/projects", json={
        "channel_slug": SLUG, "format": "long",
        "source": {"kind": "own_topic", "topic_text": "The waiter who never forgot a face"},
        "stage_mode_overrides": {"title": "auto", "script": "auto", "storyboard": "review",
                                 "voice": "auto", "images": "review", "edit": "manual"},
    }).json()
    project = wait_for(
        api, created["id"],
        lambda b: b["stages"]["storyboard"]["status"] == "awaiting_review",
    )
    folder = Path(project["folder"])
    storyboard_path = folder / "04_storyboard" / "storyboard.json"
    storyboard = json.loads(storyboard_path.read_text("utf-8"))
    # In the description, not after the "Avoid:" suffix (that part is stripped before QA).
    storyboard["scenes"][1]["image_prompt"] = "FAIL_QA " + storyboard["scenes"][1]["image_prompt"]
    storyboard_path.write_text(json.dumps(storyboard), "utf-8")
    response = api.post(f"/api/projects/{created['id']}/stage/storyboard/approve",
                        json={"by": "Imran"})
    assert response.status_code == 200, response.text

    project = wait_for_status(api, created["id"], "images", "failed")
    assert "quality check" in (project["stages"]["images"]["error"] or "").lower()
    payload = api.get(f"/api/projects/{created['id']}/stage/images").json()
    assert payload["stage"] == "images" and payload["total"] == len(storyboard["scenes"])
    rejected = payload["scenes"][1]
    assert rejected["status"] == "rejected" and rejected["image_url"] is None
    assert rejected["verdict"] == "No usable picture" and rejected["attempts"] == 4
    assert len(rejected["rejected_urls"]) == 4 and "after 4 tries" in rejected["error"]
    assert payload["scenes"][0]["status"] == "generated"
    assert payload["accepted"] == payload["total"] - 1
    assert not next(g for g in payload["gate_results"] if g["id"] == "images.qa")["passed"]

    # Upload a picture for that scene and approve with it: the gate passes.
    picture = tmp_path / "mine.png"
    Image.new("RGB", (64, 36), (200, 40, 40)).save(picture)
    with picture.open("rb") as handle:
        upload = api.post(
            f"/api/projects/{created['id']}/upload/images",
            files={"file": ("mine.png", handle, "image/png")}, data={"scene": "1"},
        )
    assert upload.status_code == 200, upload.text
    response = api.post(f"/api/projects/{created['id']}/stage/images/approve", json={
        "by": "Imran", "edits": {"uploads": [{"scene": 1, "path": upload.json()["path"]}]},
    })
    assert response.status_code == 200, response.text
    project = wait_for_status(api, created["id"], "images", "done")
    payload = api.get(f"/api/projects/{created['id']}/stage/images").json()
    uploaded = payload["scenes"][1]
    assert uploaded["status"] == "approved" and uploaded["source"] == "upload"
    assert uploaded["image_url"] == f"/api/projects/{created['id']}/files/06_images/scene_02.png"
    assert payload["accepted"] == payload["total"]
    assert all(g["passed"] for g in payload["gate_results"] if g["id"] == "images.qa")
    assert (folder / "06_images" / "scene_02.png").is_file()

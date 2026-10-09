"""The whole M1/M2 pipeline end to end through the HTTP API with the REAL stages and every
provider mocked (docs/M1-M2-CONTRACT.md section 10):

    create from ai_pick -> research awaiting_review -> approve -> title awaiting_review (7
    variants) -> approve with chosen_index -> script awaiting_review -> approve -> storyboard
    awaiting_review (scenes) -> approve -> voice awaiting_manual

plus the own_topic and manual_pick starts. Nothing touches the network or spends money.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cashflow_studio.app import create_app
from conftest import AppEnv, channel_payload

SLUG = "kind-ledger"
MOCK_PICK = "mkAv0000005"  # the obvious one-of-ten outlier in the Human Ember fixture
RESEARCH_FILES = (
    "candidates.json", "pick.json", "video.json", "transcript.json", "competitor_thumbnail.jpg",
)
SCRIPT_FILES = ("script.md", "script.json", "speech.json", "originality.json")
STAGE_ORDER = ("research", "title", "script", "storyboard", "voice", "images", "edit", "export")


@pytest.fixture
def api(app_env: AppEnv, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for name in ("LLM", "RESEARCH", "IMAGE", "VOICE"):
        monkeypatch.setenv(f"CFS_{name}_PROVIDER", "mock")
    with TestClient(create_app()) as client:
        stages = {stage.value for stage in client.app.state.engine.stages}
        assert {"research", "title", "script", "storyboard"} <= stages, stages
        response = client.post("/api/channels", json=channel_payload("Kind Ledger"))
        assert response.status_code == 201, response.text
        yield client


def wait_for(
    client: TestClient,
    project_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    timeout: float = 60.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/api/projects/{project_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if predicate(body):
            return body
        failed = {
            name: state["error"]
            for name, state in body["stages"].items()
            if state["status"] == "failed"
        }
        assert not failed, f"a stage failed: {failed}"
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out; stages: {statuses(body)}")
        time.sleep(0.05)


def statuses(project: dict[str, Any]) -> dict[str, str]:
    return {name: state["status"] for name, state in project["stages"].items()}


def at_gate(stage: str, status: str) -> Callable[[dict[str, Any]], bool]:
    return lambda body: body["stages"][stage]["status"] == status and body["current_stage"] == stage


def create(client: TestClient, source: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"channel_slug": SLUG, "format": "long", "source": source}
    body.update(overrides)
    response = client.post("/api/projects", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def approve(
    client: TestClient, project_id: str, stage: str, edits: dict[str, Any] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"by": "Imran", "notes": f"{stage} looks good"}
    if edits:
        body["edits"] = edits
    response = client.post(f"/api/projects/{project_id}/stage/{stage}/approve", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def job_json(project: dict[str, Any]) -> dict[str, Any]:
    return json.loads((Path(project["folder"]) / "job.json").read_text(encoding="utf-8"))


def assert_job_json_matches(client: TestClient, project_id: str) -> dict[str, Any]:
    """job.json (the truth) agrees with what the API returns."""
    api_view = client.get(f"/api/projects/{project_id}").json()
    on_disk = job_json(api_view)
    assert on_disk["id"] == api_view["id"]
    assert on_disk["title"] == api_view["title"]
    assert on_disk["current_stage"] == api_view["current_stage"]
    assert statuses(on_disk) == statuses(api_view)
    assert list(on_disk["stages"]) == list(STAGE_ORDER)
    return api_view


def test_ai_pick_runs_through_every_built_stage(api: TestClient, app_env: AppEnv) -> None:
    created = create(api, {"kind": "ai_pick"})
    assert Path(created["folder"]).parent == app_env.projects_dir
    assert Path(created["folder"]).name.endswith("_kind-ledger_ai-pick")

    # Research: all competitors scanned, ranked together, one AI pick.
    project = wait_for(api, created["id"], at_gate("research", "awaiting_review"))
    # Research learned the video's title, so the folder on disk now says which video it
    # holds instead of "ai-pick" (files are the truth; Explorer must make sense).
    folder = Path(project["folder"])
    assert folder.parent == app_env.projects_dir and folder.is_dir()
    assert project["topic_slug"] != "ai-pick" and not folder.name.endswith("_ai-pick")
    assert folder.name.endswith(f"_kind-ledger_{project['topic_slug']}")
    assert not Path(created["folder"]).exists()
    for name in ("01_research", "02_title", "03_script", "04_storyboard", "05_voice", "08_export"):
        assert (folder / name).is_dir(), name
    for name in RESEARCH_FILES:
        assert (folder / "01_research" / name).is_file(), name
    payload = api.get(f"/api/projects/{created['id']}/stage/research").json()
    assert payload["stage"] == "research" and payload["source_kind"] == "ai_pick"
    assert payload["pick"]["video_id"] == payload["pick_video_id"] == MOCK_PICK
    assert len(payload["channels"]) == 2 and all(c["error"] is None for c in payload["channels"])
    assert payload["candidates"][0]["rank"] == 1
    assert payload["candidates"][0]["video_id"] == MOCK_PICK
    assert project["title"] == payload["pick"]["title"]
    candidates = json.loads((folder / "01_research" / "candidates.json").read_text("utf-8"))
    assert len(candidates["channels"]) == 2 and len(candidates["candidates"]) >= 50
    assert project["stages"]["research"]["attempts"] == 1
    assert_job_json_matches(api, created["id"])

    # Title: seven variants, one recommended, none of the flagged ones.
    approve(api, created["id"], "research")
    project = wait_for(api, created["id"], at_gate("title", "awaiting_review"))
    assert statuses(project)["research"] == "done"
    assert (folder / "02_title" / "title.json").is_file()
    payload = api.get(f"/api/projects/{created['id']}/stage/title").json()
    title_doc = payload["title"]
    assert len(title_doc["variants"]) == 7
    recommended = title_doc["recommended_index"]
    assert recommended is not None
    assert title_doc["variants"][recommended]["flags"] == []
    assert project["title"] == title_doc["variants"][recommended]["title"]
    chosen = 1 if recommended != 1 else 2
    chosen_title = title_doc["variants"][chosen]["title"]

    # Approve with chosen_index: the chosen title becomes the project title.
    approved = approve(api, created["id"], "title", edits={"chosen_index": chosen})
    assert approved["title"] == chosen_title
    saved_title = json.loads((folder / "02_title" / "title.json").read_text("utf-8"))
    assert saved_title["chosen_index"] == chosen and saved_title["chosen_title"] == chosen_title

    # Script: four files, originality gates passed.
    project = wait_for(api, created["id"], at_gate("script", "awaiting_review"))
    assert statuses(project)["title"] == "done"
    for name in SCRIPT_FILES:
        assert (folder / "03_script" / name).is_file(), name
    payload = api.get(f"/api/projects/{created['id']}/stage/script").json()
    assert payload["script"]["title"] == chosen_title
    assert payload["script"]["sections"] and payload["script"]["word_count"] > 100
    assert payload["originality"]["passed"] is True
    assert "## " in payload["script_md"]
    assert project["stages"]["script"]["gate_results"]

    # Storyboard: scenes, variety summary, every transition from the catalogue.
    approve(api, created["id"], "script")
    project = wait_for(api, created["id"], at_gate("storyboard", "awaiting_review"))
    assert (folder / "04_storyboard" / "storyboard.json").is_file()
    payload = api.get(f"/api/projects/{created['id']}/stage/storyboard").json()
    doc = payload["storyboard"]
    assert doc["aspect"] == "16:9" and doc["format"] == "long"
    assert len(doc["scenes"]) >= 3
    types = {t["type"] for t in payload["transitions"]}
    for scene in doc["scenes"]:
        assert scene["transition_out"]["type"] in types
        assert scene["popup"]["text"] is None or len(scene["popup"]["text"].split()) <= 6
    assert doc["variety"]["scenes"] == len(doc["scenes"])

    # Voice is not built yet: the project parks there so a person can add the files.
    approve(api, created["id"], "storyboard")
    project = wait_for(api, created["id"], at_gate("voice", "awaiting_manual"))
    assert statuses(project) == {
        "research": "done", "title": "done", "script": "done", "storyboard": "done",
        "voice": "awaiting_manual", "images": "pending", "edit": "pending", "export": "pending",
    }
    assert project["stages"]["title"]["approved_by"] == "Imran"
    final = assert_job_json_matches(api, created["id"])
    assert final["costs"]["llm_usd"] == 0.0  # the mock is free

    # The list and the review queue see it too.
    rows = api.get("/api/projects").json()
    assert [row["id"] for row in rows] == [created["id"]]
    assert rows[0]["current_stage"] == "voice" and rows[0]["status"] == "awaiting_manual"
    assert rows[0]["title"] == chosen_title

    # The picked competitor video is now "used before" for this channel: it drops out of the
    # eligible list (excluded videos are listed after the eligible ones, so ask for them all).
    candidates = api.get(f"/api/channels/{SLUG}/research/candidates?format=long&limit=500").json()
    used = {c["video_id"]: c for c in candidates["candidates"] if c["used_before"]}
    assert MOCK_PICK in used
    assert used[MOCK_PICK]["excluded_reason"]
    assert candidates["candidates"][0]["video_id"] != MOCK_PICK


def test_own_topic_skips_research_and_starts_at_the_title(api: TestClient) -> None:
    topic = "The waiter who never forgot a face"
    created = create(api, {"kind": "own_topic", "topic_text": topic})
    assert statuses(created)["research"] == "skipped"
    assert created["title"] == "The waiter who never forgot a face"
    assert not any((Path(created["folder"]) / "01_research").iterdir())

    project = wait_for(api, created["id"], at_gate("title", "awaiting_review"))
    payload = api.get(f"/api/projects/{created['id']}/stage/title").json()
    assert len(payload["title"]["variants"]) == 7
    assert payload["title"]["source_title"] == "The waiter who never forgot a face"
    assert project["stages"]["research"]["status"] == "skipped"

    # Approving with typed text works as well as choosing a variant.
    typed = "He Remembered Every Face"
    approved = approve(api, created["id"], "title", edits={"title_text": typed})
    assert approved["title"] == typed
    project = wait_for(api, created["id"], at_gate("script", "awaiting_review"))
    assert project["title"] == "He Remembered Every Face"


def test_manual_pick_uses_the_chosen_video(api: TestClient) -> None:
    other = "mkAv0000012"
    created = create(api, {"kind": "manual_pick", "video_id": other})
    assert created["source"]["video_url"] == f"https://www.youtube.com/watch?v={other}"
    project = wait_for(api, created["id"], at_gate("research", "awaiting_review"))
    folder = Path(project["folder"])  # renamed after the video's title, see the test above
    payload = api.get(f"/api/projects/{created['id']}/stage/research").json()
    assert payload["source_kind"] == "manual_pick"
    assert payload["pick_video_id"] == other
    pick = json.loads((folder / "01_research" / "pick.json").read_text("utf-8"))
    assert pick["kind"] == "manual_pick" and pick["video_id"] == other
    assert project["title"] == pick["title"]

    # The reviewer changes the pick before the title runs.
    approved = approve(api, created["id"], "research", edits={"video_id": MOCK_PICK})
    assert approved["folder"] == project["folder"]  # renamed once, not on every change
    pick = json.loads((folder / "01_research" / "pick.json").read_text("utf-8"))
    assert pick["video_id"] == MOCK_PICK
    assert approved["title"] == pick["title"]
    # The person's choice is the project's source now, so a redo would keep it.
    assert approved["source"]["kind"] == "manual_pick"
    assert approved["source"]["video_id"] == MOCK_PICK
    assert approved["source"]["video_url"] == f"https://www.youtube.com/watch?v={MOCK_PICK}"
    wait_for(api, created["id"], at_gate("title", "awaiting_review"))
    # Only the current pick counts as "used" for the channel; the overridden one is free.
    candidates = api.get(f"/api/channels/{SLUG}/research/candidates?format=long&limit=500").json()
    used = {c["video_id"] for c in candidates["candidates"] if c["used_before"]}
    assert MOCK_PICK in used and other not in used


def test_auto_modes_run_straight_through_to_voice(api: TestClient) -> None:
    created = create(
        api,
        {"kind": "ai_pick"},
        stage_mode_overrides={
            "research": "auto", "title": "auto", "script": "auto", "storyboard": "auto"
        },
    )
    project = wait_for(api, created["id"], at_gate("voice", "awaiting_manual"))
    assert statuses(project)["storyboard"] == "done"
    assert project["title"] and project["title"] != "AI pick (research pending)"
    folder = Path(project["folder"])
    assert (folder / "04_storyboard" / "storyboard.json").is_file()
    assert (folder / "03_script" / "script.md").is_file()

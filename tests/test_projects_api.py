"""/api/projects, /api/jobs and /api/ws end to end with fake stages instead of the real
research and title stages. Everything runs offline."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cashflow_studio.api import projects as projects_api
from cashflow_studio.app import create_app
from cashflow_studio.models.project import StageName
from cashflow_studio.pipeline import jobs
from cashflow_studio.pipeline.stages.base import StageContext, StageError, StageResult
from conftest import AppEnv, channel_payload


class FakeStage:
    def __init__(self, name: str, *, cost: float = 0.0, fail_first: bool = False) -> None:
        self.name = StageName(name)
        self.cost = cost
        self.fail_first = fail_first
        self.calls = 0

    async def run(self, ctx: StageContext) -> StageResult:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise StageError("The competitor list is empty. Add at least one channel.")
        await ctx.report(f"{self.name.value} halfway", 50)
        out = ctx.stage_dir(self.name) / f"{self.name.value}.json"
        out.write_text(json.dumps({"attempt": self.calls, "notes": ctx.notes}))
        if self.name == StageName.research:
            ctx.project.title = "Why kindness pays"
        return StageResult(
            outputs=[out],
            summary=f"{self.name.value} finished",
            cost_usd=self.cost,
            needs_review_payload={
                "stage": self.name.value,
                "variants": [{"index": 0, "title": "A"}, {"index": 1, "title": "B"}],
            },
        )


@pytest.fixture
def api(app_env: AppEnv, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for name in ("LLM", "RESEARCH", "IMAGE", "VOICE"):
        monkeypatch.setenv(f"CFS_{name}_PROVIDER", "mock")
    monkeypatch.setenv("CFS_MAX_PARALLEL_PROJECTS", "2")
    with TestClient(create_app()) as client:
        engine = client.app.state.engine
        engine.stages.clear()  # fakes instead of whatever real stages are installed
        engine.register(FakeStage("research", cost=0.0))
        engine.register(FakeStage("title", cost=0.03))
        engine.register(FakeStage("script"))
        assert client.post("/api/channels", json=channel_payload("Kind Ledger")).status_code == 201
        yield client


def wait_for(
    client: TestClient,
    project_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    timeout: float = 5.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/api/projects/{project_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if predicate(body):
            return body
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out; stages: {stage_statuses(body)}")
        time.sleep(0.02)


def stage_statuses(project: dict[str, Any]) -> dict[str, str]:
    return {name: state["status"] for name, state in project["stages"].items()}


def at_gate(stage: str, status: str) -> Callable[[dict[str, Any]], bool]:
    return lambda body: body["stages"][stage]["status"] == status


def create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    body = {
        "channel_slug": "kind-ledger",
        "format": "long",
        "source": {"kind": "ai_pick"},
        "stage_mode_overrides": {"research": "auto", "title": "review", "script": "auto"},
    }
    body.update(overrides)
    response = client.post("/api/projects", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_ai_pick_round_trip_to_the_first_unbuilt_stage(api: TestClient, app_env: AppEnv) -> None:
    created = create(api)
    assert created["channel_slug"] == "kind-ledger"
    assert created["source"] == {
        "kind": "ai_pick", "video_id": None, "video_url": None, "topic_text": None
    }
    assert created["stage_modes"]["title"] == "review"
    assert created["stage_modes"]["voice"] == "auto"  # from the channel
    assert created["current_stage"] == "research"
    assert Path(created["folder"]).parent == app_env.projects_dir
    assert Path(created["folder"]).name.endswith("_kind-ledger_ai-pick")

    parked = wait_for(api, created["id"], at_gate("title", "awaiting_review"))
    # Research learned the title, so the folder is named after it instead of "ai-pick".
    folder = Path(parked["folder"])
    assert folder.parent == app_env.projects_dir
    assert folder.name.endswith("_kind-ledger_why-kindness-pays")
    assert parked["topic_slug"] == "why-kindness-pays"
    assert (folder / "job.json").is_file()
    assert (folder / "08_export").is_dir()
    assert not Path(created["folder"]).exists()
    assert stage_statuses(parked)["research"] == "done"
    assert parked["title"] == "Why kindness pays"
    assert parked["current_stage"] == "title"
    assert parked["costs"]["llm_usd"] == pytest.approx(0.03)
    assert parked["stages"]["title"]["summary"] == "title finished"
    assert parked["stages"]["title"]["attempts"] == 1
    assert isinstance(parked["stages"]["title"]["history"], list)

    payload = api.get(f"/api/projects/{created['id']}/stage/title")
    assert payload.status_code == 200
    assert payload.json()["variants"][1]["title"] == "B"
    assert api.get(f"/api/projects/{created['id']}/stage/storyboard").json() == {}
    assert api.get(f"/api/projects/{created['id']}/stage/nope").status_code == 422

    rows = api.get("/api/projects").json()
    assert [row["id"] for row in rows] == [created["id"]]
    assert rows[0]["status"] == "awaiting_review" and rows[0]["current_stage"] == "title"
    assert rows[0]["costs_total_usd"] == pytest.approx(0.03)
    assert api.get("/api/projects", params={"status": "awaiting_review"}).json() != []
    assert api.get("/api/projects", params={"status": "done"}).json() == []
    assert api.get("/api/projects", params={"channel_slug": "nobody"}).json() == []

    approve = api.post(
        f"/api/projects/{created['id']}/stage/title/approve",
        json={"by": "Imran", "notes": "Go with B", "edits": {"chosen_index": 1}},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["stages"]["title"]["approved_by"] == "Imran"
    assert approve.json()["stages"]["title"]["notes"] == ["Go with B"]
    final = wait_for(api, created["id"], at_gate("storyboard", "awaiting_manual"))
    assert stage_statuses(final) == {
        "research": "done", "title": "done", "script": "done", "storyboard": "awaiting_manual",
        "voice": "pending", "images": "pending", "edit": "pending", "export": "pending",
    }
    assert final["title"] == "B"  # the chosen variant was copied into the project title
    assert (folder / "02_title" / "edits.json").is_file()

    # A manual stage is passed with Run once the person has done the work by hand.
    run = api.post(f"/api/projects/{created['id']}/run")
    assert run.status_code == 202, run.text
    assert run.json()["started"] is True
    advanced = wait_for(api, created["id"], at_gate("voice", "awaiting_manual"))
    assert stage_statuses(advanced)["storyboard"] == "done"


def test_redo_skip_mode_run_and_conflicts(api: TestClient) -> None:
    created = create(api)
    project_id = created["id"]
    wait_for(api, project_id, at_gate("title", "awaiting_review"))

    # Run does not get past a review gate.
    run = api.post(f"/api/projects/{project_id}/run")
    assert run.status_code == 202
    assert run.json()["started"] is False and "review" in run.json()["message"]

    # Approving a stage that has not run yet is a conflict with a reason.
    conflict = api.post(
        f"/api/projects/{project_id}/stage/script/approve", json={"by": "Imran"}
    )
    assert conflict.status_code == 409
    assert "cannot be approved" in conflict.json()["detail"]

    redo = api.post(
        f"/api/projects/{project_id}/stage/title/redo",
        json={"by": "Imran", "notes": "Shorter please"},
    )
    assert redo.status_code == 200, redo.text
    parked = wait_for(
        api, project_id, lambda b: b["stages"]["title"]["attempts"] == 2
        and b["stages"]["title"]["status"] == "awaiting_review",
    )
    assert parked["stages"]["title"]["notes"] == ["Shorter please"]
    written = json.loads(
        (Path(parked["folder"]) / "02_title" / "title.json").read_text(encoding="utf-8")
    )
    assert written == {"attempt": 2, "notes": ["Shorter please"]}

    mode = api.put(
        f"/api/projects/{project_id}/stage/script/mode", json={"mode": "manual"}
    )
    assert mode.status_code == 200, mode.text
    assert mode.json()["stage_modes"]["script"] == "manual"
    assert api.put(
        f"/api/projects/{project_id}/stage/script/mode", json={"mode": "sometimes"}
    ).status_code == 422

    skip = api.post(
        f"/api/projects/{project_id}/stage/title/skip", json={"by": "Imran", "notes": "Keep mine"}
    )
    assert skip.status_code == 200, skip.text
    assert skip.json()["stages"]["title"]["status"] == "skipped"
    parked = wait_for(api, project_id, at_gate("script", "awaiting_manual"))
    assert "manual" in " ".join(parked["stages"]["script"]["history"])


def test_failed_stage_reports_a_plain_english_error_and_run_retries(api: TestClient) -> None:
    engine = api.app.state.engine
    engine.register(FakeStage("research", fail_first=True))
    created = create(api)
    failed = wait_for(api, created["id"], at_gate("research", "failed"))
    assert failed["stages"]["research"]["error"] == (
        "The competitor list is empty. Add at least one channel."
    )
    assert api.get("/api/projects", params={"status": "failed"}).json()[0]["id"] == created["id"]
    run = api.post(f"/api/projects/{created['id']}/run")
    assert run.status_code == 202 and run.json()["started"] is True
    recovered = wait_for(api, created["id"], at_gate("title", "awaiting_review"))
    assert recovered["stages"]["research"]["status"] == "done"
    assert recovered["stages"]["research"]["attempts"] == 2
    assert recovered["stages"]["research"]["error"] is None


def test_own_topic_and_manual_pick_sources(api: TestClient) -> None:
    own = create(api, source={"kind": "own_topic", "topic_text": "Small acts, big change"})
    assert own["stages"]["research"]["status"] == "skipped"
    assert own["title"] == "Small acts, big change"
    assert own["topic_slug"] == "small-acts-big-change"
    wait_for(api, own["id"], at_gate("title", "awaiting_review"))

    picked = create(api, source={"kind": "manual_pick", "video_id": "dQw4w9WgXcQ"})
    assert picked["source"]["video_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    wait_for(api, picked["id"], at_gate("title", "awaiting_review"))

    assert len(api.get("/api/projects").json()) == 2


def test_create_validation_errors(api: TestClient) -> None:
    missing_channel = api.post(
        "/api/projects",
        json={"channel_slug": "nobody", "format": "long", "source": {"kind": "ai_pick"}},
    )
    assert missing_channel.status_code == 404
    assert "no channel" in missing_channel.json()["detail"]

    no_topic = api.post(
        "/api/projects",
        json={"channel_slug": "kind-ledger", "format": "long", "source": {"kind": "own_topic"}},
    )
    assert no_topic.status_code == 422
    assert "Type the topic" in no_topic.json()["detail"]

    bad_kind = api.post(
        "/api/projects",
        json={"channel_slug": "kind-ledger", "format": "long", "source": {"kind": "magic"}},
    )
    assert bad_kind.status_code == 422
    assert isinstance(bad_kind.json()["detail"], list)

    assert api.get("/api/projects/does-not-exist").status_code == 404
    assert api.post("/api/projects/does-not-exist/run").status_code == 404
    assert api.delete("/api/projects/does-not-exist").status_code == 404


def test_open_folder_runs_explorer_for_the_project_folder(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []
    monkeypatch.setattr(projects_api, "open_folder", lambda folder: opened.append(folder))
    created = create(api)
    current = wait_for(api, created["id"], at_gate("title", "awaiting_review"))["folder"]
    response = api.post(f"/api/projects/{created['id']}/open-folder")
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "folder": current}
    assert opened == [Path(current)]
    assert api.post("/api/projects/nope/open-folder").status_code == 404


def test_delete_archives_the_folder(api: TestClient, app_env: AppEnv) -> None:
    created = create(api)
    folder = Path(wait_for(api, created["id"], at_gate("title", "awaiting_review"))["folder"])
    response = api.delete(f"/api/projects/{created['id']}")
    assert response.status_code == 204
    assert response.content == b""
    assert not folder.exists()
    archived = app_env.projects_dir / "_archived" / folder.name
    assert (archived / "job.json").is_file()
    assert (archived / "02_title" / "title.json").is_file()
    assert api.get(f"/api/projects/{created['id']}").status_code == 404
    assert api.get("/api/projects").json() == []
    assert api.delete(f"/api/projects/{created['id']}").status_code == 404


def test_websocket_streams_project_updates_progress_and_job_logs(api: TestClient) -> None:
    with api.websocket_connect("/api/ws") as ws:
        created = create(api)
        seen_types: set[str] = set()
        progress_messages: list[str] = []
        for _ in range(60):
            event = ws.receive_json()
            assert event["ts"]
            if event.get("project_id") != created["id"]:
                continue
            seen_types.add(event["type"])
            if event["type"] == "stage.progress":
                progress_messages.append(event["message"])
            if event["type"] == "project.update":
                assert event["project"]["id"] == created["id"]
                assert set(event["stages"]) == {s.value for s in StageName}
                if event["stages"]["title"] == "awaiting_review":
                    break
        assert seen_types == {"project.update", "stage.progress"}
        assert "research halfway" in progress_messages

        job_id = jobs.create_job("research.scan", message="Queued")
        jobs.update(job_id, progress=40, message="Listing videos")
        logs = [ws.receive_json() for _ in range(2)]
        assert [log["type"] for log in logs] == ["job.log", "job.log"]
        assert logs[1]["job_id"] == job_id and logs[1]["progress"] == 40
        assert logs[1]["status"] == "running" and logs[1]["message"] == "Listing videos"


def test_jobs_endpoint_reports_the_registry(api: TestClient) -> None:
    assert api.get("/api/jobs/unknown").status_code == 404
    job_id = jobs.create_job("research.scan", message="Queued")
    queued = api.get(f"/api/jobs/{job_id}").json()
    assert queued["status"] == "queued" and queued["progress"] == 0
    assert queued["kind"] == "research.scan"
    jobs.update(job_id, progress=55.56, message="Fetching details")
    running = api.get(f"/api/jobs/{job_id}").json()
    assert running["status"] == "running" and running["progress"] == 55.6
    jobs.finish(job_id, result={"candidates": 3})
    done = api.get(f"/api/jobs/{job_id}").json()
    assert done == done | {"status": "done", "progress": 100, "result": {"candidates": 3}}
    assert done["error"] is None

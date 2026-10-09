"""/api/channels/{slug}/research/scan and /candidates with the mock provider."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cashflow_studio.models.research import Tab, TabListing
from cashflow_studio.pipeline.jobs import registry
from cashflow_studio.research import jobs as research_jobs
from cashflow_studio.research.errors import ResearchBlocked
from cashflow_studio.research.mock import MockProvider
from conftest import AppEnv, channel_payload

CANDIDATE_FIELDS = {
    "video_id", "url", "title", "channel_name", "channel_url", "channel_id", "format",
    "views", "views_exact", "published_at", "age_days", "duration_s", "baseline_views",
    "outlier_score", "vpd", "vpd_ratio", "sub_ratio", "label", "thumbnail_url",
    "used_before", "excluded_reason", "rank",
}


@pytest.fixture
def research_client(
    app_env: AppEnv, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("CFS_RESEARCH_PROVIDER", "mock")
    registry.clear()
    assert client.post("/api/channels", json=channel_payload("Kind Ledger")).status_code == 201
    yield client
    registry.clear()


def wait_for(job_id: str) -> dict[str, Any]:
    job = registry.wait(job_id, timeout=20)
    assert not job.is_active, job.to_dict()
    return job.to_dict()


def test_scan_runs_as_a_job_and_candidates_are_ranked_across_competitors(
    research_client: TestClient, app_env: AppEnv
) -> None:
    started = research_client.post("/api/channels/kind-ledger/research/scan", json={"force": True})
    assert started.status_code == 202, started.text
    assert set(started.json()) == {"job_id"}
    job = wait_for(started.json()["job_id"])
    assert job["kind"] == "research.scan"
    assert job["status"] == "done" and job["progress"] == 100 and job["error"] is None
    result = job["result"]
    assert result["channel_slug"] == "kind-ledger"
    assert result["videos_found"] == 80  # 2 competitors x (30 videos + 10 shorts)
    assert result["candidates_long"] == 60 and result["candidates_shorts"] == 20
    assert [(c["name"], c["videos_found"], c["error"]) for c in result["channels"]] == [
        ("Human Ember", 40, None),
        ("The Gentle Hour", 40, None),
    ]

    response = research_client.get("/api/channels/kind-ledger/research/candidates")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"scanned_at", "format", "channels", "candidates", "pick", "pick_video_id"}
    assert body["scanned_at"] is not None and body["format"] == "long"
    # The page highlights the same video the research stage would start from.
    assert body["pick_video_id"] == "mkAv0000005"
    assert body["pick"]["video_id"] == "mkAv0000005" and body["pick"]["rank"] == 1
    assert [set(c) for c in body["channels"]] == [
        {"name", "url", "id", "videos_found", "last_scanned", "error"}
    ] * 2
    assert body["channels"][0]["id"] == "UCFjva5hxOFoj2ViNgSuEQJg"
    assert body["channels"][0]["videos_found"] == 30  # only the long-form tab counts here
    assert len(body["candidates"]) == 50
    first = body["candidates"][0]
    assert set(first) == CANDIDATE_FIELDS
    assert first["rank"] == 1 and first["label"] == "one-of-ten"
    assert first["video_id"] == "mkAv0000005" and first["channel_name"] == "Human Ember"
    assert [c["rank"] for c in body["candidates"]] == list(range(1, 51))
    scores = [c["outlier_score"] for c in body["candidates"] if not c["excluded_reason"]]
    assert scores == sorted(scores, reverse=True)
    assert {c["channel_name"] for c in body["candidates"]} == {"Human Ember", "The Gentle Hour"}

    # The scan filled in the competitor ids and counts on the channel file.
    channel = research_client.get("/api/channels/kind-ledger").json()
    assert [(c["id"], c["videos_found"]) for c in channel["competitors"]] == [
        ("UCFjva5hxOFoj2ViNgSuEQJg", 40),
        ("UCgentlehour000000000001", 40),
    ]
    assert all(c["last_scanned"] for c in channel["competitors"])
    assert (Path(app_env.app_data_dir) / "db.sqlite").is_file()


def test_candidates_before_any_scan_say_so_without_touching_youtube(
    research_client: TestClient,
) -> None:
    body = research_client.get("/api/channels/kind-ledger/research/candidates").json()
    assert body["scanned_at"] is None
    assert body["candidates"] == []
    assert body["pick"] is None and body["pick_video_id"] is None
    assert [c["error"] for c in body["channels"]] == ["Not scanned yet", "Not scanned yet"]
    assert [c["videos_found"] for c in body["channels"]] == [0, 0]


def test_candidates_honour_format_and_limit(research_client: TestClient) -> None:
    job_id = research_client.post("/api/channels/kind-ledger/research/scan").json()["job_id"]
    wait_for(job_id)
    shorts = research_client.get(
        "/api/channels/kind-ledger/research/candidates", params={"format": "shorts", "limit": 3}
    ).json()
    assert shorts["format"] == "shorts" and len(shorts["candidates"]) == 3
    assert all(c["format"] == "shorts" for c in shorts["candidates"])
    assert shorts["candidates"][0]["label"] == "one-of-ten"
    assert [c["videos_found"] for c in shorts["channels"]] == [10, 10]

    everything = research_client.get(
        "/api/channels/kind-ledger/research/candidates", params={"limit": 500}
    ).json()
    assert len(everything["candidates"]) == 60
    excluded = [c for c in everything["candidates"] if c["excluded_reason"]]
    assert excluded and all(c["rank"] > 54 for c in excluded)

    assert research_client.get(
        "/api/channels/kind-ledger/research/candidates", params={"limit": 501}
    ).status_code == 422
    assert research_client.get(
        "/api/channels/kind-ledger/research/candidates", params={"format": "vertical"}
    ).status_code == 422


def test_scan_rejects_unknown_channels_and_channels_without_competitors(
    research_client: TestClient,
) -> None:
    assert research_client.post("/api/channels/nobody-here/research/scan").status_code == 404
    assert research_client.get("/api/channels/nobody-here/research/candidates").status_code == 404
    lonely = channel_payload("Lonely Channel", competitors=[])
    assert research_client.post("/api/channels", json=lonely).status_code == 201
    response = research_client.post("/api/channels/lonely-channel/research/scan")
    assert response.status_code == 422
    assert "competitor" in response.json()["detail"]


def test_a_second_scan_while_one_runs_returns_the_same_job(
    research_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    inner = MockProvider()

    class SlowProvider:
        name = "slow"

        def list_tab(self, channel_url: str, tab: Tab, *, max_videos: int = 200) -> TabListing:
            assert release.wait(timeout=10)
            return inner.list_tab(channel_url, tab, max_videos=max_videos)

        def __getattr__(self, name: str) -> Any:
            return getattr(inner, name)

    monkeypatch.setattr(
        research_jobs, "build_provider", lambda settings, config=None, **kw: SlowProvider()
    )
    first = research_client.post("/api/channels/kind-ledger/research/scan").json()["job_id"]
    second = research_client.post("/api/channels/kind-ledger/research/scan").json()["job_id"]
    assert first == second
    active = registry.get(first)
    assert active is not None and active.is_active
    release.set()
    assert wait_for(first)["status"] == "done"
    third = research_client.post("/api/channels/kind-ledger/research/scan").json()["job_id"]
    assert third != first
    assert wait_for(third)["status"] == "done"


def test_a_blocked_scan_fails_the_job_with_a_plain_message(
    research_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BlockedProvider:
        name = "blocked"

        def list_tab(self, channel_url: str, tab: Tab, *, max_videos: int = 200) -> TabListing:
            raise ResearchBlocked(
                "YouTube is asking this computer to slow down or sign in, so research is paused."
            )

    monkeypatch.setattr(
        research_jobs, "build_provider", lambda settings, config=None, **kw: BlockedProvider()
    )
    job_id = research_client.post("/api/channels/kind-ledger/research/scan").json()["job_id"]
    job = wait_for(job_id)
    assert job["status"] == "failed"
    assert job["error"].startswith("YouTube is asking this computer to slow down")
    assert "Traceback" not in job["error"]

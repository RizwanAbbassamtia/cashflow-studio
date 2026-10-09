"""Live smoke test: runs the title stage once against the real Claude API.

Skipped unless ``ANTHROPIC_API_KEY`` is set (``pytest -m live``). It spends a few cents.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cashcow_studio.config import Settings
from cashcow_studio.llm import build_llm_client
from cashcow_studio.llm.log import list_llm_calls
from cashcow_studio.models.channel import Channel
from cashcow_studio.models.project import Project, ProjectSource
from cashcow_studio.pipeline.stages.base import StageContext
from cashcow_studio.pipeline.stages.title import TitleStage

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
def test_title_stage_live(app_env, monkeypatch) -> None:
    monkeypatch.setenv("CCS_LLM_PROVIDER", "anthropic")
    settings = Settings()
    folder = app_env.projects_dir / "live-title"
    (folder / "01_research").mkdir(parents=True)
    (folder / "01_research" / "pick.json").write_text(
        json.dumps({
            "kind": "ai_pick", "video_id": "live1",
            "title": "The Nurse Who Kept a Promise for 30 Years",
            "channel_name": "Human Ember",
            "candidate": {"views": 2_400_000, "outlier_score": 9.5},
        }),
        encoding="utf-8",
    )
    channel = Channel.model_validate({
        "slug": "kind-ledger",
        "channel": {"name": "Kind Ledger", "niche": "Kindness and emotional stories",
                    "audience": "Adults 35+, English-speaking"},
    })
    now = datetime.now(UTC)
    project = Project(
        id="live-title", channel_slug="kind-ledger", topic_slug="nurse-promise", format="long",
        created_at=now, updated_at=now, folder=str(folder),
        source=ProjectSource(kind="ai_pick", video_id="live1"),
    )
    ctx = StageContext(project=project, channel=channel, settings=settings, folder=folder,
                       providers={"llm": build_llm_client(settings)})
    result = asyncio.run(TitleStage().run(ctx))
    doc = json.loads(Path(result.outputs[0]).read_text(encoding="utf-8"))
    assert len(doc["variants"]) == 7
    assert doc["recommended_index"] is not None
    assert project.title == doc["variants"][doc["recommended_index"]]["title"]
    assert result.cost_usd > 0
    rows = list_llm_calls(settings.app_data_dir, project.id)
    assert rows and rows[0]["model"].startswith("claude-")

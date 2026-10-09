"""Project folders: layout, atomic job.json writes, the SQLite index and archiving."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cashflow_studio.config import Settings
from cashflow_studio.models.project import Project, ProjectSource, StageName
from cashflow_studio.storage import db
from cashflow_studio.storage.project_store import (
    STAGE_DIRS,
    ProjectNotFound,
    ProjectStore,
    topic_slug,
)
from conftest import AppEnv


@pytest.fixture
def store(app_env: AppEnv) -> ProjectStore:
    settings = Settings()
    settings.ensure_dirs()
    return ProjectStore(settings.projects_dir, settings.app_data_dir)


def new_project(
    store: ProjectStore, topic: str = "Why kindness pays", slug: str = "kind-ledger"
) -> Project:
    now = datetime.now(UTC)
    folder = store.new_folder(slug, topic, now)
    project = Project(
        id=str(uuid.uuid4()),
        channel_slug=slug,
        topic_slug=topic_slug(topic),
        title=topic,
        created_at=now,
        updated_at=now,
        folder=str(folder),
        source=ProjectSource(kind="own_topic", topic_text=topic),
    )
    return store.create(project)


def test_create_builds_the_folder_layout(store: ProjectStore, app_env: AppEnv) -> None:
    project = new_project(store)
    folder = Path(project.folder)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert folder.parent == app_env.projects_dir
    assert folder.name == f"{today}_kind-ledger_why-kindness-pays"
    assert sorted(p.name for p in folder.iterdir() if p.is_dir()) == [
        "01_research", "02_title", "03_script", "04_storyboard",
        "05_voice", "06_images", "07_edit", "08_export",
    ]
    assert set(STAGE_DIRS) == set(StageName)
    on_disk = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    assert on_disk["id"] == project.id
    assert on_disk["stages"]["research"]["status"] == "pending"
    assert on_disk["folder"] == str(folder)

    # Same channel, same topic, same day: a numbered folder instead of a clash.
    second = new_project(store)
    assert Path(second.folder).name == f"{today}_kind-ledger_why-kindness-pays-2"
    third = new_project(store)
    assert Path(third.folder).name.endswith("-3")


def test_topic_slug_is_short_and_safe() -> None:
    assert topic_slug("  Why Kindness PAYS off!  ") == "why-kindness-pays-off"
    assert len(topic_slug("word " * 40)) <= 40
    assert topic_slug("!!!") == "topic"
    assert topic_slug(None, fallback="ai-pick") == "ai-pick"


def test_save_is_atomic_and_leaves_no_temp_files(
    store: ProjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = new_project(store)
    folder = Path(project.folder)
    project.title = "Changed"
    store.save(project)
    assert json.loads((folder / "job.json").read_text(encoding="utf-8"))["title"] == "Changed"
    assert [p.name for p in folder.iterdir() if p.name.startswith(".tmp-")] == []
    before = (folder / "job.json").read_text(encoding="utf-8")

    real_replace = os.replace

    def broken_replace(src: str, dst: str) -> None:
        if str(dst).endswith("job.json"):
            raise OSError("disk full")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", broken_replace)
    project.title = "Lost"
    with pytest.raises(OSError, match="disk full"):
        store.save(project)
    monkeypatch.undo()
    # The old file is intact and the half-written temp file is gone.
    assert (folder / "job.json").read_text(encoding="utf-8") == before
    assert [p.name for p in folder.iterdir() if p.name.startswith(".tmp-")] == []


def test_list_and_index_follow_the_folders(store: ProjectStore, app_env: AppEnv) -> None:
    first = new_project(store, "First topic")
    second = new_project(store, "Second topic", slug="other-channel")
    second.stages[StageName.research].status = "awaiting_review"
    store.save(second)

    rows = store.list()
    assert [row.id for row in rows] == [second.id, first.id]  # newest first
    assert rows[0].status == "awaiting_review" and rows[1].status == "pending"
    assert [row.id for row in store.list(channel_slug="kind-ledger")] == [first.id]
    assert [row.id for row in store.list(status="awaiting_review")] == [second.id]
    assert store.list(channel_slug="nobody") == []

    with db.connect(app_env.app_data_dir) as conn:
        indexed = {row["id"]: row for row in conn.execute("SELECT * FROM projects")}
    assert set(indexed) == {first.id, second.id}
    assert indexed[first.id]["folder"] == first.folder
    assert json.loads(indexed[first.id]["status"])["current_stage"] == "research"

    # A lost index row is rebuilt from the files.
    with db.connect(app_env.app_data_dir) as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (first.id,))
    assert store.get(first.id).title == "First topic"
    with db.connect(app_env.app_data_dir) as conn:
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 2

    # A folder removed by hand disappears from the list and the index.
    import shutil

    shutil.rmtree(first.folder)
    assert [row.id for row in store.list()] == [second.id]
    with db.connect(app_env.app_data_dir) as conn:
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    with pytest.raises(ProjectNotFound):
        store.get(first.id)


def test_a_folder_moved_by_hand_is_found_and_relabelled(
    store: ProjectStore, app_env: AppEnv
) -> None:
    project = new_project(store, "Moved topic")
    old = Path(project.folder)
    new = old.with_name(old.name + "-renamed")
    old.rename(new)
    found = store.get(project.id)
    assert found.folder == str(new)
    assert [row.id for row in store.list()] == [project.id]


def test_archive_moves_the_folder_and_never_deletes(
    store: ProjectStore, app_env: AppEnv
) -> None:
    project = new_project(store)
    folder = Path(project.folder)
    (folder / "02_title" / "title.json").write_text("{}")
    destination = store.archive(project.id)
    assert destination == app_env.projects_dir / "_archived" / folder.name
    assert not folder.exists()
    assert (destination / "job.json").is_file()
    assert (destination / "02_title" / "title.json").is_file()
    assert store.list() == []
    with pytest.raises(ProjectNotFound):
        store.get(project.id)
    with pytest.raises(ProjectNotFound):
        store.archive(project.id)

    # The next project with the same name gets a fresh folder, and archiving it again does
    # not overwrite the first archive.
    again = new_project(store)
    assert Path(again.folder).name == folder.name + "-2"
    Path(again.folder).rename(folder)  # pretend it had the original name
    again.folder = str(folder)
    store.save(again)
    second = store.archive(again.id)
    assert second == app_env.projects_dir / "_archived" / f"{folder.name}-2"
    assert (destination / "job.json").is_file()

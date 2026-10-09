"""Job registry (thread-safe progress records) and the event bus behind the WebSocket."""

from __future__ import annotations

import asyncio
import threading

from cashflow_studio.pipeline import jobs
from cashflow_studio.pipeline.events import EventBus
from cashflow_studio.pipeline.jobs import JobRegistry


def test_job_lifecycle_through_the_module_shortcuts() -> None:
    job_id = jobs.create_job("research.scan", message="Queued")
    job = jobs.get(job_id)
    assert job is not None and job.status == "queued" and job.kind == "research.scan"

    updated = jobs.update(job_id, progress=25, message="Listing videos")
    assert updated is not None
    assert updated.status == "running" and updated.progress == 25
    assert updated.message == "Listing videos"

    finished = jobs.finish(job_id, result={"videos": 12})
    assert finished is not None
    assert finished.status == "done" and finished.progress == 100
    assert finished.result == {"videos": 12}
    assert finished.error is None
    # A late progress call after the end changes nothing.
    late = jobs.update(job_id, progress=5, message="late")
    assert late is not None and late.status == "done" and late.progress == 100

    failed_id = jobs.create_job("research.scan")
    failed = jobs.finish(failed_id, error="YouTube asked us to slow down. Try again later.")
    assert failed is not None and failed.status == "failed"
    assert failed.error == "YouTube asked us to slow down. Try again later."
    assert failed.message == failed.error

    assert jobs.get("nope") is None
    assert jobs.update("nope", progress=1) is None
    assert jobs.finish("nope") is None

    view = finished.to_dict()
    assert set(view) >= {"id", "kind", "status", "progress", "message", "result", "error"}


def test_registry_is_thread_safe_and_notifies_listeners() -> None:
    registry = JobRegistry()
    seen: list[str] = []
    unsubscribe = registry.subscribe(lambda job: seen.append(job.status))
    job = registry.create("stress")

    def worker(n: int) -> None:
        for i in range(200):
            registry.update(job.id, progress=(i % 100), message=f"w{n}-{i}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    registry.finish(job.id, {"ok": True})
    final = registry.get(job.id)
    assert final is not None and final.status == "done" and final.result == {"ok": True}
    assert len(seen) == 1 + 8 * 200 + 1
    unsubscribe()
    registry.update(job.id, message="after")
    assert len(seen) == 1 + 8 * 200 + 1


def test_event_bus_delivers_from_any_thread_and_drops_oldest_when_full() -> None:
    async def main() -> None:
        bus = EventBus()
        queue = bus.subscribe(maxsize=3)
        assert bus.subscriber_count == 1

        bus.publish({"type": "stage.progress", "message": "on the loop"})
        first = await asyncio.wait_for(queue.get(), 1)
        assert first["message"] == "on the loop" and "ts" in first

        thread = threading.Thread(
            target=lambda: bus.publish({"type": "job.log", "message": "from a thread"})
        )
        thread.start()
        thread.join()
        second = await asyncio.wait_for(queue.get(), 1)
        assert second["message"] == "from a thread"

        for n in range(5):
            bus.publish({"type": "stage.progress", "n": n})
        got = [queue.get_nowait()["n"] for _ in range(3)]
        assert got == [2, 3, 4]  # the two oldest were dropped for this slow subscriber
        assert queue.empty()

        bus.unsubscribe(queue)
        assert bus.subscriber_count == 0
        bus.publish({"type": "stage.progress", "n": 99})  # nobody listens; never raises
        assert bus.history[-1]["n"] == 99

    asyncio.run(main())


def test_bus_publish_outside_any_loop_is_harmless() -> None:
    bus = EventBus()
    event = bus.publish({"type": "job.log", "message": "no loop"})
    assert event["type"] == "job.log" and event["ts"]

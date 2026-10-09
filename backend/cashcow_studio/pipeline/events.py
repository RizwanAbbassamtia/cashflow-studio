"""In-process event bus behind the WebSocket at ``/api/ws``.

Three event types are broadcast as JSON objects (docs/M1-M2-CONTRACT.md section 1)::

    {"type": "project.update", "project_id", "channel_slug", "current_stage", "status",
     "title", "updated_at", "stages": {stage: status}, "project": ProjectSummary, "ts"}
    {"type": "stage.progress", "project_id", "stage", "message", "pct", "ts"}
    {"type": "job.log", "job_id", "kind", "status", "progress", "message", "error", "ts"}

Subscribers are ``asyncio.Queue`` objects, one per WebSocket connection. ``publish`` may be
called from any thread: a subscriber's queue is bound to the event loop it was created on and
events from other threads are handed over with ``call_soon_threadsafe``. A slow subscriber
loses its oldest events rather than blocking the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

Event = dict[str, Any]

QUEUE_SIZE = 500
HISTORY_SIZE = 200


@dataclass(frozen=True)
class _Subscriber:
    queue: asyncio.Queue[Event]
    loop: asyncio.AbstractEventLoop


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[int, _Subscriber] = {}
        self._lock = threading.Lock()
        self.history: deque[Event] = deque(maxlen=HISTORY_SIZE)
        """The most recent events, for debugging and tests."""

    # Subscriptions ----------------------------------------------------------------------

    def subscribe(self, maxsize: int = QUEUE_SIZE) -> asyncio.Queue[Event]:
        """Register the calling event loop's queue. Call from a coroutine."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers[id(queue)] = _Subscriber(queue=queue, loop=loop)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        with self._lock:
            self._subscribers.pop(id(queue), None)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # Publishing ---------------------------------------------------------------------------

    def publish(self, event: Event) -> Event:
        """Deliver one event to every subscriber. Safe from any thread; never raises."""
        event = dict(event)
        event.setdefault("ts", datetime.now(UTC).isoformat())
        self.history.append(event)
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        with self._lock:
            subscribers = list(self._subscribers.values())
        for subscriber in subscribers:
            try:
                if subscriber.loop is current:
                    _deliver(subscriber.queue, event)
                elif subscriber.loop.is_closed():
                    self.unsubscribe(subscriber.queue)
                else:
                    subscriber.loop.call_soon_threadsafe(_deliver, subscriber.queue, event)
            except RuntimeError:
                # The subscriber's loop stopped between the check and the call.
                self.unsubscribe(subscriber.queue)
        return event

    # Convenience builders -----------------------------------------------------------------

    def project_update(self, project: Any) -> Event:
        """``project.update`` for a :class:`~cashcow_studio.models.project.Project`."""
        from ..models.project import summary_of  # local import: models stay import-light

        summary = summary_of(project)
        return self.publish(
            {
                "type": "project.update",
                "project_id": project.id,
                "channel_slug": project.channel_slug,
                "title": project.title,
                "current_stage": project.current_stage.value,
                "status": summary.status,
                "updated_at": project.updated_at.isoformat(),
                "stages": {name.value: state.status for name, state in project.stages.items()},
                "project": summary.model_dump(mode="json"),
            }
        )

    def stage_progress(
        self, project_id: str, stage: str, message: str, pct: float | None = None
    ) -> Event:
        return self.publish(
            {
                "type": "stage.progress",
                "project_id": project_id,
                "stage": stage,
                "message": message,
                "pct": None if pct is None else round(float(pct), 1),
            }
        )

    def job_log(self, job: Any) -> Event:
        """``job.log`` for a :class:`~cashcow_studio.pipeline.jobs.Job`."""
        return self.publish(
            {
                "type": "job.log",
                "job_id": job.id,
                "kind": job.kind,
                "status": job.status,
                "progress": round(job.progress, 1),
                "message": job.message,
                "error": job.error,
            }
        )


def _deliver(queue: asyncio.Queue[Event], event: Event) -> None:
    """Put without blocking; a full queue drops its oldest event."""
    while True:
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - raced with the consumer
                return

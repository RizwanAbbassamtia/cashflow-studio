"""WebSocket event stream at ``/api/ws``.

Every event is one JSON object with a ``type`` of ``project.update``, ``stage.progress`` or
``job.log`` (see :mod:`cashcow_studio.pipeline.events`). The browser may send anything (a
"ping" for example); incoming text is ignored, it only serves to notice a closed connection.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..pipeline.events import EventBus

log = logging.getLogger(__name__)

router = APIRouter(tags=["events"])


@router.websocket("/api/ws")
async def event_stream(websocket: WebSocket) -> None:
    bus: EventBus = websocket.app.state.engine.events
    await websocket.accept()
    queue = bus.subscribe()
    sender = asyncio.create_task(_send_events(websocket, queue))
    receiver = asyncio.create_task(_drain_incoming(websocket))
    try:
        done, pending = await asyncio.wait(
            {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception() if not task.cancelled() else None
            if exc is not None and not isinstance(exc, WebSocketDisconnect | RuntimeError):
                log.warning("WebSocket stream ended with an error: %s", exc)
    finally:
        bus.unsubscribe(queue)
        for task in (sender, receiver):
            if not task.done():
                task.cancel()


async def _send_events(websocket: WebSocket, queue: asyncio.Queue) -> None:
    while True:
        event = await queue.get()
        await websocket.send_json(event)


async def _drain_incoming(websocket: WebSocket) -> None:
    while True:
        await websocket.receive_text()

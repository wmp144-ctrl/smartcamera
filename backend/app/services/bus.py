"""In-process pub/sub used to push live events to WebSocket clients.

Detection runs on worker threads, so publishing must be thread-safe: we hop
back onto the event loop with ``call_soon_threadsafe`` when a loop is bound.
A small ring buffer lets a freshly connected client render "what just happened"
without waiting for the next event.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from app.core.timeutil import utcnow

logger = logging.getLogger(__name__)

# Topics
TOPIC_EVENT = "event"
TOPIC_ALERT = "alert"
TOPIC_STATUS = "status"
TOPIC_DETECTION = "detection"
TOPIC_STATS = "stats"
TOPIC_SYSTEM = "system"

ALL_TOPICS = (TOPIC_EVENT, TOPIC_ALERT, TOPIC_STATUS, TOPIC_DETECTION, TOPIC_STATS, TOPIC_SYSTEM)


class EventBus:
    def __init__(self, *, history: int = 200, queue_size: int = 500) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._history: deque[dict[str, Any]] = deque(maxlen=history)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._queue_size = queue_size

    # -- lifecycle ---------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # -- publishing --------------------------------------------------------
    def publish(self, topic: str, data: dict[str, Any]) -> None:
        message = {"topic": topic, "ts": utcnow().isoformat(), "data": data}
        with self._lock:
            self._history.append(message)
            queues = list(self._subscribers)
        for queue in queues:
            loop = self._loop
            if loop is not None and loop.is_running():
                try:
                    loop.call_soon_threadsafe(self._offer, queue, message)
                except RuntimeError:  # loop closed while shutting down
                    continue
            else:
                # No event loop has been bound yet, so the caller must already
                # be on the loop thread (in the running app the manager binds
                # the loop before any camera thread exists; tests publish from
                # the loop itself). Touching a queue from a *foreign* thread is
                # only safe through ``call_soon_threadsafe`` above.
                self._offer(queue, message)

    def _offer(self, queue: asyncio.Queue, message: dict[str, Any]) -> None:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            # Drop the oldest message so the stream always makes progress.
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:  # pragma: no cover - extreme backpressure
                pass

    # -- subscribing -------------------------------------------------------
    def history(self, topics: list[str] | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._history)
        if topics:
            items = [m for m in items if m["topic"] in topics]
        return items[-limit:]

    @asynccontextmanager
    async def subscription(
        self, topics: list[str] | None = None, *, replay: int = 0
    ) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        with self._lock:
            self._subscribers.add(queue)
        try:
            for message in self.history(topics, replay) if replay else []:
                self._offer(queue, message)
            yield queue
        finally:
            with self._lock:
                self._subscribers.discard(queue)

    async def stream(
        self, topics: list[str] | None = None, *, replay: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        async with self.subscription(topics, replay=replay) as queue:
            while True:
                message = await queue.get()
                if topics and message["topic"] not in topics:
                    continue
                yield message

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()


bus = EventBus()


def event_payload(
    *,
    kind: str,
    camera_id: int,
    camera_name: str,
    message: str,
    occurred_at: datetime | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": kind,
        "camera_id": camera_id,
        "camera_name": camera_name,
        "message": message,
        "occurred_at": (occurred_at or utcnow()).isoformat(),
    }
    payload.update(extra)
    return payload

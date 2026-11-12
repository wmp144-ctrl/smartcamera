"""Live event bus and WebSocket tests."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from starlette.testclient import TestClient

from app.services.bus import TOPIC_ALERT, TOPIC_EVENT, TOPIC_STATUS, EventBus, bus


# --------------------------------------------------------------------------
# bus (pure asyncio)
# --------------------------------------------------------------------------
async def test_bus_delivers_to_subscribers() -> None:
    local = EventBus()
    async with local.subscription([TOPIC_EVENT]) as queue:
        local.publish(TOPIC_EVENT, {"hello": "world"})
        message = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert message["topic"] == TOPIC_EVENT
        assert message["data"]["hello"] == "world"
        assert "ts" in message


async def test_bus_replays_history_on_demand() -> None:
    local = EventBus()
    local.publish(TOPIC_EVENT, {"n": 1})
    local.publish(TOPIC_ALERT, {"n": 2})

    async with local.subscription([TOPIC_EVENT, TOPIC_ALERT], replay=10) as queue:
        first = await asyncio.wait_for(queue.get(), timeout=1.0)
        second = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert {first["topic"], second["topic"]} == {TOPIC_EVENT, TOPIC_ALERT}


async def test_bus_history_can_be_cleared() -> None:
    local = EventBus()
    local.publish(TOPIC_EVENT, {"n": 1})
    assert local.history()
    local.clear_history()
    assert local.history() == []


async def test_bus_subscriber_count() -> None:
    local = EventBus()
    assert local.subscriber_count == 0
    async with local.subscription() as queue:
        assert local.subscriber_count == 1
        local.publish(TOPIC_STATUS, {"state": "online"})
        await asyncio.wait_for(queue.get(), timeout=1.0)
    assert local.subscriber_count == 0


async def test_bus_drops_oldest_when_full() -> None:
    local = EventBus(queue_size=2)
    async with local.subscription() as queue:
        for index in range(10):
            local.publish(TOPIC_EVENT, {"n": index})
        assert queue.qsize() == 2
        first = await queue.get()
        second = await queue.get()
        assert first["data"]["n"] == 8
        assert second["data"]["n"] == 9


async def test_bus_stream_filters_topics() -> None:
    local = EventBus()
    collected: list[str] = []

    async def consume() -> None:
        async for message in local.stream([TOPIC_ALERT]):
            collected.append(message["topic"])
            break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    local.publish(TOPIC_EVENT, {"ignored": True})
    local.publish(TOPIC_ALERT, {"wanted": True})
    await asyncio.wait_for(task, timeout=1.0)
    assert collected == [TOPIC_ALERT]


async def test_bus_publish_is_thread_safe() -> None:
    """Detector threads publish through ``call_soon_threadsafe``."""
    local = EventBus()
    local.bind_loop(asyncio.get_running_loop())
    received: list[dict] = []

    async with local.subscription([TOPIC_EVENT]) as queue:
        thread = threading.Thread(target=lambda: local.publish(TOPIC_EVENT, {"from": "thread"}))
        thread.start()
        message = await asyncio.wait_for(queue.get(), timeout=2.0)
        received.append(message)
        thread.join()

    assert received and received[0]["data"]["from"] == "thread"


# --------------------------------------------------------------------------
# websocket endpoint
# --------------------------------------------------------------------------
def test_websocket_handshake_and_live_message() -> None:
    from app.main import app

    with TestClient(app) as client:
        with client.websocket_connect("/api/ws/events?topics=event,alert") as socket:
            hello = socket.receive_json()
            assert hello["type"] == "hello"
            assert hello["topics"] == ["event", "alert"]
            assert hello["timezone"] == "Asia/Tehran"
            assert hello["jalali"]

            # Give the endpoint a moment to register its subscription.
            for _ in range(50):
                if bus.subscriber_count:
                    break
                time.sleep(0.01)

            bus.publish(
                TOPIC_EVENT,
                {
                    "kind": "event",
                    "camera_id": 1,
                    "camera_name": "دوربین ۱",
                    "message": "ورود انسان",
                },
            )
            message = socket.receive_json()
            assert message["type"] == "message"
            assert message["topic"] == "event"
            assert message["data"]["camera_name"] == "دوربین ۱"


def test_websocket_defaults_to_all_topics() -> None:
    from app.main import app

    with TestClient(app) as client:
        with client.websocket_connect("/api/ws/events") as socket:
            hello = socket.receive_json()
            assert set(hello["topics"]) >= {"event", "alert", "status", "detection"}


def test_websocket_info_endpoint() -> None:
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/api/ws/info")
        assert response.status_code == 200
        body = response.json()
        assert body["endpoint"] == "/api/ws/events"
        assert "alert" in body["topics"]


@pytest.mark.parametrize("topics", ["event", "alert", "status,event"])
def test_websocket_topic_selection(topics) -> None:
    from app.main import app

    with TestClient(app) as client:
        with client.websocket_connect(f"/api/ws/events?topics={topics}") as socket:
            hello = socket.receive_json()
            assert hello["topics"] == topics.split(",")

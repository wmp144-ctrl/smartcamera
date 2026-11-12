"""WebSocket endpoint for live events, detections and alerts."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.security import SESSION_COOKIE, is_allowed_origin, signer
from app.core.timeutil import jalali_datetime_label, utcnow
from app.services.bus import ALL_TOPICS, bus

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

HEARTBEAT_SECONDS = 20.0


@router.websocket("/api/ws/events")
async def events_socket(websocket: WebSocket) -> None:
    # WebSocket handshakes are not covered by the SameSite cookie rule, so the
    # session and the Origin are both checked here before accepting.
    if not is_allowed_origin(websocket):
        await websocket.close(code=1008, reason="origin not allowed")
        return
    if not signer.verify(websocket.cookies.get(SESSION_COOKIE)):
        await websocket.close(code=1008, reason="authentication required")
        return
    await websocket.accept()
    # Bind the bus to this loop so detector threads can publish safely even if
    # the camera manager did not start (e.g. embedded/test deployments).
    bus.bind_loop(asyncio.get_running_loop())
    raw_topics = websocket.query_params.get("topics")
    topics = [item.strip() for item in (raw_topics or "").split(",") if item.strip()] or list(
        ALL_TOPICS
    )
    try:
        replay = int(websocket.query_params.get("replay", "0"))
    except ValueError:
        replay = 0

    now = utcnow()
    await websocket.send_json(
        {
            "type": "hello",
            "topics": topics,
            "utc": now.isoformat(),
            "jalali": jalali_datetime_label(now, settings.timezone),
            "timezone": settings.timezone,
            "subscribers": bus.subscriber_count,
        }
    )

    try:
        async with bus.subscription(topics, replay=replay) as queue:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    await websocket.send_json(
                        {"type": "ping", "utc": utcnow().isoformat()}
                    )
                    continue
                if message["topic"] not in topics:
                    continue
                await websocket.send_json({"type": "message", **message})
    except WebSocketDisconnect:
        logger.debug("websocket client disconnected")
    except Exception:  # noqa: BLE001 - never let a bad socket take down the app
        logger.exception("websocket stream error")


@router.get("/api/ws/info", response_model=dict)
async def websocket_info() -> dict:
    return {
        "endpoint": "/api/ws/events",
        "topics": list(ALL_TOPICS),
        "subscribers": bus.subscriber_count,
        "history": len(bus.history(limit=1000)),
    }

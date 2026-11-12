"""Dashboard statistics, heatmap and storage endpoint tests."""

from __future__ import annotations

from datetime import date

from app.core.timeutil import utcnow
from app.services.heatmap import HeatmapBuffer, flush_buffer
from tests.factories import at, seed_camera, seed_track


async def test_overview_totals(client, session) -> None:
    camera = await seed_camera(session)
    # Anchor the tracks to "now" so the today counters are deterministic.
    now = utcnow()
    await seed_track(session, camera, frames=6, base_time=now)
    await seed_track(session, camera, label="car", frames=6, base_time=now)

    response = await client.get("/api/stats/overview")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_cameras"] == 1
    assert body["total_tracks"] == 2
    assert body["total_events"] >= 2
    assert body["tracks_today"] == 2
    assert body["timezone"] == "Asia/Tehran"
    assert body["storage_bytes"] > 0

    by_type = {entry["key"]: entry["count"] for entry in body["by_type"]}
    assert by_type["person"] == 1 and by_type["car"] == 1

    by_direction = {entry["key"]: entry["count"] for entry in body["by_direction"]}
    assert by_direction.get("right") == 2

    assert len(body["hourly"]) == 24
    assert len(body["daily"]) == 14
    assert body["cameras"][0]["camera_name"] == camera.name
    assert body["recent_events"]


async def test_overview_hourly_bucket_for_seeded_hour(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=5, base_time=utcnow())

    body = (await client.get("/api/stats/overview")).json()
    assert body["tracks_today"] == 1
    assert sum(entry["count"] for entry in body["hourly"]) == 1


async def test_overview_reports_camera_state(client, session) -> None:
    await seed_camera(session)
    body = (await client.get("/api/stats/overview")).json()
    assert body["cameras"][0]["state"] in {"unknown", "disabled", "offline"}
    assert body["online_cameras"] == 0


async def test_per_camera_stats(client, session) -> None:
    camera = await seed_camera(session)
    # This endpoint reports a *relative* window (last 7 days), so the seeded
    # track is anchored to now rather than to the shared fixed test date.
    await seed_track(session, camera, frames=5, base_time=utcnow())
    body = (await client.get("/api/stats/cameras")).json()
    assert len(body) == 1
    assert body[0]["total_tracks"] == 1
    assert body[0]["by_type"][0]["key"] == "person"


async def test_counts_endpoints(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=6)

    objects = (await client.get("/api/stats/objects")).json()
    assert {entry["key"] for entry in objects} == {"person"}

    directions = (await client.get("/api/stats/directions")).json()
    assert {entry["key"] for entry in directions} == {"right"}

    event_types = (await client.get("/api/stats/event-types")).json()
    assert any(entry["key"] == "enter" for entry in event_types)

    hours = (await client.get("/api/stats/busiest-hours")).json()
    assert len(hours) == 24

    zones = (await client.get("/api/stats/top-zones")).json()
    assert isinstance(zones, list)


async def test_activity_endpoint(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=5)

    hourly = await client.get(
        "/api/stats/activity",
        params={"start": "2026-10-05T00:00:00Z", "end": "2026-10-06T00:00:00Z", "hourly": True},
    )
    assert hourly.status_code == 200
    assert sum(entry["count"] for entry in hourly.json()) >= 1

    daily = await client.get(
        "/api/stats/activity",
        params={"start": "2026-09-30T00:00:00Z", "end": "2026-10-10T00:00:00Z", "hourly": False},
    )
    buckets = daily.json()
    # local-day buckets covering the range, plus the trailing partial day
    assert 10 <= len(buckets) <= 12
    assert sum(entry["count"] for entry in buckets) == 1
    assert all(entry["jalali"].startswith("1405/") for entry in buckets)


async def test_heatmap_endpoint_empty_then_populated(client, session) -> None:
    camera = await seed_camera(session)

    empty = await client.get(f"/api/stats/heatmap/{camera.id}")
    assert empty.status_code == 200
    body = empty.json()
    assert body["total"] == 0
    assert len(body["cells"]) == body["grid_w"] * body["grid_h"]
    assert body["max_count"] == 0

    buffer = HeatmapBuffer()
    buffer.add(camera.id, (0.5, 0.5), object_type="person", when=at(0))
    buffer.add(camera.id, (0.5, 0.5), object_type="person", when=at(1))
    await flush_buffer(session, buffer)
    await session.commit()

    populated = await client.get(
        f"/api/stats/heatmap/{camera.id}", params={"day": "2026-10-05"}
    )
    assert populated.status_code == 200
    assert populated.json()["total"] == 2
    assert populated.json()["max_count"] == 2
    assert populated.json()["jalali"] == "1405/07/13"


async def test_heatmap_accepts_jalali_day(client, session) -> None:
    camera = await seed_camera(session)
    response = await client.get(f"/api/stats/heatmap/{camera.id}", params={"day": "1405/07/13"})
    assert response.status_code == 200
    assert response.json()["jalali"] == "1405/07/13"


async def test_heatmap_invalid_day(client, session) -> None:
    camera = await seed_camera(session)
    assert (await client.get(f"/api/stats/heatmap/{camera.id}", params={"day": "nonsense"})).status_code == 422


async def test_heatmap_unknown_camera(client) -> None:
    assert (await client.get("/api/stats/heatmap/999")).status_code == 404


async def test_storage_endpoint(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=5)
    body = (await client.get("/api/stats/storage")).json()
    assert body["total_bytes"] > 0
    assert body["file_count"] >= 3
    assert str(camera.id) in body["by_camera"]
    assert "person" in body["by_type"]


def test_local_day_bounds_are_utc() -> None:
    """Regression: returning a Tehran-aware midnight shifted \"today\" by +03:30.

    SQLite stores the wall clock of whatever it is handed and ignores tzinfo,
    so these bounds must be UTC before they are compared with stored values.
    """
    from datetime import timedelta

    from app.core.timeutil import UTC, to_local
    from app.services.stats import local_day_bounds

    start, end = local_day_bounds()
    assert start.tzinfo is UTC
    assert end.tzinfo is UTC
    assert end - start == timedelta(days=1)
    # The UTC bound must be the local (Tehran) midnight of that calendar day.
    local_start = to_local(start, "Asia/Tehran")
    assert local_start.strftime("%H:%M:%S") == "00:00:00"


async def test_tracks_today_uses_the_local_day_boundary(client, session) -> None:
    """A track a few hours before local midnight must not be counted as today."""
    from datetime import timedelta

    from app.core.timeutil import to_local, utcnow
    from app.services.stats import local_day_bounds

    camera = await seed_camera(session)
    day_start, _ = local_day_bounds()
    await seed_track(session, camera, frames=5, base_time=utcnow())

    # 2 hours before the local day started -> yesterday, even in UTC terms.
    yesterday = day_start - timedelta(hours=2)
    assert to_local(yesterday, "Asia/Tehran").date() < to_local(day_start, "Asia/Tehran").date()
    await seed_track(session, camera, label="car", frames=5, base_time=yesterday)

    body = (await client.get("/api/stats/overview")).json()
    assert body["tracks_today"] == 1, "only the track inside the local day counts"
    assert body["total_tracks"] == 2

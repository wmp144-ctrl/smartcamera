"""Event log and alert API tests."""

from __future__ import annotations

from tests.factories import alert_zone, seed_camera, seed_track


async def test_events_are_logged_for_a_track(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=6)

    response = await client.get("/api/events")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1
    event = body["items"][0]
    assert event["event_type"] in {"enter", "exit", "stationary", "moving", "detection"}
    assert event["occurred_at_jalali"].startswith("1405/")
    assert event["message"]


async def test_event_has_snapshot_attached(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=5, end_with_exit=True)

    events = (await client.get("/api/events", params={"event_type": "exit"})).json()["items"]
    assert events
    assert events[0]["snapshot_url"]


async def test_event_type_filter_and_summary(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=6)

    enters = await client.get("/api/events", params={"event_type": "enter"})
    assert enters.json()["total"] >= 1

    summary = await client.get("/api/events/summary")
    assert summary.status_code == 200
    assert "enter" in summary.json()["by_type"]
    assert summary.json()["unacknowledged"] >= 1


async def test_acknowledge_event(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=4)
    event = (await client.get("/api/events")).json()["items"][0]

    response = await client.post(f"/api/events/{event['id']}/acknowledge")
    assert response.status_code == 200
    assert response.json()["acknowledged"] is True
    assert response.json()["acknowledged_at"] is not None


async def test_acknowledge_all_events(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=4)
    response = await client.post("/api/events/acknowledge-all")
    assert response.status_code == 200
    assert (await client.get("/api/events", params={"acknowledged": "false"})).json()["total"] == 0


async def test_event_search_and_404(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=4)

    found = await client.get("/api/events", params={"search": "ورود"})
    assert found.json()["total"] >= 1
    empty = await client.get("/api/events", params={"search": "چیزی-که-نیست"})
    assert empty.json()["total"] == 0

    assert (await client.get("/api/events/999999")).status_code == 404
    assert (await client.post("/api/events/999999/acknowledge")).status_code == 404


async def test_delete_event(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=4)
    event = (await client.get("/api/events")).json()["items"][0]
    assert (await client.delete(f"/api/events/{event['id']}")).status_code == 200


async def test_camera_filter_on_events(client, session) -> None:
    first = await seed_camera(session, name="اول")
    second = await seed_camera(session, name="دوم")
    await seed_track(session, first, frames=4)

    assert (await client.get("/api/events", params={"camera_id": first.id})).json()["total"] >= 1
    assert (await client.get("/api/events", params={"camera_id": second.id})).json()["total"] == 0


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------
async def test_alerts_are_created_for_alert_zones(client, session) -> None:
    camera = await seed_camera(session, zones=[alert_zone(classes=["person"])])
    await seed_track(session, camera, frames=5)

    response = await client.get("/api/alerts")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1
    alert = body["items"][0]
    assert alert["sound"] is True
    assert alert["rule_name"] == "ناحیه حساس"
    assert alert["triggered_at_jalali"].startswith("1405/")


async def test_alerts_respect_class_restriction(client, session) -> None:
    camera = await seed_camera(session, zones=[alert_zone(classes=["car"])])
    await seed_track(session, camera, label="person", frames=5)
    assert (await client.get("/api/alerts")).json()["total"] == 0


async def test_unread_alerts_endpoint(client, session) -> None:
    camera = await seed_camera(session, zones=[alert_zone()])
    await seed_track(session, camera, frames=5)

    body = (await client.get("/api/alerts/unread")).json()
    assert body["unread"] >= 1
    assert body["latest"]
    assert body["latest"][0]["severity"]


async def test_acknowledge_alerts(client, session) -> None:
    camera = await seed_camera(session, zones=[alert_zone()])
    await seed_track(session, camera, frames=5)
    alert = (await client.get("/api/alerts")).json()["items"][0]

    response = await client.post(f"/api/alerts/{alert['id']}/acknowledge")
    assert response.status_code == 200
    assert response.json()["acknowledged"] is True

    assert (await client.post("/api/alerts/acknowledge-all")).status_code == 200
    assert (await client.get("/api/alerts/unread")).json()["unread"] == 0
    assert (await client.post("/api/alerts/999999/acknowledge")).status_code == 404


async def test_alerts_without_rules_are_empty(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=4)
    assert (await client.get("/api/alerts")).json()["total"] == 0

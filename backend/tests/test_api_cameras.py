"""Camera / zone API tests."""

from __future__ import annotations

import pytest

from tests.factories import seed_camera

CAMERA_PAYLOAD = {
    "name": "دوربین پارکینگ",
    "location": "طبقه منفی یک",
    "rtsp_url": "rtsp://192.168.1.50:554/stream1",
    "enabled": True,
    "fps": 20,
    "detection_fps": 5,
    "detection_enabled": True,
    "model_name": "rf-detr-base",
}


async def test_create_and_get_camera(client) -> None:
    response = await client.post("/api/cameras", json=CAMERA_PAYLOAD)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == CAMERA_PAYLOAD["name"]
    assert body["id"] > 0
    assert body["class_config"]["person"]["enabled"] is True
    assert body["reconnect_policy"]["backoff_initial"] == 1.0
    assert body["zones"] == []

    detail = await client.get(f"/api/cameras/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["location"] == "طبقه منفی یک"


async def test_create_camera_rejects_bad_url(client) -> None:
    payload = {**CAMERA_PAYLOAD, "rtsp_url": "ftp://nope"}
    response = await client.post("/api/cameras", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "داده‌های ورودی نامعتبر است"


async def test_create_camera_rejects_empty_name(client) -> None:
    response = await client.post("/api/cameras", json={**CAMERA_PAYLOAD, "name": ""})
    assert response.status_code == 422


async def test_list_and_filter_cameras(client) -> None:
    await client.post("/api/cameras", json=CAMERA_PAYLOAD)
    await client.post(
        "/api/cameras",
        json={**CAMERA_PAYLOAD, "name": "دوربین انبار", "enabled": False},
    )
    all_cameras = await client.get("/api/cameras")
    assert all_cameras.status_code == 200
    assert len(all_cameras.json()) == 2

    enabled = await client.get("/api/cameras", params={"enabled": "true"})
    assert len(enabled.json()) == 1
    assert enabled.json()[0]["name"] == "دوربین پارکینگ"

    searched = await client.get("/api/cameras", params={"search": "انبار"})
    assert len(searched.json()) == 1


async def test_update_camera(client) -> None:
    created = (await client.post("/api/cameras", json=CAMERA_PAYLOAD)).json()
    response = await client.patch(
        f"/api/cameras/{created['id']}",
        json={
            "name": "نام جدید",
            "detection_fps": 3.5,
            "class_config": {"person": {"enabled": True, "threshold": 0.66, "min_motion": 0.02}},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "نام جدید"
    assert body["detection_fps"] == 3.5
    assert body["class_config"]["person"]["threshold"] == 0.66
    assert body["class_config"]["person"]["min_motion"] == 0.02


async def test_delete_camera(client) -> None:
    created = (await client.post("/api/cameras", json=CAMERA_PAYLOAD)).json()
    response = await client.delete(f"/api/cameras/{created['id']}")
    assert response.status_code == 200
    assert (await client.get(f"/api/cameras/{created['id']}")).status_code == 404


async def test_camera_not_found(client) -> None:
    response = await client.get("/api/cameras/9999")
    assert response.status_code == 404
    assert response.json()["detail"] == "دوربین یافت نشد"


async def test_toggle_detection(client) -> None:
    created = (await client.post("/api/cameras", json=CAMERA_PAYLOAD)).json()
    response = await client.post(
        f"/api/cameras/{created['id']}/detection", params={"enabled": "false"}
    )
    assert response.status_code == 200
    assert (await client.get(f"/api/cameras/{created['id']}")).json()["detection_enabled"] is False


async def test_status_endpoints(client, session) -> None:
    camera = await seed_camera(session, name="دوربین ۱")
    response = await client.get("/api/cameras/status/all")
    assert response.status_code == 200
    statuses = response.json()
    assert len(statuses) == 1
    # no stream is running in tests -> reported as unknown (or disabled)
    assert statuses[0]["state"] in {"unknown", "disabled"}

    single = await client.get(f"/api/cameras/{camera.id}/status")
    assert single.status_code == 200
    assert single.json()["camera_id"] == camera.id


async def test_health_history_is_empty_without_samples(client, session) -> None:
    camera = await seed_camera(session)
    response = await client.get(f"/api/cameras/{camera.id}/health")
    assert response.status_code == 200
    assert response.json() == []


async def test_models_endpoint(client) -> None:
    response = await client.get("/api/cameras/models/available")
    assert response.status_code == 200
    body = response.json()
    keys = {model["key"] for model in body["models"]}
    assert {"rf-detr-nano", "rf-detr-base", "rf-detr-large"} <= keys
    assert body["default"] == "rf-detr-base"


# --------------------------------------------------------------------------
# zones
# --------------------------------------------------------------------------
async def test_zone_lifecycle(client, session) -> None:
    camera = await seed_camera(session)

    polygon = await client.post(
        f"/api/cameras/{camera.id}/zones",
        json={
            "name": "منطقه پردازش",
            "kind": "region",
            "points": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.9], [0.1, 0.9]],
            "color": "#22d3ee",
        },
    )
    assert polygon.status_code == 201, polygon.text
    zone = polygon.json()
    assert zone["kind"] == "region"
    assert len(zone["points"]) == 4

    line = await client.post(
        f"/api/cameras/{camera.id}/zones",
        json={"name": "خط عبور", "kind": "line", "points": [[0.5, 0.0], [0.5, 1.0]]},
    )
    assert line.status_code == 201

    listed = await client.get(f"/api/cameras/{camera.id}/zones")
    assert len(listed.json()) == 2

    updated = await client.patch(
        f"/api/cameras/{camera.id}/zones/{zone['id']}",
        json={"name": "منطقه اصلی", "alert_enabled": True, "alert_classes": ["person"]},
    )
    assert updated.status_code == 200
    assert updated.json()["alert_enabled"] is True
    assert updated.json()["alert_classes"] == ["person"]

    deleted = await client.delete(f"/api/cameras/{camera.id}/zones/{zone['id']}")
    assert deleted.status_code == 200
    assert len((await client.get(f"/api/cameras/{camera.id}/zones")).json()) == 1


async def test_zone_validation(client, session) -> None:
    camera = await seed_camera(session)
    base = f"/api/cameras/{camera.id}/zones"

    # a line needs exactly two points
    response = await client.post(
        base, json={"name": "خط", "kind": "line", "points": [[0.1, 0.1], [0.2, 0.2], [0.3, 0.3]]}
    )
    assert response.status_code == 422

    # a polygon needs at least three
    response = await client.post(
        base, json={"name": "چندضلعی", "kind": "region", "points": [[0.1, 0.1], [0.2, 0.2]]}
    )
    assert response.status_code == 422

    # points must be normalised
    response = await client.post(
        base,
        json={"name": "خارج", "kind": "region", "points": [[0, 0], [40, 0], [40, 40]]},
    )
    assert response.status_code == 422


async def test_zone_requires_existing_camera(client) -> None:
    response = await client.post(
        "/api/cameras/4242/zones",
        json={"name": "x", "kind": "region", "points": [[0, 0], [1, 0], [1, 1]]},
    )
    assert response.status_code == 404


async def test_zone_on_other_camera_is_not_reachable(client, session) -> None:
    first = await seed_camera(session, name="اول")
    second = await seed_camera(session, name="دوم")
    zone = (
        await client.post(
            f"/api/cameras/{first.id}/zones",
            json={"name": "z", "kind": "region", "points": [[0, 0], [1, 0], [1, 1]]},
        )
    ).json()

    response = await client.delete(f"/api/cameras/{second.id}/zones/{zone['id']}")
    assert response.status_code == 404


async def test_snapshot_returns_404_without_a_frame(client, session) -> None:
    camera = await seed_camera(session)
    response = await client.get(f"/api/cameras/{camera.id}/snapshot")
    assert response.status_code == 404


async def test_test_endpoint_reports_failure_for_bad_stream(client) -> None:
    created = (
        await client.post(
            "/api/cameras",
            json={**CAMERA_PAYLOAD, "rtsp_url": "rtsp://192.0.2.1:1/nope"},
        )
    ).json()
    response = await client.post(f"/api/cameras/{created['id']}/test")
    assert response.status_code == 200
    assert response.json()["ok"] is False


@pytest.mark.parametrize("field", ["fps", "detection_fps"])
async def test_invalid_numeric_fields_are_rejected(client, field) -> None:
    response = await client.post("/api/cameras", json={**CAMERA_PAYLOAD, field: 0})
    assert response.status_code == 422


async def test_deleting_a_camera_removes_its_stored_images(client, session) -> None:
    """The DB rows cascade away, so the image files must be removed here."""
    from app.core.config import settings
    from tests.factories import seed_track

    camera = await seed_camera(session)
    await seed_track(session, camera, frames=5)

    media_dir = settings.snapshots_dir / str(camera.id)
    assert media_dir.is_dir() and any(media_dir.rglob("*.jpg"))

    response = await client.delete(f"/api/cameras/{camera.id}")
    assert response.status_code == 200
    assert not media_dir.exists()

"""Detected-object gallery API tests."""

from __future__ import annotations

from tests.factories import seed_camera, seed_track


async def test_list_objects_with_images(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=6)

    response = await client.get("/api/objects")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["object_type"] == "person"
    assert item["images"]
    assert item["best_image"] is not None
    assert item["first_seen_jalali"].startswith("1405/")

    image = item["images"][0]
    assert image["full_url"].startswith("/media/objects/")
    assert image["crop_url"].endswith("_crop.jpg")
    assert image["thumb_url"].endswith("_thumb.jpg")
    assert image["captured_at_jalali"].startswith("1405/")


async def test_stored_images_are_served(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=4)

    item = (await client.get("/api/objects")).json()["items"][0]
    for url in (item["best_image"]["full_url"], item["best_image"]["crop_url"]):
        response = await client.get(url)
        assert response.status_code == 200, url
        assert response.headers["content-type"] == "image/jpeg"
        assert len(response.content) > 200


async def test_object_filters_endpoint(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, label="person", frames=4)
    await seed_track(session, camera, label="car", start_x=0.2, frames=4)

    response = await client.get("/api/objects/filters")
    assert response.status_code == 200
    body = response.json()
    types = {entry["value"] for entry in body["object_types"]}
    assert {"person", "car"} <= types
    labels = {entry["value"]: entry["label"] for entry in body["object_types"]}
    assert labels["person"] == "انسان"
    assert labels["car"] == "خودرو"
    assert any(entry["value"] == "right" for entry in body["directions_all"])
    assert body["cameras"][0]["value"] == camera.id


async def test_object_filters_use_persian_direction_labels(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=6)
    body = (await client.get("/api/objects/filters")).json()
    directions = {entry["value"]: entry["label"] for entry in body["directions"]}
    assert directions.get("right") == "راست"


async def test_object_detail_and_images(client, session) -> None:
    camera = await seed_camera(session)
    track = await seed_track(session, camera, frames=6)

    detail = await client.get(f"/api/objects/{track.id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == track.id

    images = await client.get(f"/api/objects/{track.id}/images")
    assert images.status_code == 200
    assert len(images.json()) >= 1

    best = await client.get(f"/api/objects/{track.id}/best")
    assert best.status_code == 200
    assert best.json()["is_best"] is True


async def test_object_not_found(client) -> None:
    assert (await client.get("/api/objects/123456")).status_code == 404
    assert (await client.delete("/api/objects/123456")).status_code == 404


async def test_filter_by_type_and_direction(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, label="person", frames=6)
    await seed_track(session, camera, label="car", start_x=0.1, frames=6)

    persons = await client.get("/api/objects", params={"object_type": "person"})
    assert persons.json()["total"] == 1
    assert persons.json()["items"][0]["object_type"] == "person"

    rightward = await client.get("/api/objects", params={"direction": "right"})
    assert rightward.json()["total"] == 2

    leftward = await client.get("/api/objects", params={"direction": "left"})
    assert leftward.json()["total"] == 0


async def test_filter_by_confidence(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=4)
    assert (await client.get("/api/objects", params={"min_confidence": 0.95})).json()["total"] == 0
    assert (await client.get("/api/objects", params={"min_confidence": 0.5})).json()["total"] == 1


async def test_filter_by_quality(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=4)
    assert (await client.get("/api/objects", params={"min_quality": 0.99})).json()["total"] == 0
    assert (await client.get("/api/objects", params={"min_quality": 0.01})).json()["total"] == 1


async def test_time_window_filter(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=4)  # 2026-10-05T08:00Z onwards

    inside = await client.get(
        "/api/objects",
        params={"start": "2026-10-05T07:00:00Z", "end": "2026-10-05T09:00:00Z"},
    )
    assert inside.json()["total"] == 1

    outside = await client.get(
        "/api/objects",
        params={"start": "2026-10-06T00:00:00Z", "end": "2026-10-07T00:00:00Z"},
    )
    assert outside.json()["total"] == 0

    invalid = await client.get(
        "/api/objects",
        params={"start": "2026-10-07T00:00:00Z", "end": "2026-10-06T00:00:00Z"},
    )
    assert invalid.status_code == 422


async def test_camera_filter_and_pagination(client, session) -> None:
    first = await seed_camera(session, name="اول")
    second = await seed_camera(session, name="دوم")
    for _ in range(3):
        await seed_track(session, first, frames=4)

    page = await client.get("/api/objects", params={"page": 1, "page_size": 2})
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 2

    page2 = await client.get("/api/objects", params={"page": 2, "page_size": 2})
    assert len(page2.json()["items"]) == 1

    only_first = await client.get("/api/objects", params={"camera_id": first.id})
    assert only_first.json()["total"] == 3
    only_second = await client.get("/api/objects", params={"camera_id": second.id})
    assert only_second.json()["total"] == 0


async def test_delete_object_removes_files(client, session) -> None:
    camera = await seed_camera(session)
    track = await seed_track(session, camera, frames=4)
    item = (await client.get("/api/objects")).json()["items"][0]
    file_url = item["best_image"]["full_url"]
    assert (await client.get(file_url)).status_code == 200

    response = await client.delete(f"/api/objects/{track.id}")
    assert response.status_code == 200
    assert (await client.get("/api/objects")).json()["total"] == 0
    assert (await client.get(file_url)).status_code == 404


async def test_exited_object_is_marked(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=5, end_with_exit=True)
    item = (await client.get("/api/objects")).json()["items"][0]
    assert item["status"] == "ended"
    assert item["exited_frame"] is True

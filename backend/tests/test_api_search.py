"""Spatial search API tests."""

from __future__ import annotations

import csv
import io

from tests.factories import seed_camera, seed_track

# A track seeded from x=0.10 with step 0.05 over 8 frames ends with its centre
# around (0.54, 0.51).
LEFT_REGION = [[0.0, 0.0], [0.2, 0.0], [0.2, 1.0], [0.0, 1.0]]
RIGHT_REGION = [[0.4, 0.2], [0.8, 0.2], [0.8, 0.8], [0.4, 0.8]]


async def test_spatial_search_inside_region(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=8)

    response = await client.post(
        "/api/search/spatial", json={"polygon": RIGHT_REGION, "camera_ids": [camera.id]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["object_type"] == "person"
    assert body["polygon"] == RIGHT_REGION


async def test_spatial_search_outside_region(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=8)
    response = await client.post("/api/search/spatial", json={"polygon": LEFT_REGION})
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_spatial_search_combines_region_time_type_direction(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=8)

    payload = {
        "polygon": RIGHT_REGION,
        "start": "2026-10-05T07:00:00Z",
        "end": "2026-10-05T09:00:00Z",
        "object_types": ["person"],
        "directions": ["right"],
    }
    assert (await client.post("/api/search/spatial", json=payload)).json()["total"] == 1

    payload["directions"] = ["left"]
    assert (await client.post("/api/search/spatial", json=payload)).json()["total"] == 0

    payload["directions"] = ["right"]
    payload["object_types"] = ["car"]
    assert (await client.post("/api/search/spatial", json=payload)).json()["total"] == 0


async def test_spatial_search_without_polygon_returns_all(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=5)
    response = await client.post("/api/search/spatial", json={"camera_ids": [camera.id]})
    assert response.json()["total"] == 1


async def test_spatial_search_validates_polygon(client) -> None:
    response = await client.post("/api/search/spatial", json={"polygon": [[0.1, 0.1], [0.2, 0.2]]})
    assert response.status_code == 422

    response = await client.post("/api/search/spatial", json={"polygon": [[0, 0], [2, 0], [2, 2]]})
    assert response.status_code == 422


async def test_spatial_search_pagination_and_order(client, session) -> None:
    camera = await seed_camera(session)
    for _ in range(4):
        await seed_track(session, camera, frames=8)

    page = await client.post("/api/search/spatial", json={"limit": 2, "offset": 0})
    assert len(page.json()["items"]) == 2

    ascending = await client.post("/api/search/spatial", json={"order": "asc", "limit": 10})
    descending = await client.post("/api/search/spatial", json={"order": "desc", "limit": 10})
    assert ascending.json()["items"][0]["id"] != descending.json()["items"][0]["id"]


async def test_spatial_search_returns_images(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=8)
    item = (
        await client.post("/api/search/spatial", json={"polygon": RIGHT_REGION})
    ).json()["items"][0]
    assert item["images"]
    assert item["best_image"]["crop_url"]


async def test_export_csv(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=8)

    response = await client.post("/api/search/spatial/export", json={"polygon": RIGHT_REGION})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    text = response.text
    assert text.startswith("\ufeff")  # BOM for Excel
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    assert rows[0][0] == "id"
    assert "direction_fa" in rows[0]
    assert len(rows) == 2  # header + one object
    assert rows[1][3] == "انسان"


async def test_point_query_returns_aggregates(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=8)
    response = await client.post("/api/search/point", json={"polygon": RIGHT_REGION})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["by_type"]["person"] == 1
    assert body["sample"][0]["center"][0] is not None

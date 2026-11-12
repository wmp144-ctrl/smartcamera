"""The API also serves the built frontend when one exists.

These tests pin the contract that matters: the SPA catch-all must never answer a
path that belongs to the API, and it must not shadow the API descriptor.
"""

from __future__ import annotations

import pytest

from app.core.config import settings


async def test_info_endpoint_returns_json(client) -> None:
    """The service descriptor lives on an API path, never on ``/``."""
    response = await client.get("/api/info")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["app"] == "CameraSmart"
    assert body["timezone"] == settings.timezone


@pytest.mark.skipif(
    settings.frontend_dist is None, reason="frontend bundle has not been built"
)
async def test_root_serves_the_app_when_a_build_exists(client) -> None:
    """Opening ``http://host:port/`` in a browser must show the UI, not JSON."""
    response = await client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<div id=\"root\">" in response.text


@pytest.mark.skipif(
    settings.frontend_dist is not None, reason="a frontend build is present"
)
async def test_root_falls_back_to_json_without_a_build(client) -> None:
    """In API-only mode ``/`` still answers, so the host is never a dead end."""
    response = await client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["app"] == "CameraSmart"


async def test_unknown_api_route_is_a_json_404(client) -> None:
    response = await client.get("/api/definitely/not/a/route")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert "detail" in response.json()


@pytest.mark.skipif(
    settings.frontend_dist is None, reason="frontend bundle has not been built"
)
async def test_spa_route_serves_index_html(client) -> None:
    """A client-side route (e.g. /dashboard) must return the app shell."""
    response = await client.get("/dashboard")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<div id=\"root\">" in response.text


@pytest.mark.skipif(
    settings.frontend_dist is None, reason="frontend bundle has not been built"
)
async def test_frontend_assets_are_mounted(client) -> None:
    dist = settings.frontend_dist
    assert dist is not None
    assets = sorted((dist / "assets").glob("*.js"))
    assert assets, "the build should emit at least one JS chunk"
    response = await client.get(f"/assets/{assets[0].name}")
    assert response.status_code == 200


async def test_unknown_spa_route_still_serves_the_shell(client) -> None:
    """A deep link into a client-side route must not 404."""
    response = await client.get("/cameras/anything/at/all")
    if settings.frontend_dist is None:
        assert response.status_code == 404
        return
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


async def test_api_docs_are_reachable(client) -> None:
    """OpenAPI docs must not be swallowed by the SPA fallback."""
    response = await client.get("/api/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"].startswith("CameraSmart")

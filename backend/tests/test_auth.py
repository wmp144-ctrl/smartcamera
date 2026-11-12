"""Single-admin authentication: first run, login, sessions and redaction."""

from __future__ import annotations

from app.core.security import MASK, mask_url_credentials
from tests.conftest import ADMIN_PASSWORD

SETUP_PASSWORD = "setup-password-1"


def camera_payload(url: str = "rtsp://192.168.1.10:554/stream1") -> dict:
    return {
        "name": "دوربین تست",
        "location": "آزمایشگاه",
        "rtsp_url": url,
        "enabled": False,
        "fps": 5,
        "detection_fps": 1,
        "detection_enabled": False,
    }


# --------------------------------------------------------------------------
# first run / setup
# --------------------------------------------------------------------------
async def test_first_run_reports_unconfigured_and_blocks_api(anonymous_client) -> None:
    body = (await anonymous_client.get("/api/auth/status")).json()
    assert body["configured"] is False
    assert body["authenticated"] is False

    assert (await anonymous_client.get("/api/cameras")).status_code == 401
    assert (await anonymous_client.get("/api/system/info")).status_code == 401
    # Readiness stays public so a supervisor can health-check the process.
    assert (await anonymous_client.get("/api/system/ping")).status_code == 200


async def test_setup_rejects_short_or_mismatched_passwords(anonymous_client) -> None:
    short = await anonymous_client.post(
        "/api/auth/setup", json={"password": "short", "confirm": "short"}
    )
    assert short.status_code == 422

    mismatch = await anonymous_client.post(
        "/api/auth/setup", json={"password": "long-enough-1", "confirm": "long-enough-2"}
    )
    assert mismatch.status_code == 422

    body = (await anonymous_client.get("/api/auth/status")).json()
    assert body["configured"] is False


async def test_setup_signs_in_and_cannot_run_twice(anonymous_client) -> None:
    response = await anonymous_client.post(
        "/api/auth/setup",
        json={"password": SETUP_PASSWORD, "confirm": SETUP_PASSWORD},
    )
    assert response.status_code == 200, response.text
    assert response.json()["authenticated"] is True

    # The cookie from the setup response is enough for the protected API.
    assert (await anonymous_client.get("/api/cameras")).status_code == 200

    again = await anonymous_client.post(
        "/api/auth/setup",
        json={"password": "another-password", "confirm": "another-password"},
    )
    assert again.status_code == 409


# --------------------------------------------------------------------------
# login / logout
# --------------------------------------------------------------------------
async def test_login_requires_the_right_password(anonymous_client) -> None:
    await anonymous_client.post(
        "/api/auth/setup",
        json={"password": SETUP_PASSWORD, "confirm": SETUP_PASSWORD},
    )
    await anonymous_client.post("/api/auth/logout")
    assert (await anonymous_client.get("/api/cameras")).status_code == 401

    wrong = await anonymous_client.post("/api/auth/login", json={"password": "not-the-password"})
    assert wrong.status_code == 401

    right = await anonymous_client.post("/api/auth/login", json={"password": SETUP_PASSWORD})
    assert right.status_code == 200
    assert right.json()["authenticated"] is True
    assert (await anonymous_client.get("/api/cameras")).status_code == 200


async def test_media_requires_a_session(anonymous_client) -> None:
    response = await anonymous_client.get("/media/objects/1/whatever.jpg")
    assert response.status_code == 401


async def test_password_change_rotates_the_session(client, anonymous_client) -> None:
    original_cookie = client.cookies.get("camerasmart_session")
    assert original_cookie

    response = await client.post(
        "/api/auth/password",
        json={
            "current_password": ADMIN_PASSWORD,
            "new_password": "brand-new-password",
            "confirm": "brand-new-password",
        },
    )
    assert response.status_code == 200, response.text

    # The old cookie is signed with the retired secret and must stop working.
    stale = await anonymous_client.get(
        "/api/cameras", headers={"cookie": f"camerasmart_session={original_cookie}"}
    )
    assert stale.status_code == 401

    # The freshly issued cookie keeps working.
    assert (await client.get("/api/cameras")).status_code == 200


# --------------------------------------------------------------------------
# credential redaction
# --------------------------------------------------------------------------
def test_mask_url_credentials() -> None:
    assert (
        mask_url_credentials("rtsp://admin:secret@10.0.0.5:554/s1")
        == f"rtsp://{MASK}@10.0.0.5:554/s1"
    )
    assert mask_url_credentials("rtsp://10.0.0.5:554/s1") == "rtsp://10.0.0.5:554/s1"
    assert mask_url_credentials("file:///C:/clips/a.mp4") == "file:///C:/clips/a.mp4"


async def test_camera_credentials_are_never_returned(client) -> None:
    created = await client.post(
        "/api/cameras", json=camera_payload("rtsp://admin:secret@10.0.0.5:554/s1")
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["rtsp_url"] == f"rtsp://{MASK}@10.0.0.5:554/s1"
    assert body["has_credentials"] is True
    camera_id = body["id"]

    listed = (await client.get("/api/cameras")).json()
    assert all("secret" not in camera["rtsp_url"] for camera in listed)

    # Editing the name sends the masked URL back; the stored one must survive.
    updated = await client.patch(f"/api/cameras/{camera_id}", json={"name": "نام تازه"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "نام تازه"
    assert updated.json()["rtsp_url"] == f"rtsp://{MASK}@10.0.0.5:554/s1"

    from app.core.database import async_session_factory
    from app.models.camera import Camera

    async with async_session_factory() as session:
        stored = await session.get(Camera, camera_id)
        assert stored is not None
        assert stored.rtsp_url == "rtsp://admin:secret@10.0.0.5:554/s1"

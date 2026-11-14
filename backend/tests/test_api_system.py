"""System information, settings and maintenance endpoint tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.timeutil import utcnow
from tests.factories import seed_camera, seed_track


async def test_ping(client) -> None:
    response = await client.get("/api/system/ping")
    assert response.status_code == 200
    assert response.json()["ok"] is True


async def test_info_endpoint(client) -> None:
    response = await client.get("/api/info")
    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == "Asia/Tehran"
    assert body["jalali"].startswith("140")
    assert body["ui"] == "/dashboard"


async def test_system_info(client) -> None:
    response = await client.get("/api/system/info")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["app_name"] == "CameraSmart"
    assert body["timezone"] == "Asia/Tehran"
    assert body["jalali_now"].startswith("14")
    names = {component["name"] for component in body["components"]}
    assert {"Python", "FastAPI", "PyTorch", "FFmpeg"} <= names
    assert body["gpu"]["available"] in {True, False}


async def test_time_endpoint(client) -> None:
    body = (await client.get("/api/system/time")).json()
    assert body["timezone"] == "Asia/Tehran"
    assert body["jalali"].count("/") == 2
    assert body["weekday_fa"] in {
        "شنبه",
        "یک‌شنبه",
        "دوشنبه",
        "سه‌شنبه",
        "چهارشنبه",
        "پنج‌شنبه",
        "جمعه",
    }
    assert body["day_start_utc"] < body["day_end_utc"]
    assert "T" in body["jalali_long"] or " " in body["jalali_long"]


async def test_day_bounds_for_jalali_day(client) -> None:
    body = (await client.get("/api/system/day-bounds", params={"jalali": "1405/07/13"})).json()
    assert body["jalali"] == "1405/07/13"
    assert body["jalali_year"] == 1405
    assert body["start_utc"] == "2026-10-04T20:30:00Z"
    assert body["end_utc"] == "2026-10-05T20:30:00Z"
    assert body["timezone"] == "Asia/Tehran"


async def test_day_bounds_for_gregorian_day(client) -> None:
    body = (
        await client.get("/api/system/day-bounds", params={"year": 2026, "month": 10, "day": 5})
    ).json()
    assert body["jalali"] == "1405/07/13"


async def test_day_bounds_defaults_to_today(client) -> None:
    response = await client.get("/api/system/day-bounds")
    assert response.status_code == 200
    body = response.json()
    start = datetime.fromisoformat(body["start_utc"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(body["end_utc"].replace("Z", "+00:00"))
    assert (end - start).total_seconds() == 86400


async def test_day_bounds_invalid(client) -> None:
    assert (await client.get("/api/system/day-bounds", params={"jalali": "bad"})).status_code == 422
    assert (
        await client.get("/api/system/day-bounds", params={"jalali": "1405/13/40"})
    ).status_code == 422


async def test_gpu_endpoint(client) -> None:
    response = await client.get("/api/system/gpu")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] in {True, False}
    assert body["source"] in {"pynvml", "nvidia-smi", "none"}
    if body["devices"]:
        device = body["devices"][0]
        assert device["memory_total_mb"] > 0
        assert 0 <= device["utilization"] <= 100


async def test_models_endpoint(client) -> None:
    response = await client.get("/api/system/models")
    assert response.status_code == 200
    models = response.json()
    assert len(models) >= 5
    assert all(model["weights_filename"].endswith(".pth") for model in models)


async def test_compatibility_report(client) -> None:
    body = (await client.get("/api/system/compatibility")).json()
    assert "versions" in body
    assert "issues" in body
    assert isinstance(body["notes"], list)
    assert body["python"].startswith("3.11")


async def test_runtime_stats(client, session) -> None:
    await seed_camera(session)
    body = (await client.get("/api/system/runtime")).json()
    assert body["manager"]["streams"] == 0
    assert body["detection_defaults"]["detection_fps"] == 5.0


async def test_storage_endpoint(client, session) -> None:
    camera = await seed_camera(session)
    await seed_track(session, camera, frames=4)
    body = (await client.get("/api/system/storage")).json()
    assert body["total_bytes"] > 0


async def test_settings_roundtrip(client) -> None:
    assert (await client.get("/api/system/settings")).json() == []

    response = await client.put("/api/system/settings/theme", json={"value": {"mode": "dark"}})
    assert response.status_code == 200
    assert response.json()["value"] == {"mode": "dark"}

    updated = await client.put("/api/system/settings/theme", json={"value": {"mode": "light"}})
    assert updated.json()["value"] == {"mode": "light"}

    listed = (await client.get("/api/system/settings")).json()
    assert len(listed) == 1
    assert listed[0]["key"] == "theme"


async def test_retention_dry_run_then_delete(client, session) -> None:
    camera = await seed_camera(session)
    old = datetime(2020, 1, 1, 8, 0, tzinfo=timezone.utc)
    await seed_track(session, camera, frames=4, base_time=old)

    dry = await client.post("/api/system/retention/run", json={"days": 30, "dry_run": True})
    assert dry.status_code == 200
    assert dry.json()["tracks_deleted"] == 1
    assert dry.json()["dry_run"] is True
    assert (await client.get("/api/objects")).json()["total"] == 1  # nothing removed

    real = await client.post("/api/system/retention/run", json={"days": 30, "dry_run": False})
    assert real.json()["tracks_deleted"] == 1
    assert real.json()["files_deleted"] >= 1
    assert (await client.get("/api/objects")).json()["total"] == 0


async def test_retention_keeps_recent_data(client, session) -> None:
    camera = await seed_camera(session)
    # 30 days is a *relative* cutoff, so this data has to be recent relative to
    # now (the shared BASE_TIME would fall outside the window).
    await seed_track(session, camera, frames=4, base_time=utcnow())
    await client.post("/api/system/retention/run", json={"days": 30})
    assert (await client.get("/api/objects")).json()["total"] == 1


async def test_init_db_is_idempotent(client) -> None:
    first = await client.post("/api/system/maintenance/init-db")
    second = await client.post("/api/system/maintenance/init-db")
    assert first.status_code == 200 and second.status_code == 200


async def test_openapi_schema_is_available(client) -> None:
    response = await client.get("/api/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for expected in (
        "/api/cameras",
        "/api/objects",
        "/api/events",
        "/api/alerts",
        "/api/search/spatial",
        "/api/stats/overview",
        "/api/system/info",
    ):
        assert expected in paths, expected


async def test_retention_deletes_old_heatmap_cells(client, session) -> None:
    """Heatmap cells are bucketed per local day and must be trimmed too."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.core.config import settings
    from app.core.timeutil import to_local, utcnow
    from app.models.system import HeatmapCell

    camera = await seed_camera(session)
    today = (to_local(utcnow(), settings.timezone) or utcnow()).date()
    session.add_all(
        [
            HeatmapCell(
                camera_id=camera.id,
                day=today - timedelta(days=settings.retention_days + 5),
                grid_w=settings.heatmap_grid_w,
                grid_h=settings.heatmap_grid_h,
                cell_x=1,
                cell_y=1,
                object_type="person",
                count=3,
                updated_at=utcnow(),
            ),
            HeatmapCell(
                camera_id=camera.id,
                day=today,
                grid_w=settings.heatmap_grid_w,
                grid_h=settings.heatmap_grid_h,
                cell_x=2,
                cell_y=2,
                object_type="person",
                count=2,
                updated_at=utcnow(),
            ),
        ]
    )
    await session.commit()

    body = (
        await client.post("/api/system/retention/run", json={"days": settings.retention_days})
    ).json()
    assert body["heatmap_deleted"] == 1

    rows = (await session.execute(select(HeatmapCell))).scalars().all()
    assert [row.cell_x for row in rows] == [2]


async def test_automatic_retention_loop_runs_and_stops(monkeypatch) -> None:
    """The background retention task must run a pass and cancel cleanly."""
    import asyncio
    import contextlib

    from app import main as main_module

    calls: list[int] = []

    class _Report:
        def as_dict(self) -> dict:
            return {}

    async def fake_apply(session, days=None, dry_run=False):  # noqa: ANN001, ARG001
        calls.append(1)
        return _Report()

    monkeypatch.setattr(main_module, "apply_retention", fake_apply)
    monkeypatch.setattr(main_module, "RETENTION_STARTUP_DELAY_SECONDS", 0.01)

    task = asyncio.create_task(main_module._retention_loop())
    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls == [1]


# --------------------------------------------------------------------------
# performance profile
# --------------------------------------------------------------------------
async def test_performance_endpoint_reports_profile_and_fixed_batch(client) -> None:
    response = await client.get("/api/system/performance")
    assert response.status_code == 200, response.text
    body = response.json()
    profile = body["profile"]
    for key in (
        "stream_fps",
        "detection_fps",
        "detection_max_width",
        "inference_queue_size",
        "snapshot_workers",
        "snapshot_queue_size",
        "persist_batch_max",
        "fp16",
        "device",
    ):
        assert key in profile, key
    assert profile["inference_queue_size"] >= 1
    # Batch inference is reported as fixed, never as a knob that cannot work.
    assert body["fixed"]["batch_size"]["editable"] is False
    assert "hardware" in body


async def test_performance_update_persists_and_is_bounded(client, monkeypatch, tmp_path) -> None:
    from app.api import system as system_module
    from app.services import performance as performance_module

    monkeypatch.setattr(
        performance_module, "profile_path", lambda: tmp_path / "performance.json"
    )
    applied: list[dict] = []
    monkeypatch.setattr(
        system_module, "apply_profile", lambda profile: applied.append(as_dict(profile))
    )

    ok = await client.put("/api/system/performance", json={"inference_queue_size": 2})
    assert ok.status_code == 200, ok.text
    assert ok.json()["changed"]["inference_queue_size"] == 2
    assert applied and applied[-1]["inference_queue_size"] == 2
    assert (tmp_path / "performance.json").is_file()

    bad = await client.put("/api/system/performance", json={"stream_fps": 500})
    assert bad.status_code == 422, bad.text
    assert "stream_fps" in bad.text


def as_dict(profile) -> dict:  # noqa: ANN001
    from dataclasses import asdict

    return asdict(profile)

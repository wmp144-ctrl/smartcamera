"""Factories used by the API tests to seed realistic data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera import Camera, Zone
from app.models.detection import TrackRecord
from app.services.detector import FakeDetector
from app.services.persistence import Persister
from app.services.pipeline import DetectionPipeline, PipelineConfig, ZoneSpec
from app.services.tracker import Detection

WIDTH, HEIGHT = 320, 180
BASE_TIME = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)


def at(seconds: float = 0.0) -> datetime:
    return BASE_TIME + timedelta(seconds=seconds)


def frame(value: int = 40) -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8)


def draw(frame_array: np.ndarray, bbox: list[float], value: int = 210) -> np.ndarray:
    out = frame_array.copy()
    x1 = int(bbox[0] * WIDTH)
    y1 = int(bbox[1] * HEIGHT)
    x2 = max(x1 + 2, int(bbox[2] * WIDTH))
    y2 = max(y1 + 2, int(bbox[3] * HEIGHT))
    out[y1:y2, x1:x2] = value
    return out


def alert_zone(
    name: str = "ناحیه حساس",
    *,
    points: list[list[float]] | None = None,
    classes: list[str] | None = None,
    sound: bool = True,
    kind: str = "alert",
) -> Zone:
    return Zone(
        name=name,
        kind=kind,
        points=points or [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        alert_enabled=True,
        alert_sound=sound,
        alert_classes=classes or [],
    )


async def seed_camera(
    session: AsyncSession,
    *,
    camera_id: int | None = None,
    name: str = "دوربین ورودی",
    location: str = "حیاط",
    rtsp_url: str = "rtsp://example.local/stream1",
    detection_enabled: bool = True,
    zones: list[Zone] | None = None,
) -> Camera:
    camera = Camera(
        id=camera_id,
        name=name,
        location=location,
        rtsp_url=rtsp_url,
        detection_enabled=detection_enabled,
    )
    session.add(camera)
    await session.commit()
    await session.refresh(camera)
    for zone in zones or []:
        zone.camera_id = camera.id
        session.add(zone)
    if zones:
        await session.commit()

    # Re-query with the relationship eagerly loaded so ``camera.zones`` is
    # usable outside the session's greenlet context. ``populate_existing``
    # forces the identity-mapped instance to refresh its stale empty
    # collection (expire_on_commit is off in this project).
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    return (
        await session.execute(
            select(Camera)
            .options(selectinload(Camera.zones))
            .where(Camera.id == camera.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


def pipeline_config(camera: Camera, **overrides) -> PipelineConfig:
    zones = [
        ZoneSpec(
            id=zone.id,
            name=zone.name,
            kind=zone.kind,
            points=list(zone.points or []),
            enabled=zone.enabled,
            alert_enabled=zone.alert_enabled,
            alert_sound=zone.alert_sound,
            alert_classes=list(zone.alert_classes or []),
            schedule=zone.schedule,
        )
        for zone in (camera.zones or [])
    ]
    base = dict(
        camera_id=camera.id,
        camera_name=camera.name,
        class_config={
            "person": {"enabled": True, "threshold": 0.3, "min_motion": 0.0},
            "car": {"enabled": True, "threshold": 0.3, "min_motion": 0.0},
        },
        zones=zones,
        require_motion=False,
        stationary_after_seconds=0.5,
        track_max_age_seconds=0.5,
        min_track_hits=1,
        record_snapshots=True,
    )
    base.update(overrides)
    return PipelineConfig(**base)


async def seed_track(
    session: AsyncSession,
    camera: Camera,
    *,
    label: str = "person",
    start_x: float = 0.10,
    step: float = 0.05,
    frames: int = 8,
    start_seconds: float = 0.0,
    end_with_exit: bool = False,
    base_time: datetime | None = None,
    **config_overrides,
) -> TrackRecord:
    """Run a synthetic track through the pipeline and persist it.

    ``base_time`` defaults to a fixed instant (2026-10-05T08:00Z) so time-window
    assertions are deterministic; pass a different instant to place the track on
    a specific local day.
    """
    reference = base_time or BASE_TIME

    def moment(seconds: float) -> datetime:
        return reference + timedelta(seconds=seconds)

    config = pipeline_config(camera, **config_overrides)
    script = [
        [Detection([start_x + i * step, 0.3, start_x + i * step + 0.18, 0.72], 0.9, label)]
        for i in range(frames)
    ]
    pipeline = DetectionPipeline(config, FakeDetector(script))
    persister = Persister()

    now = 0.0
    for index in range(frames):
        x = start_x + index * step
        now = start_seconds + index * 0.1
        result = pipeline.process(
            draw(frame(), [x, 0.3, x + 0.18, 0.72]), ts=moment(now), now=now
        )
        await persister.persist(session, config, result)

    if end_with_exit:
        pipeline.set_detector(FakeDetector([[]]))
        now += 1.0
        result = pipeline.process(frame(), ts=moment(now), now=now)
        await persister.persist(session, config, result)

    await session.commit()
    from sqlalchemy import select

    return (
        await session.execute(select(TrackRecord).where(TrackRecord.camera_id == camera.id).order_by(TrackRecord.id.desc()))
    ).scalars().first()

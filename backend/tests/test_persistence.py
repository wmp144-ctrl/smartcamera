"""Persistence tests: pipeline result -> database rows + files on disk."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.camera import Camera
from app.models.detection import Event, ObjectImage, TrackRecord
from app.models.system import Alert
from app.services.detector import FakeDetector
from app.services.persistence import Persister
from app.services.pipeline import DetectionPipeline, PipelineConfig, ZoneSpec
from app.services.tracker import Detection

WIDTH, HEIGHT = 320, 180


def _t(seconds: float = 0.0) -> datetime:
    return datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def frame(value: int = 40) -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8)


def draw(value: int = 40) -> np.ndarray:
    out = frame(value)
    out[54:126, 96:160] = 210
    return out


def person(x: float = 0.3) -> Detection:
    return Detection(bbox=[x, 0.3, x + 0.2, 0.7], confidence=0.9, object_type="person")


def scripted(detections: list[list[Detection]]) -> FakeDetector:
    return FakeDetector(lambda _f, i: detections[min(i, len(detections) - 1)])


async def make_camera(session, camera_id: int = 1) -> Camera:
    camera = Camera(id=camera_id, name="دوربین تست", rtsp_url="rtsp://example/stream")
    session.add(camera)
    await session.commit()
    return camera


def make_config(camera_id: int = 1, **overrides) -> PipelineConfig:
    base = dict(
        camera_id=camera_id,
        camera_name="دوربین تست",
        class_config={"person": {"enabled": True, "threshold": 0.3, "min_motion": 0.0}},
        require_motion=False,
        stationary_after_seconds=0.5,
        track_max_age_seconds=0.5,
        min_track_hits=1,
        record_snapshots=True,
    )
    base.update(overrides)
    return PipelineConfig(**base)


async def test_persist_creates_track_images_and_events(session) -> None:
    await make_camera(session)
    config = make_config()
    pipeline = DetectionPipeline(config, scripted([[person()]]))
    persister = Persister()

    for index in range(4):
        result = pipeline.process(draw(), ts=_t(index * 0.1), now=index * 0.1)
        await persister.persist(session, config, result)
    await session.commit()

    tracks = (await session.execute(select(TrackRecord))).scalars().all()
    assert len(tracks) == 1
    track = tracks[0]
    assert track.object_type == "person"
    assert track.status == "active"
    assert track.entered_frame is True
    assert track.center_x is not None and 0.0 < track.center_x < 1.0

    images = (
        (await session.execute(select(ObjectImage).where(ObjectImage.track_id == track.id)))
        .scalars()
        .all()
    )
    assert images, "at least one image should be stored"
    assert track.best_image_id is not None
    best = next(image for image in images if image.is_best)
    assert best.id == track.best_image_id
    assert best.quality_score > 0

    # the files really exist on disk (relative to the storage root)
    for image in images:
        assert (settings.storage_root / image.full_path).is_file()
        assert (settings.storage_root / image.crop_path).is_file()
        assert (settings.storage_root / image.thumb_path).is_file()
        # categorised layout: <camera>/<yyyy>/<mm>/<dd>/<type>/...
        parts = image.full_path.split("/")
        assert parts[0] == "objects"
        assert parts[1] == "1"
        assert parts[-2] == "person"

    events = (await session.execute(select(Event))).scalars().all()
    enter = [event for event in events if event.event_type == "enter"]
    assert len(enter) == 1
    assert enter[0].track_id == track.id
    assert enter[0].snapshot_full_path
    assert enter[0].message


async def test_repeated_frames_do_not_duplicate_events(session) -> None:
    await make_camera(session)
    config = make_config()
    pipeline = DetectionPipeline(config, scripted([[person()]]))
    persister = Persister()

    for index in range(10):
        result = pipeline.process(draw(), ts=_t(index * 0.1), now=index * 0.1)
        await persister.persist(session, config, result)
    await session.commit()

    enters = (
        await session.execute(select(func.count()).select_from(Event).where(Event.event_type == "enter"))
    ).scalar()
    tracks = (await session.execute(select(func.count()).select_from(TrackRecord))).scalar()
    assert enters == 1
    assert tracks == 1


async def test_static_object_keeps_a_single_image(session) -> None:
    await make_camera(session)
    config = make_config()
    pipeline = DetectionPipeline(config, scripted([[person()]]))
    persister = Persister()

    now = 0.0
    for _ in range(20):
        result = pipeline.process(draw(), ts=_t(now), now=now)
        await persister.persist(session, config, result)
        now += 0.1
    await session.commit()

    track = (await session.execute(select(TrackRecord))).scalars().one()
    images = (
        (await session.execute(select(ObjectImage).where(ObjectImage.track_id == track.id)))
        .scalars()
        .all()
    )
    assert len(images) == 1, "a stationary object must collapse to exactly one image"
    assert images[0].is_best is True
    assert track.motion_state == "stationary"

    stationary_events = (
        await session.execute(select(Event).where(Event.event_type == "stationary"))
    ).scalars().all()
    assert len(stationary_events) == 1


async def test_moving_object_replaces_best_image_but_keeps_it_bounded(session) -> None:
    await make_camera(session)
    config = make_config(best_image_improvement=0.0)
    detector = scripted([[person(0.05 + index * 0.04)] for index in range(20)])
    pipeline = DetectionPipeline(config, detector)
    persister = Persister()

    now = 0.0
    x = 0.05
    for index in range(20):
        result = pipeline.process(draw(), ts=_t(now), now=now)
        await persister.persist(session, config, result)
        now += 0.1
        x += 0.04
    await session.commit()

    track = (await session.execute(select(TrackRecord))).scalars().one()
    images = (
        (await session.execute(select(ObjectImage).where(ObjectImage.track_id == track.id)))
        .scalars()
        .all()
    )
    best = [image for image in images if image.is_best]
    assert len(best) == 1
    assert len(images) <= 3  # enter anchor + rolling best + possible exit anchor
    assert track.direction in {"right", "stationary"}


async def test_exit_event_links_to_stored_image(session) -> None:
    await make_camera(session)
    config = make_config()
    pipeline = DetectionPipeline(config, scripted([[person()], []]))
    persister = Persister()

    result = pipeline.process(draw(), ts=_t(0), now=0.0)
    await persister.persist(session, config, result)
    result = pipeline.process(draw(), ts=_t(0.1), now=0.1)
    await persister.persist(session, config, result)

    result = pipeline.process(frame(), ts=_t(0.7), now=0.7)
    await persister.persist(session, config, result)
    await session.commit()

    exit_event = (
        await session.execute(select(Event).where(Event.event_type == "exit"))
    ).scalars().one()
    assert exit_event.snapshot_full_path
    assert (settings.storage_root / exit_event.snapshot_full_path).is_file()
    track = (await session.execute(select(TrackRecord))).scalars().one()
    assert track.status == "ended"
    assert track.exited_frame is True


async def test_alerts_are_persisted_and_linked_to_events(session) -> None:
    await make_camera(session)
    from app.models.camera import Zone

    zone_row = Zone(
        camera_id=1,
        name="ناحیه حساس",
        kind="alert",
        points=[[0, 0], [1, 0], [1, 1], [0, 1]],
        alert_enabled=True,
        alert_classes=["person"],
    )
    session.add(zone_row)
    await session.commit()
    await session.refresh(zone_row)

    zone = ZoneSpec(
        id=zone_row.id, name="ناحیه حساس", kind="alert",
        points=[[0, 0], [1, 0], [1, 1], [0, 1]],
        alert_enabled=True, alert_classes=["person"], alert_sound=True,
    )
    config = make_config(zones=[zone])
    pipeline = DetectionPipeline(config, scripted([[person()]]))
    persister = Persister()

    result = pipeline.process(draw(), ts=_t(0), now=0.0)
    await persister.persist(session, config, result)
    await session.commit()

    alert = (await session.execute(select(Alert))).scalars().one()
    assert alert.sound is True
    assert alert.rule_name == "ناحیه حساس"
    assert alert.event_id is not None
    assert "person" in alert.message


async def test_persister_is_idempotent_across_calls(session) -> None:
    await make_camera(session)
    config = make_config()
    pipeline = DetectionPipeline(config, scripted([[person()]]))
    persister = Persister()

    result = pipeline.process(draw(), ts=_t(0), now=0.0)
    await persister.persist(session, config, result)
    await persister.persist(session, config, result)
    await session.commit()

    count = (await session.execute(select(func.count()).select_from(TrackRecord))).scalar()
    assert count == 1


async def test_persist_updates_ended_track(session) -> None:
    await make_camera(session)
    config = make_config()
    pipeline = DetectionPipeline(config, scripted([[person(0.05 + i * 0.05)] for i in range(6)]))
    persister = Persister()

    now = 0.0
    for _ in range(6):
        result = pipeline.process(draw(), ts=_t(now), now=now)
        await persister.persist(session, config, result)
        now += 0.1

    # let the track age out
    pipeline.set_detector(scripted([[]]))
    for _ in range(6):
        now += 0.2
        result = pipeline.process(frame(), ts=_t(now), now=now)
        await persister.persist(session, config, result)
    await session.commit()

    track = (await session.execute(select(TrackRecord))).scalars().one()
    assert track.status == "ended"
    assert track.duration_seconds > 0
    assert track.distance_travelled > 0
    assert track.last_seen >= track.first_seen


async def test_persist_without_snapshots_still_logs_events(session) -> None:
    await make_camera(session)
    config = make_config(record_snapshots=False)
    pipeline = DetectionPipeline(config, scripted([[person()]]))
    persister = Persister()

    result = pipeline.process(draw(), ts=_t(0), now=0.0)
    outcome = await persister.persist(session, config, result)
    await session.commit()

    assert outcome.images == []
    assert any(event.event_type == "enter" for event in outcome.events)


async def test_heatmap_flush_and_query(session) -> None:
    from app.services.heatmap import HeatmapBuffer, flush_buffer, get_heatmap

    await make_camera(session)
    buffer = HeatmapBuffer()  # uses the configured grid
    buffer.add(1, (0.25, 0.35), object_type="person", when=_t(0))
    buffer.add(1, (0.26, 0.36), object_type="person", when=_t(1))
    assert buffer.pending_count == 2

    written = await flush_buffer(session, buffer)
    await session.commit()
    assert written == 1  # both points land in the same cell
    assert buffer.pending_count == 0

    data = await get_heatmap(session, 1, day=_t(0).date())
    assert data["grid_w"] == settings.heatmap_grid_w
    assert sum(data["cells"]) == 2
    assert data["max_count"] == 2

    # a point on the other side of the frame lands in a different cell
    buffer.add(1, (0.85, 0.85), object_type="person", when=_t(2))
    await flush_buffer(session, buffer)
    await session.commit()
    data = await get_heatmap(session, 1, day=_t(0).date())
    assert sum(data["cells"]) == 3


async def test_delete_object_removes_files(session) -> None:
    from app.api.objects import delete_object

    await make_camera(session)
    config = make_config()
    pipeline = DetectionPipeline(config, scripted([[person()]]))
    persister = Persister()
    result = pipeline.process(draw(), ts=_t(0), now=0.0)
    await persister.persist(session, config, result)
    await session.commit()

    track = (await session.execute(select(TrackRecord))).scalars().one()
    image = (
        await session.execute(select(ObjectImage).where(ObjectImage.track_id == track.id))
    ).scalars().first()
    path = settings.storage_root / image.full_path
    assert path.is_file()

    await delete_object(track.id, session)
    assert not path.exists()
    assert (await session.execute(select(func.count()).select_from(TrackRecord))).scalar() == 0


@pytest.mark.parametrize("camera_id", [1, 2])
async def test_relative_paths_are_portable(session, camera_id) -> None:
    await make_camera(session, camera_id)
    config = make_config(camera_id=camera_id)
    pipeline = DetectionPipeline(config, scripted([[person()]]))
    persister = Persister()
    result = pipeline.process(draw(), ts=_t(0), now=0.0)
    await persister.persist(session, config, result)
    await session.commit()

    image = (await session.execute(select(ObjectImage))).scalars().one()
    assert not image.full_path.startswith(("/", "C:"))
    assert image.full_path.startswith(f"objects/{camera_id}/")


# --------------------------------------------------------------------------
# bounded snapshot writer
# --------------------------------------------------------------------------
def test_snapshot_writer_drops_instead_of_growing_without_bound() -> None:
    from app.services.persistence import SnapshotWriter

    gate = threading.Event()
    done: list[int] = []
    started = threading.Event()

    def gate_task() -> None:
        started.set()
        gate.wait(3.0)

    # Capacity 1 and one busy worker: the second snapshot cannot be queued.
    writer = SnapshotWriter(workers=1, capacity=1)
    try:
        writer.submit(gate_task)
        assert started.wait(2.0), "the writer never picked up the blocking task"
        assert writer.submit(lambda: done.append(1)) is True
        assert writer.submit(lambda: done.append(2)) is False
        gate.set()
        deadline = time.monotonic() + 5.0
        while not done and time.monotonic() < deadline:
            time.sleep(0.02)
        assert done == [1]
        stats = writer.stats()
        assert stats["dropped"] == 1
        assert stats["written"] >= 2
    finally:
        gate.set()
        writer.shutdown()


def test_snapshot_writer_resize_keeps_queued_work() -> None:
    from app.services.persistence import SnapshotWriter

    gate = threading.Event()
    started = threading.Event()
    done: list[str] = []

    def gate_task() -> None:
        started.set()
        gate.wait(3.0)

    writer = SnapshotWriter(workers=1, capacity=4)
    try:
        writer.submit(gate_task)
        assert started.wait(2.0)
        writer.submit(lambda: done.append("a"))
        writer.submit(lambda: done.append("b"))
        writer.resize(workers=2, capacity=4)
        gate.set()
        deadline = time.monotonic() + 5.0
        while len(done) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert sorted(done) == ["a", "b"]
        assert writer.stats()["workers"] == 2
    finally:
        gate.set()
        writer.shutdown()


def test_snapshot_writer_reports_errors_without_dying() -> None:
    from app.services.persistence import SnapshotWriter

    ran = threading.Event()
    writer = SnapshotWriter(workers=1, capacity=2)
    try:
        def boom() -> None:
            raise OSError("disk full")

        assert writer.submit(boom) is True
        assert writer.submit(ran.set) is True
        assert ran.wait(3.0), "a failing snapshot must not stop the worker"
        assert writer.stats()["errors"] == 1
        assert "disk full" in (writer.stats()["last_error"] or "")
    finally:
        writer.shutdown()


def test_snapshot_writer_regains_workers_after_shrink_and_grow() -> None:
    """Lowering then raising the worker count must not silently halve the pool."""
    from app.services.persistence import SnapshotWriter

    writer = SnapshotWriter(workers=4, capacity=8)
    try:
        writer.resize(workers=2, capacity=8)
        deadline = time.monotonic() + 3.0
        while writer.stats()["workers"] > 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert writer.stats()["workers"] == 2

        writer.resize(workers=4, capacity=8)
        deadline = time.monotonic() + 3.0
        while writer.stats()["workers"] < 4 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert writer.stats()["workers"] == 4
    finally:
        writer.shutdown()

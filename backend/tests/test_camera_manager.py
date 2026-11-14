"""Camera-manager health persistence.

The manager writes ``health_metrics`` rows for every live stream on a timer.
A camera can disappear from the database while its stream is still draining
(deleted between two ticks, or removed by another process). Inserting metrics
for a missing camera used to abort the whole transaction with a FOREIGN KEY
failure and spam the log; these tests pin the tolerant behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.camera import Camera
from app.models.system import HealthMetric
from app.services.camera_manager import manager
from app.services.stream import StreamHealth


def add_stream(
    camera_id: int,
    state: str = "online",
    fps: float = 20.0,
    *,
    pipeline: object | None = None,
    detection_enabled: bool = True,
    running: bool = True,
) -> None:
    """Register a fake stream so the manager thinks the camera is running."""
    health = StreamHealth()
    health.state = state
    health.fps = fps
    manager._streams[camera_id] = SimpleNamespace(  # type: ignore[assignment]
        health=health,
        pipeline=pipeline,
        running=running,
        config=SimpleNamespace(id=camera_id, detection_enabled=detection_enabled),
        stop=lambda: None,
    )


async def make_camera(session, camera_id: int) -> Camera:
    camera = Camera(id=camera_id, name=f"دوربین {camera_id}", rtsp_url="rtsp://example/stream")
    session.add(camera)
    await session.commit()
    return camera


async def test_write_health_persists_rows_for_existing_cameras(session) -> None:
    await make_camera(session, 1)
    add_stream(1, fps=21.5)

    await manager._write_health()

    rows = (await session.execute(select(HealthMetric))).scalars().all()
    assert len(rows) == 1
    assert rows[0].camera_id == 1
    assert rows[0].state == "online"
    assert rows[0].fps == 21.5


async def test_write_health_skips_deleted_camera_without_failing(session) -> None:
    """A stream whose camera row is gone must not break the batch."""
    await make_camera(session, 1)
    add_stream(1, fps=10.0)
    add_stream(99, fps=5.0)  # never inserted -> would violate the FK

    await manager._write_health()  # must not raise

    rows = (await session.execute(select(HealthMetric))).scalars().all()
    assert [row.camera_id for row in rows] == [1], "only the live camera gets metrics"

    written = (
        await session.execute(select(func.count()).select_from(HealthMetric).where(HealthMetric.camera_id == 99))
    ).scalar()
    assert written == 0


async def test_write_health_is_a_noop_without_streams(session) -> None:
    await manager._write_health()
    count = (await session.execute(select(func.count()).select_from(HealthMetric))).scalar()
    assert count == 0


async def test_write_health_prunes_rows_past_retention(session) -> None:
    from datetime import timedelta

    from app.core.config import settings
    from app.core.timeutil import utcnow

    await make_camera(session, 1)
    session.add(
        HealthMetric(
            camera_id=1,
            state="online",
            recorded_at=utcnow() - timedelta(days=settings.retention_days + 5),
        )
    )
    await session.commit()

    add_stream(1)
    await manager._write_health()

    rows = (await session.execute(select(HealthMetric))).scalars().all()
    assert all(row.recorded_at > utcnow() - timedelta(days=settings.retention_days) for row in rows)
    assert len(rows) == 1


class _UnavailableDetector:
    """Mimics an RFDETR detector whose weights/torch failed to load."""

    name = "rfdetr:unavailable"
    available = False
    load_error = "ModuleNotFoundError: No module named 'rfdetr'"

    def detect(self, frame):  # noqa: ANN001, ARG002
        return []

    def close(self) -> None:
        pass


def test_missing_detector_does_not_build_a_pipeline(monkeypatch) -> None:
    """Regression: an unavailable detector must not produce a pipeline.

    Otherwise the stream runs a detection pass per frame that can never find
    anything, and ``detection_fps`` reports a rate for work that does nothing.
    """
    from app.services import detector as detector_module

    released: list[object] = []
    detector = _UnavailableDetector()
    monkeypatch.setattr(detector_module.registry, "acquire", lambda *a, **k: detector)
    monkeypatch.setattr(detector_module.registry, "release", lambda d: released.append(d))

    from app.services.stream import CameraRuntimeConfig

    config = CameraRuntimeConfig(id=7, name="تست", rtsp_url="rtsp://example/x", detection_enabled=True)

    assert manager._build_pipeline(config) is None
    assert released == [detector], "the unused detector must be released"
    assert "rfdetr" in (manager._detector_errors[7] or "")

    # and the manager explains itself only while detection is wanted
    add_stream(7)
    assert manager.detector_error(7) == _UnavailableDetector.load_error
    manager._streams[7].config.detection_enabled = False
    assert manager.detector_error(7) is None
    manager._detector_errors.clear()


def test_available_detector_clears_a_previous_error(monkeypatch) -> None:
    from app.services import detector as detector_module
    from app.services.detector import FakeDetector
    from app.services.stream import CameraRuntimeConfig

    good = FakeDetector([])
    monkeypatch.setattr(detector_module.registry, "acquire", lambda *a, **k: good)
    config = CameraRuntimeConfig(id=8, name="تست", rtsp_url="rtsp://example/x", detection_enabled=True)
    manager._detector_errors[8] = "stale failure"

    pipeline = manager._build_pipeline(config)

    assert pipeline is not None
    assert 8 not in manager._detector_errors


class ExplodingStream:
    """A stream whose ``stop`` raises, with no ``config`` attribute at all."""

    def __init__(self) -> None:
        self.health = StreamHealth()
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True
        raise RuntimeError("boom")


def test_reset_survives_a_broken_stream() -> None:
    """One bad stream must not stop the others from being stopped, and the
    failure log itself must not raise (it runs on the shutdown path)."""
    broken = ExplodingStream()
    healthy = ExplodingStream()
    healthy.stop = lambda: setattr(healthy, "stopped", True)  # type: ignore[method-assign]
    manager._streams[1] = broken  # type: ignore[assignment]
    manager._streams[2] = healthy  # type: ignore[assignment]

    manager.reset()  # must not raise

    assert broken.stopped is True
    assert healthy.stopped is True
    assert manager._streams == {}


def test_active_detection_cameras_requires_a_loaded_pipeline() -> None:
    """The GPU panel's "active cameras" must count real detectors only."""
    add_stream(1, detection_enabled=True)  # enabled but no pipeline -> no GPU work
    add_stream(2, detection_enabled=True, pipeline=object())
    add_stream(3, detection_enabled=False, pipeline=object())

    assert manager.active_detection_cameras() == 1

    manager._streams[1].pipeline = object()
    assert manager.active_detection_cameras() == 2


def test_remove_camera_forgets_state_but_keeps_others() -> None:
    from app.services.persistence import Persister

    add_stream(1)
    add_stream(2)
    manager._persisters[1] = Persister()

    manager.remove_camera(1)

    assert set(manager._streams) == {2}
    assert 1 not in manager._persisters


def test_remove_camera_is_tolerant_when_stop_fails() -> None:
    manager._streams[5] = ExplodingStream()  # type: ignore[assignment]

    manager.remove_camera(5)  # must not raise

    assert 5 not in manager._streams


@pytest.mark.parametrize("state", ["offline", "connecting", "error"])
async def test_write_health_records_any_state(session, state: str) -> None:
    await make_camera(session, 1)
    add_stream(1, state=state)

    await manager._write_health()

    row = (await session.execute(select(HealthMetric))).scalars().one()
    assert row.state == state


def test_detector_preload_args_skip_disabled_cameras() -> None:
    """Only cameras that actually want detection may trigger a model load."""
    from app.services.camera_manager import CameraManager

    enabled = SimpleNamespace(
        id=3, enabled=True, detection_enabled=True, model_name="rf-detr-base", model_weights=None
    )
    args = CameraManager.detector_preload_args(enabled)
    assert args == {"camera_id": 3, "model_name": "rf-detr-base", "model_weights": None}

    off = SimpleNamespace(
        id=4, enabled=True, detection_enabled=False, model_name="rf-detr-base", model_weights=None
    )
    assert CameraManager.detector_preload_args(off) is None

    disabled = SimpleNamespace(
        id=5, enabled=False, detection_enabled=True, model_name="rf-detr-base", model_weights=None
    )
    assert CameraManager.detector_preload_args(disabled) is None


async def test_persist_batch_drops_results_of_a_camera_deleted_mid_flight(
    session, monkeypatch
) -> None:
    """A delete that races the persister must drop results, not raise.

    The camera existed when the check ran (streams can still be draining), so
    the INSERT hits a foreign-key error. Reporting that as a crash per queued
    frame filled the log; the results now go away with one info line.
    """
    from app.core.timeutil import utcnow
    from app.services.pipeline import PipelineResult, TrackSnapshot
    from app.services.stream import CameraRuntimeConfig

    config = CameraRuntimeConfig(id=999, name="حذف‌شده", rtsp_url="rtsp://example/x")
    now = utcnow()
    snapshot = TrackSnapshot(
        runtime_track_id=1,
        object_type="person",
        confidence=0.9,
        first_seen=now,
        last_seen=now,
        duration_seconds=0.1,
        motion_state="moving",
        direction="right",
        velocity_x=0.1,
        velocity_y=0.0,
        distance_travelled=0.1,
        bbox_first=[0.1, 0.1, 0.2, 0.2],
        bbox_last=[0.11, 0.1, 0.21, 0.2],
        status="active",
        entered_frame=True,
        exited_frame=False,
        zone_id=None,
        crossed_line_id=None,
        line_direction=None,
        frame_count=2,
        meta={},
    )
    result = PipelineResult(timestamp=now, camera_id=999, started_tracks=[snapshot])

    calls = {"count": 0}

    async def fake_exists(camera_id: int) -> bool:  # noqa: ARG001
        calls["count"] += 1
        return calls["count"] == 1  # existed for the batch, gone when re-checked

    monkeypatch.setattr(manager, "_camera_exists", fake_exists)
    await manager._persist_batch(config, [result])  # must not raise

    assert calls["count"] >= 2, "the delete must be detected by a second check"

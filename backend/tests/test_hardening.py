"""Regression tests for lifecycle, threading and configuration claims.

Every test here was written against the code *before* the fix it guards, and
each one failed (or the behaviour was measured) before that fix landed.
"""

from __future__ import annotations

import json
import threading
import time

import numpy as np
import pytest

from app.services import performance
from app.services.detector import FakeDetector, parse_raw_output
from app.services.inference import scheduler
from app.services.pipeline import DetectionPipeline, PipelineConfig
from app.services.stream import CameraRuntimeConfig, CameraStream


def make_config(**overrides) -> CameraRuntimeConfig:
    base = {
        "id": 77,
        "name": "hardening",
        "rtsp_url": "rtsp://example/hardening",
        "fps": 20,
    }
    base.update(overrides)
    return CameraRuntimeConfig(**base)


def frame(width: int = 1280, height: int = 720, value: int = 10) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.uint8)


class GatedPipeline:
    """A pipeline whose ``process`` blocks until the test lets it finish."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.gate = threading.Event()
        self.finished = threading.Event()
        self.detector = FakeDetector(lambda _f, _i: [])
        self.config = PipelineConfig(camera_id=77)

    def process(self, _frame, *, ts=None, now=None):  # noqa: ANN001, ARG002
        self.started.set()
        self.gate.wait(5.0)
        self.finished.set()

        class _Result:
            detections: list = []
            active_tracks: list = []
            snapshots: list = []
            events: list = []

        return _Result()


# --------------------------------------------------------------------------
# 1. Stop / Reload must not release a pipeline an inference job is using
# --------------------------------------------------------------------------
def test_stop_waits_for_the_running_inference_job() -> None:
    scheduler.reset()
    try:
        pipeline = GatedPipeline()
        stream = CameraStream(make_config(), pipeline=pipeline)  # type: ignore[arg-type]
        stream._submit_detection(frame(), time.monotonic())
        assert pipeline.started.wait(2.0), "the scheduler never ran the job"

        stopped = threading.Event()

        def stop() -> None:
            stream.stop(timeout=1.0)
            stopped.set()

        worker = threading.Thread(target=stop, daemon=True)
        worker.start()
        # The job is still inside the pipeline: stop() must not report the
        # camera stopped (and the detector must not be released) yet.
        assert not stopped.wait(0.25), "stop() returned while inference was still running"
        pipeline.gate.set()
        assert stopped.wait(6.0), "stop() never returned after the job finished"
        assert pipeline.finished.is_set()
    finally:
        pipeline.gate.set()
        scheduler.shutdown()


def test_unregistering_a_camera_leaves_no_stats_behind() -> None:
    scheduler.reset()
    try:
        pipeline = GatedPipeline()
        stream = CameraStream(make_config(id=78), pipeline=pipeline)  # type: ignore[arg-type]
        stream._submit_detection(frame(), time.monotonic())
        assert pipeline.started.wait(2.0)
        scheduler.unregister(78)
        pipeline.gate.set()
        time.sleep(0.2)
        assert "78" not in scheduler.stats()["detail"]
    finally:
        pipeline.gate.set()
        scheduler.shutdown()


# --------------------------------------------------------------------------
# 2. JPEG encoding must not run on the RTSP decode thread
# --------------------------------------------------------------------------
def test_publishing_a_frame_does_not_encode_on_the_decode_thread(monkeypatch) -> None:
    stream = CameraStream(make_config(), reader_factory=lambda _cfg: None)
    stream.mark_live_view(20)

    def slow_encode(frm):  # noqa: ANN001
        time.sleep(0.05)
        return b"jpeg"

    monkeypatch.setattr(stream, "_encode_live", slow_encode)
    started = time.perf_counter()
    stream._publish_frame(frame())
    elapsed = time.perf_counter() - started
    assert elapsed < 0.02, f"decode thread spent {elapsed * 1000:.0f}ms encoding a JPEG"

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if stream.latest_jpeg(encode_if_missing=False) == b"jpeg":
            break
        time.sleep(0.01)
    assert stream.latest_jpeg(encode_if_missing=False) == b"jpeg"
    stream.stop()


# --------------------------------------------------------------------------
# 3. The detection downscale must honour the configured width exactly
# --------------------------------------------------------------------------
def test_detector_frame_honours_the_width_limit() -> None:
    pipeline = DetectionPipeline(
        PipelineConfig(camera_id=1, detection_max_width=1280), FakeDetector(lambda _f, _i: [])
    )
    smaller = pipeline._detector_frame(np.zeros((1080, 1920, 3), dtype=np.uint8))
    assert smaller.shape[1] == 1280
    assert smaller.shape[0] == 720  # aspect ratio preserved, not 540


def test_boxes_stay_normalised_after_a_downscale() -> None:
    """The stride must not shift coordinates: boxes are normalised per frame."""

    class Raw:
        xyxy = [[96.0, 54.0, 192.0, 108.0]]
        confidence = [0.9]
        class_id = [0]
        data = {"class_name": ["person"]}

    boxes = parse_raw_output(Raw(), 1920, 1080)
    assert boxes[0].bbox == pytest.approx([0.05, 0.05, 0.1, 0.1], abs=1e-6)
    scaled = parse_raw_output(Raw(), 1280, 720)
    assert scaled[0].bbox == pytest.approx([0.075, 0.075, 0.15, 0.15], abs=1e-6)


# --------------------------------------------------------------------------
# 4. CPU-only has to be a real, working path
# --------------------------------------------------------------------------
def test_cuda_request_falls_back_to_cpu_without_cuda(monkeypatch) -> None:
    torch = pytest.importorskip("torch")
    from app.services.detector import resolve_device

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert resolve_device("cuda") == "cpu"
    assert resolve_device("cuda:0") == "cpu"
    assert resolve_device("cpu") == "cpu"


def test_cpu_device_keeps_cores_free_for_the_decoders(monkeypatch) -> None:
    torch = pytest.importorskip("torch")
    from app.services.detector import RFDetrDetector

    seen: list[int] = []
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch, "set_num_threads", lambda count: seen.append(count))
    detector = RFDetrDetector("rf-detr-small", device="cuda")
    detector._configure_torch()
    assert seen and seen[0] >= 1


# --------------------------------------------------------------------------
# 5. Saved performance profiles are validated when they are loaded
# --------------------------------------------------------------------------
def test_saved_profile_values_are_validated_on_load(tmp_path, monkeypatch) -> None:
    path = tmp_path / "performance.json"
    path.write_text(
        json.dumps(
            {
                "stream_fps": 999,
                "detection_fps": -3,
                "snapshot_workers": 0,
                "snapshot_queue_size": 1,
                "device": "gpu",
                "fp16": "maybe",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(performance, "profile_path", lambda: path)
    defaults = performance.default_profile()
    profile = performance.load_profile()
    # Out-of-range values are rejected and the machine's defaults are kept.
    assert profile.stream_fps == defaults.stream_fps
    assert profile.detection_fps == defaults.detection_fps
    assert profile.snapshot_workers == defaults.snapshot_workers
    assert profile.snapshot_queue_size == defaults.snapshot_queue_size
    assert profile.device in {"cuda", "cpu"}
    assert profile.fp16 in {"auto", "on", "off"}


# --------------------------------------------------------------------------
# 5b. Worker pools and odd-sized frames
# --------------------------------------------------------------------------
def test_snapshot_writer_survives_repeated_resizes() -> None:
    """2 -> 4 -> 2 -> 4 must always leave the configured number of live workers."""
    from app.services.persistence import SnapshotWriter

    writer = SnapshotWriter(workers=2, capacity=8)
    try:
        for target in (4, 2, 4, 2, 4):
            writer.resize(workers=target, capacity=8)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                stats = writer.stats()
                if stats["workers"] == target and stats["workers_configured"] == target:
                    break
                time.sleep(0.02)
            assert stats["workers"] == target, stats
            assert stats["workers_configured"] == target, stats
    finally:
        writer.shutdown()


@pytest.mark.parametrize("shape", [(1080, 1920), (720, 1279), (331, 941), (13, 7), (2, 2)])
def test_unusual_frame_sizes_do_not_break_the_pipeline(shape) -> None:  # noqa: ANN001
    from app.services.imaging import fit_width
    from app.services.motion import MotionDetector

    height, width = shape
    frame_data = np.full((height, width, 3), 40, dtype=np.uint8)
    detector = MotionDetector()
    detector.update(frame_data)
    assert detector.update(frame_data).motion is False

    pipeline = DetectionPipeline(
        PipelineConfig(camera_id=1, detection_max_width=1280),
        FakeDetector(lambda _f, _i: []),
    )
    handed = pipeline._detector_frame(frame_data)
    assert handed.ndim == 3 and handed.flags.writeable
    if width > 1280:
        assert handed.shape[1] == 1280
        assert abs(handed.shape[0] / handed.shape[1] - height / width) < 0.01

    scaled = fit_width(frame_data, 0)
    assert scaled.shape == frame_data.shape


# --------------------------------------------------------------------------
# 6. The scheduler survives shutdown and restart
# --------------------------------------------------------------------------
def test_scheduler_restarts_after_shutdown() -> None:
    scheduler.reset()
    scheduler.shutdown()
    threads_before = len(threading.enumerate())
    scheduler.register(5)
    assert scheduler.submit(5, lambda: None)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and scheduler.stats()["processed"] < 1:
        time.sleep(0.02)
    assert scheduler.stats()["processed"] >= 1
    scheduler.shutdown()
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline and len(threading.enumerate()) > threads_before:
        time.sleep(0.05)
    assert len(threading.enumerate()) <= threads_before + 1

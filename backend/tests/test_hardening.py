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
from app.services.inference import InferenceScheduler, scheduler
from app.services.pipeline import DetectionPipeline, PipelineConfig
from app.services.stream import CameraRuntimeConfig, CameraStream


def wait_for(predicate, timeout: float = 3.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


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
        # Regression: retirement compared the serial with ``workers``, so after
        # a few cycles every new worker retired instantly and ``resize`` spawned
        # replacements until the OS refused (13 744 threads measured).
        assert writer._next_serial <= 2 + 2 * 3 + 4, writer._next_serial
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
# 7. Claims from the second review: scheduler races, queues, dispatcher, config
# --------------------------------------------------------------------------
def test_a_claimed_job_keeps_the_camera_busy() -> None:
    """There is a window between ``_next_job`` and setting ``_current``.

    Inside it ``wait_camera_idle`` used to answer "idle" for a camera whose job
    had already been taken off the queue, so ``stop()`` released the pipeline
    one instruction before that job entered it.
    """
    sched = InferenceScheduler()
    try:
        sched.register(91)
        assert sched.submit(91, lambda: None)
        claimed = sched._next_job()
        assert claimed is not None
        assert sched.wait_camera_idle(91, timeout=0.05) is False
    finally:
        sched.shutdown()


def test_a_claimed_job_of_an_unregistered_camera_is_not_run() -> None:
    sched = InferenceScheduler()
    try:
        sched.register(92)
        assert sched.submit(92, lambda: None)
        assert sched._next_job() is not None
        sched.unregister(92)
        assert sched._claim_is_valid(92) is False
    finally:
        sched.shutdown()


def test_shutdown_timeout_does_not_start_a_second_dispatcher() -> None:
    """A dispatcher that outlives its join must not be revived by a new start."""
    sched = InferenceScheduler(name="dispatcher-probe")
    gate = threading.Event()
    started = threading.Event()

    def stuck() -> None:
        started.set()
        gate.wait(5.0)

    try:
        sched.register(93)
        assert sched.submit(93, stuck)
        assert started.wait(2.0)
        sched.shutdown(timeout=0.1)
        # Work submitted after a shutdown that could not join the stuck job must
        # still be processed, by exactly one dispatcher.
        for _ in range(3):
            sched.submit(93, lambda: None)
        # ``queue_size`` is 1 here, so the three submits collapse into one job
        # plus two drops: the point is that the queue is served at all.
        assert wait_for(lambda: sched.stats()["processed"] >= 1, timeout=3.0)
        # The old thread keeps its own (already set) stop event: once its job
        # returns it exits instead of sharing the queue with the new one.
        assert sched._stale, "the stuck dispatcher should be remembered"
        gate.set()
        assert wait_for(
            lambda: not [t for t in threading.enumerate() if t.name == "dispatcher-probe" and t.is_alive() and t is not sched._thread],
            timeout=4.0,
        ), "a second dispatcher survived the stuck job"
        alive = [t for t in threading.enumerate() if t.name == "dispatcher-probe" and t.is_alive()]
        assert len(alive) == 1
    finally:
        gate.set()
        sched.shutdown()


def test_a_multi_frame_queue_runs_the_newest_frame() -> None:
    """With ``inference_queue_size > 1`` the older waiting frame must be dropped."""
    sched = InferenceScheduler()
    try:
        sched.set_queue_size(2)
        sched.register(94)
        assert sched.submit(94, lambda: None)   # oldest
        assert sched.submit(94, lambda: None)   # newest
        claimed = sched._next_job()
        assert claimed is not None
        # Only the newest frame may remain: the older one was dropped.
        assert sched.pending() == 0
        assert sched.stats()["detail"]["94"]["dropped"] == 1
    finally:
        sched.shutdown()


def test_live_pipelines_receive_a_new_detection_width() -> None:
    """A performance change must reach cameras that are already running."""
    from app.services import performance as perf
    from app.services.camera_manager import CameraManager

    manager = CameraManager()
    config = make_config(detection_enabled=True)
    pipeline = DetectionPipeline(
        config.to_pipeline_config(), FakeDetector(lambda _f, _i: [])
    )
    stream = CameraStream(config, pipeline=pipeline)
    manager._streams[config.id] = stream  # noqa: SLF001 - test seam
    settings_before = perf.settings.detection_max_width
    try:
        perf.settings.detection_max_width = 640
        manager.apply_performance()
        assert pipeline.config.detection_max_width == 640
        # Per-camera values must survive: the profile only holds defaults.
        assert stream.config.detection_fps == 5.0
        assert stream.config.fps == 20
    finally:
        perf.settings.detection_max_width = settings_before


def test_downscale_keeps_the_exact_width_without_opencv(monkeypatch) -> None:
    """The stride fallback must not turn a 1280 limit into 960 px again."""
    import builtins
    import sys

    from app.services import imaging

    real_import = builtins.__import__

    def no_cv2(name, *args, **kwargs):  # noqa: ANN001
        if name == "cv2":
            raise ImportError("no cv2 in this environment")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "cv2", None)
    monkeypatch.setattr(builtins, "__import__", no_cv2)
    wide = np.zeros((1080, 1920, 3), dtype=np.uint8)
    scaled = imaging.fit_width(wide, 1280)
    assert scaled.shape[1] == 1280
    assert scaled.shape[0] == 720


@pytest.mark.parametrize("flags", ["readonly", "noncontiguous"])
def test_pipeline_accepts_awkward_numpy_arrays(flags) -> None:  # noqa: ANN001
    """Read-only and non-contiguous frames are normal from ffmpeg/PIL paths."""
    base = np.zeros((720, 2560, 3), dtype=np.uint8)
    frame_data = base[:, ::2] if flags == "noncontiguous" else np.zeros((720, 1280, 3), np.uint8)
    frame_data.setflags(write=(flags != "readonly"))

    pipeline = DetectionPipeline(
        PipelineConfig(camera_id=1, require_motion=False),
        FakeDetector(lambda _f, _i: []),
    )
    pipeline.process(frame_data)

    stream = CameraStream(make_config(), reader_factory=lambda _cfg: None)
    stream.mark_live_view(20)
    stream._publish_frame(frame_data)
    assert wait_for(lambda: stream.latest_jpeg(encode_if_missing=False) is not None)
    stream.stop()

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

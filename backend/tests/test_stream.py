"""Streaming layer tests (no real RTSP / ffmpeg process required)."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from app.services.detector import FakeDetector
from app.services.pipeline import DetectionPipeline
from app.services.stream import (
    FFmpegFrameReader,
    CameraRuntimeConfig,
    CameraStream,
)
from app.services.tracker import Detection

WIDTH, HEIGHT = 160, 90


def frame(value: int = 40) -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8)


def person() -> Detection:
    return Detection(bbox=[0.3, 0.3, 0.5, 0.7], confidence=0.9, object_type="person")


class FakeReader:
    """A frame reader with controllable failures."""

    def __init__(
        self,
        *,
        fail_open: bool = False,
        fail_at: int | None = None,
        frame_value: int = 40,
    ) -> None:
        self.fail_open = fail_open
        self.fail_at = fail_at
        self.frame_value = frame_value
        self.reads = 0
        self.closed = False
        self.opened = False

    def open(self) -> bool:
        if self.fail_open:
            return False
        self.opened = True
        return True

    @property
    def running(self) -> bool:
        return self.opened and not self.closed

    def read(self) -> np.ndarray | None:
        self.reads += 1
        if self.fail_at is not None and self.reads > self.fail_at:
            return None
        time.sleep(0.001)
        return frame(self.frame_value)

    def close(self) -> None:
        self.closed = True


def make_config(**overrides) -> CameraRuntimeConfig:
    base = dict(
        id=1,
        name="دوربین تست",
        rtsp_url="rtsp://example/stream",
        enabled=True,
        fps=60,
        detection_fps=30.0,
        detection_enabled=False,
        class_config={"person": {"enabled": True, "threshold": 0.3, "min_motion": 0.0}},
        reconnect_policy={"backoff_initial": 0.05, "backoff_max": 0.1, "backoff_factor": 1.0},
    )
    base.update(overrides)
    return CameraRuntimeConfig(**base)


def wait_for(predicate, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# --------------------------------------------------------------------------
# reader
# --------------------------------------------------------------------------
def test_ffmpeg_command_contains_expected_flags() -> None:
    reader = FFmpegFrameReader(
        "rtsp://cam/1", width=640, height=360, fps=15, transport="tcp"
    )
    command = reader.command()
    assert command[0] == "ffmpeg"
    assert "-rtsp_transport" in command
    assert command[command.index("-rtsp_transport") + 1] == "tcp"
    assert "fps=15" in command[command.index("-vf") + 1]
    assert command[-1] == "-"
    assert reader.frame_bytes == 640 * 360 * 3


def test_ffmpeg_reader_uses_scale_only_when_requested() -> None:
    scaled = FFmpegFrameReader("rtsp://x", width=320, height=180, fps=5, scale=True)
    assert "scale=320:180" in scaled.command()[scaled.command().index("-vf") + 1]
    plain = FFmpegFrameReader("rtsp://x", width=320, height=180, fps=5)
    assert "scale=" not in plain.command()[plain.command().index("-vf") + 1]


def test_ffmpeg_reader_handles_missing_binary() -> None:
    reader = FFmpegFrameReader("rtsp://x", binary="definitely-not-a-real-binary-xyz")
    assert reader.open() is False
    assert reader.read() is None
    reader.close()  # must not raise


def test_probe_returns_none_for_unreachable_stream() -> None:
    from app.services.stream import probe_stream

    # Non-routable address -> ffprobe fails fast and we fall back gracefully.
    assert probe_stream("rtsp://192.0.2.1:1/nope", timeout=1.0) is None


# --------------------------------------------------------------------------
# camera stream
# --------------------------------------------------------------------------
def test_stream_becomes_online_and_publishes_frames() -> None:
    reader = FakeReader()
    stream = CameraStream(make_config(), reader_factory=lambda _cfg: reader)
    stream.start()
    try:
        assert wait_for(lambda: stream.health.state == "online")
        assert wait_for(lambda: stream.health.frames_total > 3)
        assert stream.latest_jpeg() is not None
        assert stream.latest_frame() is not None
        assert stream.health.width == WIDTH
        assert stream.health.height == HEIGHT
    finally:
        stream.stop()
    assert reader.closed


def test_stream_reports_reconnecting_when_open_fails() -> None:
    states: list[str] = []
    stream = CameraStream(
        make_config(),
        reader_factory=lambda _cfg: FakeReader(fail_open=True),
        on_state_change=lambda _cfg, state, _msg: states.append(state),
    )
    stream.start()
    try:
        assert wait_for(lambda: "reconnecting" in states)
    finally:
        stream.stop()
    assert "reconnecting" in states


def test_stream_reconnects_after_decode_failures() -> None:
    readers: list[FakeReader] = []

    def factory(_cfg):
        reader = FakeReader(fail_at=3) if len(readers) < 2 else FakeReader()
        readers.append(reader)
        return reader

    stream = CameraStream(
        make_config(reconnect_policy={"backoff_initial": 0.02, "backoff_max": 0.02, "backoff_factor": 1.0}),
        reader_factory=factory,
    )
    stream.start()
    try:
        assert wait_for(lambda: len(readers) >= 2, timeout=5.0)
        assert wait_for(lambda: stream.health.reconnect_count >= 2)
        assert wait_for(lambda: stream.health.dropped_frames > 0)
    finally:
        stream.stop()


def test_disabled_camera_does_not_start() -> None:
    stream = CameraStream(make_config(enabled=False))
    stream.start()
    assert stream.running is False
    assert stream.health.state == "disabled"
    stream.stop()


def test_stream_runs_detection_and_reports_results() -> None:
    reader = FakeReader()
    config = make_config(detection_enabled=True)
    pipeline = DetectionPipeline(config.to_pipeline_config(), FakeDetector([[person()]]))
    pipeline.config.require_motion = False

    results: list = []
    lock = threading.Lock()

    def on_result(_cfg, result) -> None:
        with lock:
            results.append(result)

    stream = CameraStream(
        config, pipeline=pipeline, on_result=on_result, reader_factory=lambda _cfg: reader
    )
    stream.start()
    try:
        assert wait_for(lambda: len(results) > 0)
        assert stream.health.detections_total > 0
        assert wait_for(lambda: stream.health.detection_fps > 0)
        assert stream.latest_overlay_jpeg() is not None
    finally:
        stream.stop()

    with lock:
        assert results[0].camera_id == 1
        assert results[0].active_tracks


def test_detection_is_skipped_when_disabled() -> None:
    reader = FakeReader()
    config = make_config(detection_enabled=False)
    pipeline = DetectionPipeline(config.to_pipeline_config(), FakeDetector([[person()]]))
    pipeline.config.require_motion = False
    results: list = []

    stream = CameraStream(
        config,
        pipeline=pipeline,
        on_result=lambda _c, result: results.append(result),
        reader_factory=lambda _cfg: reader,
    )
    stream.start()
    try:
        assert wait_for(lambda: stream.health.frames_total > 5)
        time.sleep(0.1)
        assert results == []
        assert stream.health.detections_total == 0
    finally:
        stream.stop()


def test_detection_can_be_toggled_at_runtime() -> None:
    reader = FakeReader()
    config = make_config(detection_enabled=False)
    pipeline = DetectionPipeline(config.to_pipeline_config(), FakeDetector([[person()]]))
    pipeline.config.require_motion = False
    results: list = []
    stream = CameraStream(
        config,
        pipeline=pipeline,
        on_result=lambda _c, result: results.append(result),
        reader_factory=lambda _cfg: reader,
    )
    stream.start()
    try:
        assert wait_for(lambda: stream.health.frames_total > 3)
        stream.enable_detection(True)
        assert wait_for(lambda: len(results) > 0)
    finally:
        stream.stop()


def test_status_payload_shape() -> None:
    reader = FakeReader()
    stream = CameraStream(make_config(), reader_factory=lambda _cfg: reader)
    stream.start()
    try:
        assert wait_for(lambda: stream.health.state == "online")
        status = stream.status()
        for key in (
            "state",
            "fps",
            "detection_fps",
            "bitrate_kbps",
            "width",
            "height",
            "latency_ms",
            "jitter_ms",
            "dropped_frames",
            "reconnect_count",
            "last_frame_at",
            "camera_id",
            "camera_name",
            "detection_enabled",
        ):
            assert key in status, key
    finally:
        stream.stop()


def test_reload_applies_new_configuration() -> None:
    readers: list[FakeReader] = []
    stream = CameraStream(make_config(fps=60), reader_factory=lambda cfg: readers.append(FakeReader()) or readers[-1])
    stream.start()
    try:
        assert wait_for(lambda: len(readers) == 1)
        stream.reload(make_config(fps=30, name="نام جدید"))
        assert wait_for(lambda: len(readers) >= 2)
        assert stream.config.name == "نام جدید"
    finally:
        stream.stop()


def test_camera_runtime_config_builds_pipeline_config() -> None:
    config = make_config(detection_enabled=True)
    pipeline_config = config.to_pipeline_config()
    assert pipeline_config.camera_id == 1
    assert pipeline_config.require_motion is True
    assert "person" in pipeline_config.class_config


@pytest.mark.parametrize("fps", [5, 20, 60])
def test_reported_fps_is_reasonable(fps) -> None:
    reader = FakeReader()
    stream = CameraStream(make_config(fps=fps), reader_factory=lambda _cfg: reader)
    stream.start()
    try:
        assert wait_for(lambda: stream.health.fps > 0)
    finally:
        stream.stop()


# --------------------------------------------------------------------------
# Input handling: protocol specific flags and Windows file URLs
# --------------------------------------------------------------------------


def test_rtsp_inputs_get_the_transport_flag() -> None:
    """``-rtsp_transport`` is required for RTSP but is an error for anything else."""
    reader = FFmpegFrameReader("rtsp://cam.local:554/stream", width=64, height=64)
    command = reader.command()
    assert command[command.index("-i") + 1] == "rtsp://cam.local:554/stream"
    assert "-rtsp_transport" in command
    assert command[command.index("-rtsp_transport") + 1] == "tcp"


def test_udp_transport_is_honoured() -> None:
    reader = FFmpegFrameReader("rtsp://cam.local/stream", width=64, height=64, transport="udp")
    command = reader.command()
    assert command[command.index("-rtsp_transport") + 1] == "udp"


@pytest.mark.parametrize(
    "url",
    [
        "http://cam.local/video.mjpg",
        "https://cam.local/video.mp4",
        "rtmp://cam.local/live",
    ],
)
def test_non_rtsp_inputs_never_receive_rtsp_options(url: str) -> None:
    """Regression: ffmpeg aborts with "Option rtsp_transport not found".

    The old code passed ``-rtsp_transport`` unconditionally, so any HTTP/RTMP
    camera could never connect.
    """
    command = FFmpegFrameReader(url, width=64, height=64).command()
    assert "-rtsp_transport" not in command
    assert command[command.index("-i") + 1] == url


def test_local_files_get_no_protocol_flags() -> None:
    from app.services.stream import input_options

    assert input_options("C:/clips/a.mp4") == []
    assert input_options("/var/clips/a.mp4") == []


def test_windows_file_urls_are_normalised_to_native_paths() -> None:
    """Regression: ``file:///C:/x.mp4`` is rejected by the Windows ffmpeg build."""
    import os

    from app.services.stream import normalise_input

    if os.name != "nt":
        pytest.skip("Windows-specific path normalisation")

    assert normalise_input("file:///C:/clips/a.mp4") == "C:/clips/a.mp4"
    assert normalise_input("file:///C:/clips/with%20space.mp4") == "C:/clips/with space.mp4"
    assert normalise_input("rtsp://cam.local/stream") == "rtsp://cam.local/stream"
    assert normalise_input("  rtsp://cam.local/stream  ") == "rtsp://cam.local/stream"


def test_file_url_is_opened_by_the_native_path() -> None:
    from app.services.stream import normalise_input

    reader = FFmpegFrameReader("file:///C:/clips/a.mp4", width=64, height=64)
    command = reader.command()
    assert command[command.index("-i") + 1] == normalise_input("file:///C:/clips/a.mp4")
    assert "-rtsp_transport" not in command
    # Local files must be paced to real time, otherwise the whole clip is
    # decoded in milliseconds and the stream immediately hits EOF.
    assert "-re" in command


def test_probe_command_avoids_ffmpeg_only_options() -> None:
    """Regression: ffprobe rejects ``-nostdin`` with "Option not found".

    Passing it made every probe fail, so the camera "test" button always
    reported a broken stream and every reader fell back to 1280x720 scaling.
    """
    import shutil
    import subprocess

    from app.services.stream import probe_command

    assert "-nostdin" not in probe_command("rtsp://cam.local/stream", timeout=5.0)
    assert "-rtsp_transport" in probe_command("rtsp://cam.local/stream", timeout=5.0)
    # A hard protocol timeout must be present for network inputs.
    assert "-rw_timeout" in probe_command("rtsp://cam.local/stream", timeout=5.0)

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe are not installed")

    from app.services.stream import probe_stream

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        clip = Path(directory) / "probe.mp4"
        built = subprocess.run(  # noqa: S603
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=96x54:rate=10",
                "-t",
                "1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(clip),
            ],
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        if built.returncode != 0 or not clip.is_file():
            pytest.skip("ffmpeg could not generate a sample clip")

        probe = probe_stream(clip.as_uri(), timeout=20.0)
        assert probe is not None, "probing a local file must succeed"
        assert (probe.width, probe.height) == (96, 54)
        assert probe.codec == "h264"
        # The container bitrate must be preferred over a decoded-size estimate.
        assert probe.bitrate_kbps is not None and probe.bitrate_kbps > 0

        # A plain Windows path must work as well.
        assert probe_stream(str(clip), timeout=20.0) is not None


def test_scheme_detection() -> None:
    from app.services.stream import stream_scheme

    assert stream_scheme("rtsp://a/b") == "rtsp"
    assert stream_scheme("RTSPs://a/b") == "rtsps"
    assert stream_scheme("C:/clips/a.mp4") == ""


def test_run_bounded_kills_a_hanging_command() -> None:
    """``subprocess.run(timeout=...)`` is unreliable on Windows; ours is not."""
    import sys
    import time

    from app.services.stream import _run_bounded

    started = time.monotonic()
    result = _run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], 1.0)
    elapsed = time.monotonic() - started
    assert result is None, "a hung child must be abandoned"
    assert elapsed < 10, f"the deadline was not enforced (took {elapsed:.1f}s)"


def test_run_bounded_returns_output_for_a_quick_command() -> None:
    import sys

    from app.services.stream import _run_bounded

    result = _run_bounded([sys.executable, "-c", "print('ok')"], 10.0)
    assert result is not None
    returncode, stdout, _stderr = result
    assert returncode == 0
    assert stdout.strip() == "ok"


def test_probe_is_hard_bounded_for_unreachable_hosts() -> None:
    """Regression: an unreachable camera used to block the probe forever."""
    import shutil
    import time

    from app.services.stream import probe_stream

    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe is not installed")

    started = time.monotonic()
    assert probe_stream("rtsp://192.0.2.1:1/nope", timeout=2.0) is None
    assert time.monotonic() - started < 20


# --------------------------------------------------------------------------
# live-view budgeting / reload behaviour
# --------------------------------------------------------------------------
def test_downscale_caps_the_live_frame_width() -> None:
    """Regression: 1920px frames were never downscaled (stride truncated to 1)."""
    from app.services.stream import _downscale

    wide = np.zeros((1080, 1920, 3), dtype=np.uint8)
    scaled = _downscale(wide, 1280)
    assert scaled.shape[1] <= 1280
    assert scaled.shape[1] == 960

    small = np.zeros((720, 640, 3), dtype=np.uint8)
    assert _downscale(small, 1280).shape == small.shape


def test_live_frames_are_only_encoded_while_somebody_watches() -> None:
    """An idle camera must not burn a core encoding JPEGs nobody asked for."""
    reader = FakeReader()
    stream = CameraStream(make_config(), reader_factory=lambda _cfg: reader)
    stream.start()
    try:
        assert wait_for(lambda: stream.health.frames_total > 3)
        assert stream.latest_jpeg(encode_if_missing=False) is None

        # The snapshot button still works without a live view.
        assert stream.latest_jpeg() is not None

        # Once a client polls the live view the decoder encodes again.
        stream.mark_live_view()
        assert wait_for(lambda: stream.latest_jpeg(encode_if_missing=False) is not None)
    finally:
        stream.stop()


def test_reload_without_detection_drops_the_pipeline() -> None:
    readers: list[FakeReader] = []

    def factory(_cfg):
        reader = FakeReader()
        readers.append(reader)
        return reader

    config = make_config(detection_enabled=False)
    pipeline = DetectionPipeline(config.to_pipeline_config(), FakeDetector([[person()]]))
    stream = CameraStream(config, pipeline=pipeline, reader_factory=factory)
    stream.start()
    try:
        assert wait_for(lambda: stream.health.frames_total > 1)
        stream.reload(make_config(detection_enabled=False))
        assert stream.pipeline is None
        assert stream.running
        assert len(readers) >= 2, "the decode loop was restarted with the new config"
    finally:
        stream.stop()


def test_start_refuses_to_run_two_decoders(monkeypatch) -> None:
    """A stop() that could not join in time must not let start() add a decoder."""
    import app.services.stream as stream_module

    monkeypatch.setattr(stream_module, "ORPHAN_JOIN_TIMEOUT_SECONDS", 0.2)

    stream = CameraStream(make_config(), reader_factory=lambda _cfg: FakeReader())
    stream.start()
    assert wait_for(lambda: stream.running)
    stream.stop(timeout=5.0)
    assert stream.running is False

    # A thread that keeps running stands in for a reader stuck in a socket read.
    release = threading.Event()
    stuck = threading.Thread(target=release.wait, daemon=True)
    stuck.start()
    try:
        stream._orphan_threads.append(stuck)
        stream.start()
        assert stream.running is False, "a second decoder must not start"
        assert stream.health.state == "error"
    finally:
        release.set()
        stuck.join(timeout=2.0)

    stream.start()  # the previous decoder is gone, so a new one may run
    assert wait_for(lambda: stream.running)
    stream.stop()

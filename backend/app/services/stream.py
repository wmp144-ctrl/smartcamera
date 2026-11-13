"""RTSP streaming.

Stream management is deliberately **not** delegated to OpenCV: each camera gets
an ``ffmpeg`` subprocess that demuxes RTSP, handles the transport/FPS and emits
raw ``bgr24`` frames on stdout. Reading those frames ourselves gives full
control over reconnection, backpressure, health metrics and the detection
budget. OpenCV is only a *fallback* decoder when ffmpeg is unavailable.
"""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import unquote, urlparse

import numpy as np

from app.core.config import settings
from app.core.timeutil import utcnow
from app.services.annotate import OverlayBox, OverlayZone, draw_overlay
from app.services.geometry import box_center
from app.services.pipeline import DetectionPipeline, PipelineConfig, ZoneSpec
from app.services.storage import to_pil

logger = logging.getLogger(__name__)

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720

#: How long after the last live-view request the encode loop keeps producing
#: JPEGs. Nobody watching (headless/kiosk, closed browser tab) must not burn a
#: core per camera on JPEG encoding.
LIVE_IDLE_GRACE_SECONDS = 3.0

#: JPEG/PNG encoding for stored snapshots is CPU work that used to run on the
#: decode thread, delaying the next frame (and the live view) by ~20-40 ms per
#: object entering the scene. One background worker keeps that work off the
#: video path while preserving the frame order of every camera.
_SNAPSHOT_WRITER = ThreadPoolExecutor(max_workers=2, thread_name_prefix="snapshot-writer")

#: How long ``start()`` waits for a decode thread a previous ``stop()`` could not
#: finish joining (a reader blocked in a socket read returns after ``-rw_timeout``).
ORPHAN_JOIN_TIMEOUT_SECONDS = 10.0


@dataclass(slots=True)
class StreamProbe:
    width: int
    height: int
    fps: float
    codec: str | None = None
    #: Encoded bitrate in kbit/s as reported by the container, when known.
    bitrate_kbps: float | None = None


def ffmpeg_available(binary: str | None = None) -> bool:
    return shutil.which(binary or settings.ffmpeg_bin) is not None


def stream_scheme(url: str) -> str:
    """URL scheme in lower case (``""`` for a plain filesystem path)."""
    scheme, separator, _ = url.partition("://")
    return scheme.lower() if separator else ""


def is_local_source(url: str) -> bool:
    """True for a filesystem path or a ``file://`` URI, i.e. not a live stream."""
    return stream_scheme(url) in {"", "file"}


def normalise_input(url: str) -> str:
    """Translate a URL into something the local ffmpeg build accepts.

    ``file:///C:/clips/a.mp4`` is valid URI syntax, but the Windows ffmpeg
    builds reject it with "No such file or directory"; the native
    ``C:/clips/a.mp4`` path works. Network URLs are returned untouched.
    """
    text = url.strip()
    if not text.lower().startswith("file:"):
        return text
    parsed = urlparse(text)
    if os.name == "nt":
        if parsed.netloc:  # UNC share: file://server/share/clip.mp4
            path = f"//{parsed.netloc}{parsed.path}"
        else:
            path = parsed.path.lstrip("/")
    else:
        path = f"{parsed.netloc}{parsed.path}" if parsed.netloc else parsed.path
    return unquote(path) or text


def input_options(
    url: str, transport: str = "tcp", *, rw_timeout_ms: int | None = 8_000_000
) -> list[str]:
    """Protocol specific flags to place **before** ``-i``.

    ``-rtsp_transport`` is an RTSP demuxer option. Passing it for an HTTP, RTMP
    or file input makes ffmpeg abort with "Option rtsp_transport not found", so
    the flags are derived from the URL scheme instead of being hard-coded.
    Local files get no flags at all (they must not be throttled).
    """
    scheme = stream_scheme(url)
    timeout = ["-rw_timeout", str(rw_timeout_ms)] if rw_timeout_ms else []
    if scheme in {"rtsp", "rtsps"}:
        chosen = transport if transport in {"tcp", "udp"} else "tcp"
        return [
            "-rtsp_transport",
            chosen,
            "-fflags",
            "nobuffer+genpts",
            "-flags",
            "low_delay",
            *timeout,
        ]
    if scheme in {"http", "https", "rtmp", "rtmps", "srt", "udp"}:
        return ["-fflags", "nobuffer+genpts", *timeout]
    return []


def _run_bounded(
    command: list[str], timeout: float
) -> tuple[int, str, str] | None:
    """Run a short-lived subprocess with a **hard** deadline.

    ``subprocess.run(timeout=...)`` is not dependable here: on Windows a child
    blocked inside a network connect keeps its pipes open, so the timeout is
    never enforced and a probe against an unreachable camera would hang a worker
    thread forever. This always kills the process itself and drains the pipes on
    helper threads.

    Returns ``(returncode, stdout, stderr)`` or ``None`` when the call had to be
    abandoned (timed out / could not be started).
    """
    try:
        process = subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        logger.debug("cannot start %s: %s", command[0], exc)
        return None

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []

    def pump(stream, sink: list[bytes]) -> None:  # noqa: ANN001
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                sink.append(chunk)
        except (OSError, ValueError):
            pass

    pumps = [
        threading.Thread(target=pump, args=(process.stdout, stdout_chunks), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, stderr_chunks), daemon=True),
    ]
    for thread in pumps:
        thread.start()

    timed_out = False
    try:
        process.wait(timeout=max(0.5, timeout))
    except subprocess.TimeoutExpired:
        timed_out = True

    if timed_out or process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        for thread in pumps:
            thread.join(timeout=1.0)
        logger.debug("timed out after %.1fs: %s", timeout, " ".join(command[:3]))
        return None

    for thread in pumps:
        thread.join(timeout=2.0)
    return (
        process.returncode,
        b"".join(stdout_chunks).decode("utf-8", "replace"),
        b"".join(stderr_chunks).decode("utf-8", "replace"),
    )


def probe_command(url: str, *, timeout: float = 15.0) -> list[str]:
    """The ffprobe invocation used for a stream URL.

    NOTE: never add ``-nostdin`` here — that is an **ffmpeg-only** option and
    ffprobe rejects it ("Failed to set value ... for option 'nostdin'"), which
    made every probe fail. ``stdin=DEVNULL`` already prevents the stdin hang it
    was meant to fix.

    ``-rw_timeout`` is set from ``timeout`` so ffprobe gives up on a stalled
    socket by itself; ``_run_bounded`` is the second line of defence.
    """
    target = normalise_input(url)
    return [
        settings.ffprobe_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        *input_options(target, "tcp", rw_timeout_ms=max(1, int(timeout * 1000))),
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,codec_name,bit_rate",
        "-of",
        "json",
        target,
    ]


def probe_stream(url: str, *, timeout: float = 15.0) -> StreamProbe | None:
    """Ask ffprobe for the stream geometry. Returns ``None`` when unavailable."""
    if not shutil.which(settings.ffprobe_bin):
        return None
    command = probe_command(url, timeout=timeout)
    completed = _run_bounded(command, timeout)
    if completed is None:
        return None
    returncode, stdout, stderr = completed
    if returncode != 0:
        logger.debug("ffprobe could not read %s: %s", url, stderr.strip()[:200])
        return None
    try:
        payload = json.loads(stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        rate = stream.get("avg_frame_rate") or "0/1"
        numerator, _, denominator = rate.partition("/")
        fps = float(numerator) / float(denominator or 1) if float(denominator or 1) else 0.0
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if width <= 0 or height <= 0:
        # Some RTSP servers only reveal SPS after the first frame; fall back to
        # the default geometry with a scale filter.
        return None
    bitrate: float | None = None
    try:
        raw_bitrate = stream.get("bit_rate")
        if raw_bitrate not in (None, "N/A"):
            bitrate = float(raw_bitrate) / 1000.0
    except (TypeError, ValueError):
        bitrate = None
    return StreamProbe(
        width=width,
        height=height,
        fps=fps,
        codec=stream.get("codec_name"),
        bitrate_kbps=bitrate,
    )


def _read_exact(stream, size: int) -> bytes | None:  # noqa: ANN001
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class FFmpegFrameReader:
    """Reads raw BGR frames from an ffmpeg subprocess."""

    def __init__(
        self,
        url: str,
        *,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        fps: int = 20,
        transport: str = "tcp",
        binary: str | None = None,
        scale: bool = False,
    ) -> None:
        self.url = normalise_input(url)
        self.width = width
        self.height = height
        self.fps = max(1, int(fps))
        self.transport = transport if transport in {"tcp", "udp"} else "tcp"
        self.binary = binary or settings.ffmpeg_bin
        self.scale = scale
        self.frame_bytes = width * height * 3
        self._process: subprocess.Popen | None = None

    def command(self) -> list[str]:
        filters = [f"fps={self.fps}"]
        if self.scale:
            filters.append(f"scale={self.width}:{self.height}")
        filters.append("format=bgr24")
        options = input_options(self.url, self.transport)
        if not stream_scheme(self.url):
            # A local file is decoded far faster than real time; ``-re`` paces it
            # at its native frame rate so a file-backed camera behaves like a
            # live source (and stays in sync with the display FPS).
            options = ["-re", *options]
        return [
            self.binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            *options,
            "-i",
            self.url,
            "-an",
            "-sn",
            "-dn",
            "-vf",
            ",".join(filters),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-",
        ]

    def open(self) -> bool:
        try:
            self._process = subprocess.Popen(  # noqa: S603
                self.command(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self.frame_bytes,
            )
        except (OSError, ValueError) as exc:
            logger.warning("cannot start ffmpeg for %s: %s", self.url, exc)
            self._process = None
            return False
        return True

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def read(self) -> np.ndarray | None:
        if self._process is None or self._process.stdout is None:
            return None
        raw = _read_exact(self._process.stdout, self.frame_bytes)
        if raw is None:
            return None
        return np.frombuffer(raw, dtype=np.uint8).reshape((self.height, self.width, 3))

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdout is not None:
                process.stdout.close()
        except OSError:
            pass
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                process.kill()
            except Exception:  # noqa: BLE001
                pass


class OpenCVFrameReader:
    """Fallback reader used only when ffmpeg is missing."""

    def __init__(self, url: str, *, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT, fps: int = 20, transport: str = "tcp") -> None:
        self.url = normalise_input(url)
        self.width = width
        self.height = height
        self.fps = max(1, int(fps))
        self._capture = None

    def open(self) -> bool:
        try:
            import cv2  # noqa: PLC0415

            capture = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not capture.isOpened():
                return False
            self._capture = capture
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("OpenCV fallback failed for %s: %s", self.url, exc)
            return False

    @property
    def running(self) -> bool:
        return self._capture is not None and self._capture.isOpened()

    def read(self) -> np.ndarray | None:
        if self._capture is None:
            return None
        ok, frame = self._capture.read()
        return frame if ok else None

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


@dataclass
class StreamHealth:
    state: str = "unknown"
    fps: float = 0.0
    detection_fps: float = 0.0
    bitrate_kbps: float = 0.0
    width: int = 0
    height: int = 0
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    dropped_frames: int = 0
    reconnect_count: int = 0
    last_frame_at: datetime | None = None
    message: str | None = None
    frames_total: int = 0
    detections_total: int = 0

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "fps": round(self.fps, 2),
            "detection_fps": round(self.detection_fps, 2),
            "bitrate_kbps": round(self.bitrate_kbps, 1),
            "width": self.width,
            "height": self.height,
            "latency_ms": round(self.latency_ms, 1),
            "jitter_ms": round(self.jitter_ms, 1),
            "dropped_frames": self.dropped_frames,
            "reconnect_count": self.reconnect_count,
            "last_frame_at": self.last_frame_at.isoformat() if self.last_frame_at else None,
            "message": self.message,
        }


@dataclass
class CameraRuntimeConfig:
    """A thread-safe, detached snapshot of a camera (no ORM objects)."""

    id: int
    name: str
    rtsp_url: str
    enabled: bool = True
    transport: str = "tcp"
    fps: int = 20
    detection_fps: float = 5.0
    detection_enabled: bool = False
    motion_sensitivity: float = 0.004
    model_name: str = "rf-detr-base"
    model_weights: str | None = None
    class_config: dict = field(default_factory=dict)
    record_objects: bool = True
    record_snapshots: bool = True
    reconnect_policy: dict = field(default_factory=dict)
    zones: list[ZoneSpec] = field(default_factory=list)
    width: int | None = None
    height: int | None = None

    def to_pipeline_config(self) -> PipelineConfig:
        return PipelineConfig(
            camera_id=self.id,
            camera_name=self.name,
            class_config=dict(self.class_config or {}),
            zones=list(self.zones),
            motion_sensitivity=self.motion_sensitivity,
            require_motion=settings.require_motion_for_detection,
            stationary_after_seconds=settings.stationary_after_seconds,
            track_max_age_seconds=settings.track_max_age_seconds,
            min_track_hits=settings.min_track_hits,
            best_image_improvement=settings.best_image_improvement,
            grid_w=settings.heatmap_grid_w,
            grid_h=settings.heatmap_grid_h,
            record_objects=self.record_objects,
            record_snapshots=self.record_snapshots,
            alert_cooldown_seconds=settings.alert_cooldown_seconds,
        )


class CameraStream:
    """Owns one camera's decode loop and (optionally) its detection pipeline."""

    def __init__(
        self,
        config: CameraRuntimeConfig,
        *,
        pipeline: DetectionPipeline | None = None,
        on_result=None,  # callable(config, PipelineResult) -> None
        on_state_change=None,  # callable(config, str, str | None) -> None
        reader_factory=None,
    ) -> None:
        self.config = config
        self.pipeline = pipeline
        self.on_result = on_result
        self.on_state_change = on_state_change
        self._reader_factory = reader_factory
        self.health = StreamHealth(state="disabled" if not config.enabled else "unknown")
        #: ffprobe costs a full RTSP handshake (up to 15 s against a dead host),
        #: and ``_build_reader`` runs on every reconnect. Probe once per URL.
        self._probe_url: str | None = None
        self._probe: StreamProbe | None = None
        self._thread: threading.Thread | None = None
        #: Threads a previous ``stop()`` could not join in time (a reader can sit
        #: inside a bounded socket read for a few seconds). They are joined by
        #: ``start()`` so the same camera never runs two decoders at once.
        self._orphan_threads: list[threading.Thread] = []
        #: Detection runs on its own thread. Inferring inside the decode loop made
        #: the camera blind for 40-120 ms per detection (the reader cannot pull
        #: frames while torch is busy): visible stutter and dropped frames.
        #: The queue holds **one** frame, so a newer frame replaces a waiting one.
        self._detect_queue: queue.Queue[tuple[np.ndarray, float]] = queue.Queue(maxsize=1)
        self._detect_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_frame: np.ndarray | None = None
        self._last_jpeg: bytes | None = None
        self._overlay_cache: tuple[bytes, float] | None = None
        self._detections: list[OverlayBox] = []
        self._last_detect_at = 0.0
        self._frames_window: list[float] = []
        self._detect_window: list[float] = []
        #: ``time.monotonic()`` of the last live-view poll (0 = nobody watching).
        self._live_plain_at = 0.0
        self._backoff = 1.0
        #: Encoded bitrate from the stream probe, when the container reports it.
        self._source_bitrate_kbps: float | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if not self.config.enabled:
            self._set_state("disabled", None)
            return
        if self._thread is not None and self._thread.is_alive():
            return
        if not self._join_orphans():
            # Starting anyway would run a second ffmpeg/decoder for the same
            # camera (double detection, double writes). Fail loudly instead.
            logger.error(
                "camera %s: previous decode thread is still alive; not starting another",
                self.config.id,
            )
            self._set_state("error", "راه‌اندازی مجدد ناموفق بود؛ دوباره تلاش کنید")
            return
        self._stop.clear()
        self._drain_detections()
        self._thread = threading.Thread(
            target=self._run, name=f"camera-{self.config.id}", daemon=True
        )
        self._thread.start()
        self._detect_thread = threading.Thread(
            target=self._detect_loop, name=f"detect-{self.config.id}", daemon=True
        )
        self._detect_thread.start()

    def stop(self, timeout: float = 6.0) -> None:
        """Signal the decode loop to end and wait for it (bounded).

        The join is time-boxed because a reader blocked inside a socket read
        only returns after its ``-rw_timeout``; when it does not finish in time
        the thread is remembered instead of dropped, so a later ``start()``
        cannot run two decoders for one camera.
        """
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive() and thread not in self._orphan_threads:
                self._orphan_threads.append(thread)
                logger.warning(
                    "camera %s: decode thread has not finished stopping yet", self.config.id
                )
        # The detector thread only *reads* frames, so it can be stopped after the
        # decoder: pipeline state is swapped by ``reload()`` once both are done.
        detect = self._detect_thread
        self._detect_thread = None
        if detect is not None and detect is not threading.current_thread():
            detect.join(timeout)
        self._drain_detections()

    def _join_orphans(self, timeout: float | None = None) -> bool:
        """Wait for threads left over by an earlier ``stop()``. True = no orphans."""
        if timeout is None:
            timeout = ORPHAN_JOIN_TIMEOUT_SECONDS
        deadline = time.monotonic() + timeout
        for thread in list(self._orphan_threads):
            thread.join(max(0.0, deadline - time.monotonic()))
            if not thread.is_alive():
                self._orphan_threads.remove(thread)
        return not self._orphan_threads

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- introspection -----------------------------------------------------
    def _set_state(self, state: str, message: str | None) -> None:
        changed = self.health.state != state
        self.health.state = state
        self.health.message = message
        if changed and self.on_state_change is not None:
            try:
                self.on_state_change(self.config, state, message)
            except Exception:  # noqa: BLE001
                logger.exception("state change callback failed")

    def latest_jpeg(self, *, encode_if_missing: bool = True) -> bytes | None:
        """Newest live JPEG.

        ``encode_if_missing=False`` is the MJPEG/server-push path: it returns
        ``None`` when the decode loop has not produced a frame (nobody is
        watching, so nothing is encoded) instead of encoding inside the API
        request. ``True`` is the on-demand path used by the snapshot button.
        """
        with self._lock:
            cached = self._last_jpeg
            frame = self._last_frame
        if cached is not None:
            return cached
        if not encode_if_missing or frame is None:
            return None
        return self._encode_live(frame)

    def mark_live_view(self) -> None:
        """Tell the stream that a client is watching the raw live view."""
        self._live_plain_at = time.monotonic()

    def latest_frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._last_frame is None else self._last_frame

    def latest_overlay_jpeg(self, max_age: float = 0.25) -> bytes | None:
        with self._lock:
            frame = self._last_frame
            detections = list(self._detections)
            cached = self._overlay_cache
            if cached is not None and time.monotonic() - cached[1] < max_age:
                return cached[0]
        if frame is None:
            return None
        zones = [
            OverlayZone(points=zone.points, color=_zone_color(zone), name=zone.name, kind=zone.kind)
            for zone in self.config.zones
            if zone.enabled
        ]
        image = draw_overlay(to_pil(frame), detections, zones)
        encoded = _encode(image, settings.jpeg_quality)
        with self._lock:
            self._overlay_cache = (encoded, time.monotonic())
        return encoded

    def status(self) -> dict:
        health = self.health.as_dict()
        health["camera_id"] = self.config.id
        health["camera_name"] = self.config.name
        health["detection_enabled"] = self.config.detection_enabled
        health["running"] = self.running
        return health

    def enable_detection(self, enabled: bool) -> None:
        """Turn the detector on/off for this camera without restarting the stream."""
        self.config.detection_enabled = enabled
        if enabled:
            self._last_detect_at = 0.0

    def drop_pipeline(self) -> None:
        """Detach the pipeline and give its (shared) detector back to the registry.

        Without the release the registry reference count only ever grows, so a
        model could never be unloaded and the counts in ``/api/system/runtime``
        became meaningless.
        """
        pipeline = self.pipeline
        self.pipeline = None
        if pipeline is None or pipeline.detector is None:
            return
        from app.services.detector import registry as detector_registry  # noqa: PLC0415

        detector = pipeline.detector
        pipeline.set_detector(None)
        detector_registry.release(detector)

    def reload(self, config: CameraRuntimeConfig) -> None:
        """Apply a configuration change (restarts the thread when needed)."""
        was_running = self.running
        self.stop()
        self.config = config
        if config.detection_enabled:
            # ``_rebuild_pipeline`` acquires first and releases the old detector
            # afterwards, so a shared model is never unloaded and reloaded.
            try:
                self.pipeline = _rebuild_pipeline(self, config)
            except Exception:  # noqa: BLE001 - a config save must not fail on the AI stack
                logger.exception(
                    "could not rebuild the detector for camera %s; detection stays off",
                    config.id,
                )
                self.pipeline = None
        else:
            # Detection is off for this camera: holding the model would keep VRAM
            # busy for a camera that is not allowed to detect anything.
            self.drop_pipeline()
        # Keep the counters (frames, detections, reconnects, drops): a settings
        # save must not make the health panel look like a fresh boot. Only the
        # volatile values are reset.
        self.health.state = "disabled" if not config.enabled else "unknown"
        self.health.message = None
        self.health.fps = 0.0
        self.health.detection_fps = 0.0
        self._backoff = 1.0
        if was_running or config.enabled:
            self.start()

    # -- the loop ----------------------------------------------------------
    def _build_reader(self):  # noqa: ANN201
        if self._reader_factory is not None:
            return self._reader_factory(self.config)
        if self._probe_url != self.config.rtsp_url:
            self._probe = probe_stream(self.config.rtsp_url)
            self._probe_url = self.config.rtsp_url
        probe = self._probe
        width = self.config.width or (probe.width if probe else None) or DEFAULT_WIDTH
        height = self.config.height or (probe.height if probe else None) or DEFAULT_HEIGHT
        scale = probe is None or bool(self.config.width and self.config.width != probe.width)
        # Prefer the container's own bitrate: measuring the decoded frame size
        # instead would report tens of Mbps for any camera, which is not what a
        # network health panel should show.
        self._source_bitrate_kbps = probe.bitrate_kbps if probe else None
        if ffmpeg_available():
            return FFmpegFrameReader(
                self.config.rtsp_url,
                width=width,
                height=height,
                fps=self.config.fps,
                transport=self.config.transport,
                scale=scale,
            )
        return OpenCVFrameReader(
            self.config.rtsp_url, width=width, height=height, fps=self.config.fps
        )

    def _run(self) -> None:
        policy = self.config.reconnect_policy or {}
        backoff_initial = float(policy.get("backoff_initial", 1.0) or 1.0)
        backoff_max = float(policy.get("backoff_max", 30.0) or 30.0)
        backoff_factor = float(policy.get("backoff_factor", 2.0) or 2.0)
        max_retries = int(policy.get("max_retries", 0) or 0)

        attempts = 0
        while not self._stop.is_set():
            reader = self._build_reader()
            local_file = is_local_source(self.config.rtsp_url)
            spin = round(time.perf_counter(), 3)
            if not reader.open():
                attempts += 1
                self.health.dropped_frames += 1
                if local_file:
                    # Retrying a path that does not exist never succeeds; say so
                    # instead of showing the camera as "reconnecting" forever.
                    self._set_state("error", "فایل ویدیویی باز نشد")
                    return
                self._set_state("reconnecting", "اتصال به دوربین برقرار نشد")
                if max_retries and attempts > max_retries:
                    self._set_state("error", "تعداد تلاش‌های اتصال به پایان رسید")
                    return
                if self._stop.wait(self._backoff):
                    break
                self._backoff = min(self._backoff * backoff_factor, backoff_max)
                continue

            self._backoff = backoff_initial
            self.health.reconnect_count += 1
            self._set_state("online", None)
            consecutive_failures = 0

            while not self._stop.is_set():
                loop_start = time.perf_counter()
                frame = reader.read()
                read_done = time.perf_counter()
                if frame is None:
                    consecutive_failures += 1
                    self.health.dropped_frames += 1
                    if consecutive_failures >= 5:
                        break
                    continue
                consecutive_failures = 0

                height, width = frame.shape[:2]
                self.health.width = int(width)
                self.health.height = int(height)
                self.health.last_frame_at = utcnow()
                self.health.frames_total += 1
                self.health.latency_ms = (read_done - loop_start) * 1000.0
                self._record_rate(self._frames_window, read_done)
                self.health.fps = _rate(self._frames_window)

                expect_interval = 1.0 / max(1, self.config.fps)
                drift = (read_done - loop_start) - expect_interval
                self.health.jitter_ms = max(0.0, drift * 1000.0)
                if self._source_bitrate_kbps:
                    self.health.bitrate_kbps = self._source_bitrate_kbps
                else:
                    # No container metadata (common for live RTSP): report the
                    # decoded throughput as a rough upper bound.
                    frame_bytes = frame.nbytes
                    self.health.bitrate_kbps = (
                        frame_bytes * 8.0 / 1000.0 * max(0.1, self.health.fps)
                    )

                self._publish_frame(frame)

                if self._should_detect(read_done):
                    self._submit_detection(frame, read_done)

                elapsed = time.perf_counter() - loop_start
                sleep_for = expect_interval - elapsed
                if sleep_for > 0 and self._stop.wait(sleep_for):
                    break

                if not reader.running:
                    break

            reader.close()
            if self._stop.is_set():
                break
            if local_file:
                # A local file is not a live camera: replaying it would invent
                # events that never happened. Play the clip once and stop.
                self._set_state("offline", "پایان فایل ویدیویی")
                return
            self._set_state("reconnecting", "قطع ارتباط؛ تلاش مجدد")
            attempts += 1
            if max_retries and attempts > max_retries:
                self._set_state("error", "تعداد تلاش‌های اتصال به پایان رسید")
                return
            if self._stop.wait(self._backoff):
                break
            self._backoff = min(self._backoff * backoff_factor, backoff_max)

        self._set_state("offline", "متوقف شده")

    def _encode_live(self, frame: np.ndarray) -> bytes | None:
        """Encode one live-view JPEG (never raises: the API path stays honest)."""
        try:
            image = to_pil(_downscale(frame, settings.live_max_width))
            return _encode(image, settings.jpeg_quality)
        except Exception:  # noqa: BLE001 - a bad frame must not break the stream
            logger.debug("live JPEG encoding failed for camera %s", self.config.id, exc_info=True)
            return None

    def _publish_frame(self, frame: np.ndarray) -> None:
        # Only encode while somebody is actually watching the raw live view:
        # a full-frame JPEG costs ~9 ms, which is 18% of a core per camera at
        # 20 fps — pure waste for a headless or closed browser tab.
        watching = (time.monotonic() - self._live_plain_at) < LIVE_IDLE_GRACE_SECONDS
        encoded = self._encode_live(frame) if watching else None
        with self._lock:
            self._last_frame = frame
            self._last_jpeg = encoded
            self._overlay_cache = None

    def _should_detect(self, now: float) -> bool:
        if not self.config.detection_enabled or self.pipeline is None:
            return False
        interval = 1.0 / max(0.1, self.config.detection_fps)
        if now - self._last_detect_at < interval:
            return False
        self._last_detect_at = now
        return True

    def _submit_detection(self, frame: np.ndarray, now: float) -> None:
        """Queue a frame for the detection thread, replacing any waiting frame.

        Detection samples the stream (5 fps by default), so analysing the newest
        frame is always preferable to working through a backlog of old ones.
        """
        try:
            self._detect_queue.put_nowait((frame, now))
        except queue.Full:
            try:
                self._detect_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._detect_queue.put_nowait((frame, now))
            except queue.Full:  # pragma: no cover - extreme scheduling luck
                pass

    def _drain_detections(self) -> None:
        while True:
            try:
                self._detect_queue.get_nowait()
            except queue.Empty:
                return

    def _detect_loop(self) -> None:
        while not self._stop.is_set():
            try:
                frame, now = self._detect_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._run_detection(frame, now)
            except Exception:  # noqa: BLE001 - never let the worker die
                logger.exception("detection loop failed for camera %s", self.config.id)

    def _run_detection(self, frame: np.ndarray, now: float) -> None:
        pipeline = self.pipeline
        if pipeline is None:
            return
        try:
            result = pipeline.process(frame, ts=utcnow(), now=now)
        except Exception:  # noqa: BLE001 - detection must never kill the stream
            logger.exception("detection failed for camera %s", self.config.id)
            return

        self.health.detections_total += len(result.detections)
        self._record_rate(self._detect_window, now)
        self.health.detection_fps = _rate(self._detect_window)

        boxes: list[OverlayBox] = []
        for track in result.active_tracks:
            boxes.append(
                OverlayBox(
                    bbox=track.bbox_last,
                    label=track.object_type,
                    confidence=track.confidence,
                    track_id=track.runtime_track_id,
                    motion_state=track.motion_state,
                    direction=track.direction,
                )
            )
        with self._lock:
            self._detections = boxes

        callback = self.on_result
        deliver = callback is not None and bool(
            result.events or result.snapshots or result.active_tracks
        )

        if result.snapshots:
            # Encoding and writing the images is offloaded to a single worker so
            # the decoder thread continues with the next frame. The result is
            # forwarded from that worker to keep the image and its metadata in
            # one order for the persistence task.
            pipeline_config = pipeline.config
            camera = self.config

            def write_and_forward() -> None:
                from app.services.persistence import materialize_snapshots  # noqa: PLC0415

                materialize_snapshots(pipeline_config, result)
                if deliver:
                    try:
                        callback(camera, result)  # type: ignore[misc]
                    except Exception:  # noqa: BLE001
                        logger.exception("result callback failed for camera %s", camera.id)

            _SNAPSHOT_WRITER.submit(write_and_forward)
            return

        if deliver:
            try:
                callback(self.config, result)  # type: ignore[misc]
            except Exception:  # noqa: BLE001
                logger.exception("result callback failed for camera %s", self.config.id)

    @staticmethod
    def _record_rate(window: list[float], now: float) -> None:
        window.append(now)
        cutoff = now - 5.0
        while window and window[0] < cutoff:
            window.pop(0)


def _rate(window: list[float]) -> float:
    if len(window) < 2:
        return 0.0
    span = window[-1] - window[0]
    if span <= 0:
        return 0.0
    return (len(window) - 1) / span


def _downscale(frame: np.ndarray, max_width: int) -> np.ndarray:
    """Stride-subsample a frame so the live view is never wider than ``max_width``.

    The stride is rounded **up**: with a truncating division a 1920px camera
    with the default 1280 limit produced a stride of 1, i.e. no downscale at
    all, and every live frame was encoded at 1080p.
    """
    if max_width <= 0:
        return frame
    height, width = frame.shape[:2]
    if width <= max_width:
        return frame
    step = max(2, math.ceil(width / max_width))
    return frame[::step, ::step]


def _encode(image, quality: int) -> bytes:
    import io  # noqa: PLC0415

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=False)
    return buffer.getvalue()


def _zone_color(zone: ZoneSpec) -> str:
    colors = {
        "region": "#22d3ee",
        "ignore": "#ef4444",
        "alert": "#f59e0b",
        "line": "#a855f7",
    }
    return colors.get(zone.kind, "#22d3ee")


def _rebuild_pipeline(stream: CameraStream, config: CameraRuntimeConfig) -> DetectionPipeline:
    from app.services.detector import registry as detector_registry  # noqa: PLC0415

    detector = detector_registry.acquire(
        config.model_name, weights_path=config.model_weights, device=settings.device
    )
    pipeline = stream.pipeline
    if pipeline is None:
        return DetectionPipeline(config.to_pipeline_config(), detector)
    previous = pipeline.detector
    pipeline.config = config.to_pipeline_config()
    pipeline.set_detector(detector)
    pipeline.rebuild_rules()
    if previous is not None and previous is not detector:
        # Swapped models: hand the old one back once the new one is in place.
        detector_registry.release(previous)
    return pipeline


def box_center_of(bbox: list[float]) -> tuple[float, float]:
    return box_center(bbox)

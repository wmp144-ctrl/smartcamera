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
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import unquote, urlparse

import numpy as np

from app.core.config import settings
from app.core.timeutil import utcnow
from app.services.annotate import OverlayBox, OverlayZone, draw_overlay
from app.services.geometry import box_center
from app.services.imaging import fit_width
from app.services.inference import scheduler
from app.services.persistence import snapshot_writer
from app.services.pipeline import DetectionPipeline, PipelineConfig, ZoneSpec
from app.services.storage import to_pil

logger = logging.getLogger(__name__)

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720

#: How long after the last live-view request the encode loop keeps producing
#: JPEGs. Nobody watching (headless/kiosk, closed browser tab) must not burn a
#: core per camera on JPEG encoding.
LIVE_IDLE_GRACE_SECONDS = 3.0

#: Snapshots are written by ``app.services.persistence.snapshot_writer()``: a
#: bounded pool of workers with its own queue, so a burst of objects can never
#: turn into an unbounded backlog of encoded frames in RAM.

#: How long ``start()`` waits for a decode thread a previous ``stop()`` could not
#: finish joining (a reader blocked in a socket read returns after its timeout).
ORPHAN_JOIN_TIMEOUT_SECONDS = 10.0

#: How long ``stop()`` waits for an inference job already inside the pipeline.
#: The job is a few hundred milliseconds at worst; the bound only exists so a
#: wedged model cannot block a camera restart forever.
INFERENCE_DRAIN_TIMEOUT_SECONDS = 5.0

#: Window used to average the detection rate. Longer than the stream window
#: because a camera can legitimately detect only a few frames per minute.
DETECT_RATE_WINDOW_SECONDS = 15.0


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
    url: str, transport: str = "tcp", *, timeout_us: int | None = 8_000_000
) -> list[str]:
    """Protocol specific flags to place **before** ``-i``.

    ``-rtsp_transport`` is an RTSP demuxer option. Passing it for an HTTP, RTMP
    or file input makes ffmpeg abort with "Option rtsp_transport not found", so
    the flags are derived from the URL scheme instead of being hard-coded.
    Local files get no flags at all (they must not be throttled).

    RTSP gets ``-timeout`` (the socket I/O timeout of the RTSP demuxer), **not**
    ``-rw_timeout``: the latter is an AVIO option and this build aborts with
    "Option rw_timeout not found. Error opening input file" before a single
    packet is read, which turned every RTSP camera into an endless reconnect
    loop. Values are microseconds.
    """
    scheme = stream_scheme(url)
    if scheme in {"rtsp", "rtsps"}:
        chosen = transport if transport in {"tcp", "udp"} else "tcp"
        return [
            "-rtsp_transport",
            chosen,
            "-fflags",
            "nobuffer+genpts",
            "-flags",
            "low_delay",
            *(["-timeout", str(int(timeout_us))] if timeout_us else []),
        ]
    timeout = ["-rw_timeout", str(int(timeout_us))] if timeout_us else []
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

    ``-timeout`` / ``-rw_timeout`` is set from ``timeout`` so ffprobe gives up
    on a stalled socket by itself; ``_run_bounded`` is the second line of
    defence. The value is in **microseconds** — passing milliseconds made every
    probe give up after a few milliseconds.
    """
    target = normalise_input(url)
    return [
        settings.ffprobe_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        *input_options(target, "tcp", timeout_us=max(1, int(timeout * 1_000_000))),
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


def _read_exact(stream, size: int) -> bytearray | None:  # noqa: ANN001
    """Read exactly ``size`` bytes into a **writable** buffer.

    ``np.frombuffer`` on a ``bytes`` object returns a read-only array, and
    RF-DETR runs ``torch.from_numpy(image.transpose((2, 0, 1)))`` on the frame it
    is given, so every detection printed the long "NumPy array is not writable"
    UserWarning into the console. A ``bytearray`` keeps the same zero-copy view
    but lets PyTorch own a writable tensor.
    """
    buffer = bytearray()
    while len(buffer) < size:
        chunk = stream.read(size - len(buffer))
        if not chunk:
            return None
        buffer.extend(chunk)
    return buffer


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
        # ``stderr`` used to be DEVNULL, so a reader that could not start (a bad
        # option, a refused RTSP handshake, a missing codec) died silently and
        # the camera just showed "reconnecting" forever. Keep the last lines and
        # report them once when ffmpeg exits by itself.
        self._stderr_tail: deque[str] = deque(maxlen=20)
        self._stderr_thread: threading.Thread | None = None
        self._exit_reported = False
        self._closing = False

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
        self._closing = False
        self._exit_reported = False
        self._stderr_tail.clear()
        try:
            self._process = subprocess.Popen(  # noqa: S603
                self.command(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=self.frame_bytes,
            )
        except (OSError, ValueError) as exc:
            logger.warning("cannot start ffmpeg for %s: %s", self.url, exc)
            self._process = None
            return False
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, args=(self._process.stderr,), daemon=True
        )
        self._stderr_thread.start()
        return True

    def _drain_stderr(self, stream) -> None:  # noqa: ANN001
        """Keep ffmpeg's error output drained and remember the tail.

        Not draining a pipe deadlocks ffmpeg once the buffer is full.
        """
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._stderr_tail.append(line)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _report_exit(self) -> None:
        """Log why ffmpeg stopped, once per reader instance.

        The pipe reaches EOF a moment before the process is reaped, so wait
        briefly for the exit code: without it the report was skipped and a
        camera that kept reconnecting left no explanation in the log at all.
        """
        if self._exit_reported or self._closing:
            return
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                return
        self._exit_reported = True
        detail = " | ".join(self._stderr_tail) or "بدون خروجی خطا"
        logger.warning(
            "ffmpeg stopped for %s (exit=%s): %s", self.url, process.returncode, detail[:500]
        )

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def read(self) -> np.ndarray | None:
        if self._process is None or self._process.stdout is None:
            return None
        try:
            raw = _read_exact(self._process.stdout, self.frame_bytes)
        except (ValueError, OSError):
            # ``close()`` runs on another thread to unblock this read; a closed
            # pipe raises instead of returning EOF on Windows.
            return None
        if raw is None:
            self._report_exit()
            return None
        return np.frombuffer(raw, dtype=np.uint8).reshape((self.height, self.width, 3))

    def close(self) -> None:
        self._closing = True
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
    #: failed connection attempts (a camera that never opened); kept apart from
    #: ``dropped_frames`` so the health chart does not call a network outage a
    #: decode problem.
    connect_failures: int = 0
    reconnect_count: int = 0
    last_frame_at: datetime | None = None
    message: str | None = None
    frames_total: int = 0
    detections_total: int = 0
    # --- processing telemetry ---------------------------------------------
    #: Frames replaced in the camera's inference slot before they were analysed
    #: (the newest frame always wins, so this is "skipped", not "lost").
    detection_dropped: int = 0
    #: Time waiting for the shared inference thread (last frame).
    queue_wait_ms: float = 0.0
    #: Motion + detection + tracking time for the last analysed frame.
    inference_ms: float = 0.0
    #: Stored snapshots dropped because the writer queue was full.
    snapshot_dropped: int = 0
    #: JPEGs encoded for live viewers (proves the encode follows the viewer rate)
    live_encoded: int = 0

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
            "connect_failures": self.connect_failures,
            "reconnect_count": self.reconnect_count,
            "last_frame_at": self.last_frame_at.isoformat() if self.last_frame_at else None,
            "message": self.message,
            "detection_dropped": self.detection_dropped,
            "queue_wait_ms": round(self.queue_wait_ms, 1),
            "inference_ms": round(self.inference_ms, 1),
            "snapshot_dropped": self.snapshot_dropped,
            "live_encoded": self.live_encoded,
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
            detection_max_width=settings.detection_max_width,
            stationary_after_seconds=settings.stationary_after_seconds,
            track_max_age_seconds=settings.track_max_age_seconds,
            min_track_hits=settings.min_track_hits,
            best_image_improvement=settings.best_image_improvement,
            grid_w=settings.heatmap_grid_w,
            grid_h=settings.heatmap_grid_h,
            record_objects=self.record_objects,
            record_snapshots=self.record_snapshots,
            alert_cooldown_seconds=settings.alert_cooldown_seconds,
            track_high_threshold=settings.track_high_threshold,
            track_low_threshold=settings.track_low_threshold,
            track_appearance_weight=settings.track_appearance_weight,
            track_camera_motion=settings.track_camera_motion,
            track_path_interval_seconds=settings.track_path_interval_seconds,
            track_path_max_points=settings.track_path_max_points,
            track_frame_interval_seconds=settings.track_frame_interval_seconds,
            max_track_frames=settings.max_track_frames,
        )


class LiveEncoder:
    """One background thread per camera that encodes the live-view JPEG.

    Encoding used to run inline in the RTSP decode thread (~9 ms for a 1080p
    frame, measured). At 20 fps that is nearly a fifth of the loop spent in
    JPEG, so the next ``read()`` was late, the frame pipe filled and the camera
    looked like it was stuttering. The decoder now hands the newest frame over
    and keeps reading; a slow encoder simply drops the frame it has not started
    (the pending slot holds exactly one), so memory stays bounded.
    """

    def __init__(self, encode, sink) -> None:  # noqa: ANN001 - callables
        self._encode = encode
        self._sink = sink
        self._lock = threading.Lock()
        self._pending: np.ndarray | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def submit(self, frame: np.ndarray) -> None:
        with self._lock:
            self._pending = frame
        self._ensure_thread()
        self._wake.set()

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="live-encoder", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.5)
            self._wake.clear()
            with self._lock:
                frame = self._pending
                self._pending = None
            if frame is None:
                continue
            encoded = self._encode(frame)
            if encoded is not None:
                self._sink(encoded)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        with self._lock:
            self._pending = None


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
        #: Frames go to the process-wide inference scheduler, which owns the
        #: only detector thread (see ``app.services.inference``). Nothing here
        #: blocks on the model: the decode loop must keep pulling frames.
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_frame: np.ndarray | None = None
        self._last_jpeg: bytes | None = None
        #: ``time.monotonic()`` of the last encoded live JPEG (0 = none yet)
        self._last_jpeg_at = 0.0
        #: cadence gate for the encoder thread (last frame *handed over*),
        #: separate from ``_last_jpeg_at`` so a drop cannot stretch the cadence
        self._next_live_at = 0.0
        #: JPEG encoding happens off the decode thread (see ``LiveEncoder``).
        #: The lambda resolves ``_encode_live`` on every frame so a subclass or
        #: a test can replace it without rebuilding the stream.
        self._encoder = LiveEncoder(lambda frame: self._encode_live(frame), self._store_live_jpeg)
        self._overlay_cache: tuple[bytes, float] | None = None
        self._detections: list[OverlayBox] = []
        self._last_detect_at = 0.0
        self._frames_window: list[float] = []
        self._prev_read_at: float | None = None
        self._detect_window: list[float] = []
        #: ``time.monotonic()`` of the last live-view poll (0 = nobody watching).
        self._live_plain_at = 0.0
        #: JPEG rate the watching client asked for (0 = use ``live_max_fps``).
        self._live_fps = 0.0
        #: reader currently in use, so ``stop()`` can close its pipe and unblock
        #: a decode thread sitting inside a socket read.
        self._reader = None
        #: successful sessions since ``start()`` (the first one is not a reconnect)
        self._sessions = 0
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
        scheduler.register(self.config.id)
        self._thread = threading.Thread(
            target=self._run, name=f"camera-{self.config.id}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 6.0) -> None:
        """Signal the decode loop to end and wait for it (bounded).

        The join is time-boxed because a reader blocked inside a socket read
        only returns after its socket timeout; when it does not finish in time
        the thread is remembered instead of dropped, so a later ``start()``
        cannot run two decoders for one camera.
        """
        self._stop.set()
        thread = self._thread
        self._thread = None
        reader = self._reader
        if (
            reader is not None
            and thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            # A decode thread blocked inside ``reader.read()`` only notices the
            # stop flag after the socket timeout (up to 8 s for RTSP). Closing
            # the pipe makes that read return immediately, so a camera stops in
            # milliseconds and a restart cannot race a second decoder.
            try:
                reader.close()
            except Exception:  # noqa: BLE001 - teardown must never raise
                logger.debug("camera %s: closing the reader failed", self.config.id, exc_info=True)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive() and thread not in self._orphan_threads:
                self._orphan_threads.append(thread)
                logger.warning(
                    "camera %s: decode thread has not finished stopping yet", self.config.id
                )
        # Hand the camera's slot back first: queued frames are dropped instead of
        # run, so a stopped camera cannot start new work.
        scheduler.unregister(self.config.id)
        # ...then wait for the job that is *already* inside the pipeline. Without
        # this the caller released the detector right after stop(), and the shared
        # model was closed underneath the running ``pipeline.process`` — which is
        # how a Save or Delete produced a burst of detection errors.
        if self.pipeline is not None and not scheduler.wait_camera_idle(
            self.config.id, INFERENCE_DRAIN_TIMEOUT_SECONDS
        ):
            logger.warning(
                "camera %s: inference still running after %.0fs; releasing anyway",
                self.config.id,
                INFERENCE_DRAIN_TIMEOUT_SECONDS,
            )
        self._encoder.stop()

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

    def latest_jpeg(
        self, *, encode_if_missing: bool = True, max_age: float | None = None
    ) -> bytes | None:
        """Newest live JPEG.

        ``encode_if_missing=False`` is the MJPEG/server-push path: it returns
        ``None`` when the decode loop has not produced a frame (nobody is
        watching, so nothing is encoded) instead of encoding inside the API
        request. ``True`` is the on-demand path used by the snapshot button.

        A cached frame older than ``max_age`` is **not** returned: when the
        decoder stops (camera unplugged, ffmpeg died) the last JPEG used to be
        replayed forever, so the UI showed a frozen "live" picture instead of
        the no-signal placeholder.
        """
        limit = LIVE_IDLE_GRACE_SECONDS if max_age is None else max_age
        now = time.monotonic()
        with self._lock:
            cached = self._last_jpeg
            cached_at = self._last_jpeg_at
            frame = self._last_frame
        if cached is not None and (now - cached_at) <= limit:
            return cached
        if not encode_if_missing or frame is None:
            return None
        return self._encode_live(frame)

    def mark_live_view(self, fps: float | None = None) -> None:
        """Tell the stream that a client is watching the raw live view.

        ``fps`` is the rate that client actually consumes. Encoding at the full
        stream rate and then throttling only the HTTP delivery meant a 3 fps
        grid tile still cost 20 JPEG encodes per second on the decode thread;
        the decoder now encodes at the viewer's cadence.
        """
        self._live_plain_at = time.monotonic()
        if fps:
            self._live_fps = max(1.0, min(float(fps), float(max(1, self.config.fps)) * 1.5))

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
            if not reader.open():
                attempts += 1
                self.health.connect_failures += 1
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

            # NOTE: the backoff is *not* reset here. A server that accepts the
            # RTSP session but has no stream (publisher gone) would reset it on
            # every attempt and the retry cadence would stay at 1 s forever —
            # 246 reconnects in a five minute outage, measured. It is reset
            # below, once a session actually produced frames.
            self._prev_read_at = None
            self._sessions += 1
            if self._sessions > 1:
                # The first session is not a reconnect; counting it made a
                # freshly booted camera show "reconnect 1" in the health panel.
                self.health.reconnect_count += 1
            self._reader = reader
            self._set_state("online", None)
            consecutive_failures = 0
            # A camera whose server accepts the RTSP session but has no stream
            # yet (the publisher is gone) opens *and* closes immediately. That
            # used to loop once per second forever: 21 reconnects in 22 seconds
            # of an outage, each one a full handshake. A session that delivered
            # no frame is treated as a failed connect so the backoff applies.
            session_frames = 0

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
                session_frames += 1

                height, width = frame.shape[:2]
                self.health.width = int(width)
                self.health.height = int(height)
                self.health.last_frame_at = utcnow()
                self.health.frames_total += 1
                self.health.latency_ms = (read_done - loop_start) * 1000.0
                self._record_rate(self._frames_window, read_done)
                # A reconnect (or the first seconds of a buffered RTSP session)
                # delivers a burst of frames, and the 5 s window then reported
                # 166 fps for a 20 fps camera - which also blew up the bitrate
                # estimate and the health charts. Cap it at what the reader can
                # physically deliver.
                self.health.fps = min(_rate(self._frames_window), self.config.fps * 1.5)

                expect_interval = 1.0 / max(1, self.config.fps)
                if self._prev_read_at is not None:
                    # Jitter is how late a frame arrived against the camera's
                    # cadence. It used to compare the *read duration* with the
                    # interval, which is always ~2 ms, so the panel always
                    # showed 0.0 ms even when frames were visibly stuttering.
                    late_by = (read_done - self._prev_read_at) - expect_interval
                    self.health.jitter_ms = max(0.0, late_by * 1000.0)
                self._prev_read_at = read_done
                if self._source_bitrate_kbps:
                    self.health.bitrate_kbps = self._source_bitrate_kbps
                else:
                    # A live RTSP session rarely advertises an encoded bitrate,
                    # and the decoded frame size is *not* a substitute: 720p at
                    # 20 fps of raw BGR is ~350 Mbps, i.e. roughly 100x the real
                    # stream, which the dashboard then showed as "355 Mbps".
                    # Report "unknown" instead of a number that is wrong by two
                    # orders of magnitude.
                    self.health.bitrate_kbps = 0.0

                self._publish_frame(frame)

                if self._should_detect(read_done):
                    self._submit_detection(frame, read_done)

                # No extra sleep here on purpose. ffmpeg already paces the
                # output to ``-vf fps=<config.fps>`` in real time, so
                # sleeping again made the consumer slower than the producer
                # (~16 fps for a 20 fps stream), filled the frame pipe and
                # stalled the RTSP socket - which is what made MediaMTX drop
                # the session every minute or so.
                if not reader.running:
                    break

            self._reader = None
            reader.close()
            if self._stop.is_set():
                break
            if session_frames == 0:
                attempts += 1
                # The session opened but never produced a picture (publisher
                # gone, wrong stream path): from the operator's point of view
                # that is a failed connection, so it is counted as one.
                self.health.connect_failures += 1
                self._set_state("reconnecting", "جریان تصویری روی این آدرس در دسترس نیست")
                if max_retries and attempts > max_retries:
                    self._set_state("error", "تعداد تلاش‌های اتصال به پایان رسید")
                    return
                if self._stop.wait(self._backoff):
                    break
                self._backoff = min(self._backoff * backoff_factor, backoff_max)
                continue
            if local_file:
                # A local file is not a live camera: replaying it would invent
                # events that never happened. Play the clip once and stop.
                self._set_state("offline", "پایان فایل ویدیویی")
                return
            # A session that delivered frames is a healthy connection: the next
            # failure starts from the initial backoff again.
            self._backoff = backoff_initial
            attempts = 0
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
        # Only encode while somebody is actually watching the raw live view, and
        # only at the rate that viewer asked for: a full-frame JPEG costs ~9 ms,
        # so encoding 20 of them per second for a 5 fps tile is pure waste.
        now = time.monotonic()
        watching = (now - self._live_plain_at) < LIVE_IDLE_GRACE_SECONDS
        with self._lock:
            self._last_frame = frame
        if not watching:
            return
        fps = self._live_fps or float(settings.live_max_fps or self.config.fps)
        interval = 1.0 / max(1.0, fps)
        if now < self._next_live_at:
            return
        self._next_live_at = now + interval
        # The encode itself runs on the camera's encoder thread, never here: the
        # decode thread must get back to ``reader.read()`` immediately.
        self._encoder.submit(frame)

    def _store_live_jpeg(self, encoded: bytes) -> None:
        """Publish an encoded live JPEG (called from the encoder thread)."""
        with self._lock:
            self._last_jpeg = encoded
            self._last_jpeg_at = time.monotonic()
            self._overlay_cache = None
            self.health.live_encoded += 1

    def _should_detect(self, now: float) -> bool:
        if not self.config.detection_enabled or self.pipeline is None:
            return False
        interval = 1.0 / max(0.1, self.config.detection_fps)
        if now - self._last_detect_at < interval:
            return False
        self._last_detect_at = now
        return True

    def _submit_detection(self, frame: np.ndarray, now: float) -> None:
        """Hand a frame to the shared inference scheduler.

        Detection samples the stream (5 fps by default), so the scheduler's
        per-camera slot holds one frame and a newer one replaces it: analysis
        always sees the newest frame instead of working through a backlog.
        """

        def job() -> None:
            self._run_detection(frame, now)

        if not scheduler.submit(self.config.id, job):
            self.health.detection_dropped += 1

    def _run_detection(self, frame: np.ndarray, now: float) -> None:
        pipeline = self.pipeline
        if pipeline is None:
            return
        started = time.perf_counter()
        try:
            result = pipeline.process(frame, ts=utcnow(), now=now)
        except Exception:  # noqa: BLE001 - detection must never kill the stream
            logger.exception("detection failed for camera %s", self.config.id)
            return
        self.health.inference_ms = (time.perf_counter() - started) * 1000.0
        self.health.queue_wait_ms = scheduler.wait_ms(self.config.id)

        self.health.detections_total += len(result.detections)
        # Measure the detection rate when the frame was *analysed*, not when it
        # was submitted: with a busy scheduler the queued timestamps arrive out
        # of order and ``_rate`` then reported 0 fps for a working camera.
        # A 15 s window (not the 5 s used for the stream) keeps the number
        # meaningful when the GPU can only analyse ~0.2 fps per camera: in a 5 s
        # window that is a single sample, and ``_rate`` then printed 0.0 fps for
        # a camera whose detection was working perfectly (measured on 8 cameras).
        self._record_rate(self._detect_window, time.perf_counter(), span=DETECT_RATE_WINDOW_SECONDS)
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

            if not snapshot_writer().submit(write_and_forward):
                # The disk is behind: keep the metadata, drop the pixels, and
                # say so instead of slowing the whole pipeline down.
                self.health.snapshot_dropped += len(result.snapshots)
                if deliver:
                    try:
                        callback(self.config, result)  # type: ignore[misc]
                    except Exception:  # noqa: BLE001
                        logger.exception("result callback failed for camera %s", self.config.id)
            return

        if deliver:
            try:
                callback(self.config, result)  # type: ignore[misc]
            except Exception:  # noqa: BLE001
                logger.exception("result callback failed for camera %s", self.config.id)

    @staticmethod
    def _record_rate(window: list[float], now: float, span: float = 5.0) -> None:
        window.append(now)
        cutoff = now - span
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
    """Fit a frame into ``max_width`` px, keeping the aspect ratio.

    Stride-subsampling (``frame[::step, ::step]``) turned a 1920 px camera into
    960 px for a 1280 limit and aliased thin structures away; ``fit_width``
    resizes to the requested width with area averaging instead.
    """
    return fit_width(frame, max_width)


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
    if previous is not None:
        if previous is detector:
            # ``acquire`` took a second reference on the model this camera already
            # used; give it straight back. Without this every settings save leaked
            # one reference and the model could never be unloaded (reproduced:
            # refs=3 after three rebuilds).
            detector_registry.release(detector)
        else:
            # Swapped models: hand the old one back once the new one is in place.
            detector_registry.release(previous)
    return pipeline


def box_center_of(bbox: list[float]) -> tuple[float, float]:
    return box_center(bbox)

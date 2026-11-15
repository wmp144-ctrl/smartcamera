"""Camera manager: owns the streams, the persistence consumer and the telemetry.

Threading model
---------------

::

    video thread (per camera)          asyncio task (one)
    ---------------------------        ----------------------------
    decode -> detect -> track   --->   bounded queue  --->  persist to DB
    encode live JPEG                   (result_queue)      flush heatmap
    update health metrics                                  publish to the bus

The video threads never touch the database. Everything that needs a session is
handled by :meth:`CameraManager._consume_loop`, which keeps the video loop
timing-stable even when SQLite is busy.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import db_write_lock, session_scope
from app.core.timeutil import utcnow
from app.models.camera import Camera
from app.models.system import HealthMetric
from app.schemas.detection import EventRead
from app.schemas.system import AlertRead
from app.services.bus import (
    TOPIC_ALERT,
    TOPIC_DETECTION,
    TOPIC_EVENT,
    TOPIC_STATUS,
    bus,
    event_payload,
)
from app.services.detector import registry as detector_registry
from app.services.heatmap import HeatmapBuffer, flush_buffer
from app.services.inference import scheduler
from app.services.models_registry import detection_capacity, speed_factor, suggest_model
from app.services.performance import apply_profile, load_profile
from app.services.persistence import PersistOutcome, Persister, snapshot_writer
from app.services.pipeline import DetectionPipeline, PipelineResult, ZoneSpec
from app.services.stream import CameraRuntimeConfig, CameraStream

logger = logging.getLogger(__name__)

HEALTH_INTERVAL = 10.0
#: Deleting expired health rows on every health tick was a write every 10 s
#: for no benefit: once an hour is plenty for a retention window of days.
HEALTH_PRUNE_INTERVAL = 3600.0
HEATMAP_INTERVAL = 5.0
DETECTION_PUBLISH_INTERVAL = 0.25
#: Results persisted per camera and per transaction. Batching turns ~6 ms of
#: connection + commit overhead per result into ~2.4 ms (measured on SQLite).
PERSIST_BATCH_MAX = 16
#: Attempts (and backoff base, in seconds) for a transient "database is locked".
PERSIST_RETRIES = 3
PERSIST_RETRY_DELAY = 0.25


def _detach_detector(stream: object) -> None:
    """Hand a stream's detector back to the registry before the stream is dropped.

    Uses ``getattr`` because tests install lightweight stream doubles.
    """
    detach = getattr(stream, "drop_pipeline", None)
    if not callable(detach):
        return
    try:
        detach()
    except Exception:  # noqa: BLE001 - teardown must never raise
        logger.exception("could not detach the detector from a stream")


def zones_from_camera(camera: Camera) -> list[ZoneSpec]:
    return [
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


def runtime_config_from_camera(camera: Camera) -> CameraRuntimeConfig:
    return CameraRuntimeConfig(
        id=camera.id,
        name=camera.name,
        rtsp_url=camera.rtsp_url,
        enabled=camera.enabled,
        transport=camera.transport,
        fps=camera.fps,
        detection_fps=camera.detection_fps,
        detection_enabled=camera.detection_enabled,
        motion_sensitivity=camera.motion_sensitivity,
        model_name=camera.model_name,
        model_weights=camera.model_weights,
        class_config=dict(camera.class_config or {}),
        record_objects=camera.record_objects,
        record_snapshots=camera.record_snapshots,
        reconnect_policy=dict(camera.reconnect_policy or {}),
        zones=zones_from_camera(camera),
        width=camera.width,
        height=camera.height,
    )


class CameraManager:
    def __init__(self) -> None:
        self._streams: dict[int, CameraStream] = {}
        self._configs: dict[int, CameraRuntimeConfig] = {}
        self._persisters: dict[int, Persister] = {}
        #: camera id -> (exists, checked_at). A removed camera whose stream is
        #: still draining results used to recreate rows for a dead camera id.
        self._camera_cache: dict[int, tuple[bool, float]] = {}
        self._queue: queue.Queue[tuple[CameraRuntimeConfig, PipelineResult, float]] = (
            queue.Queue(maxsize=settings.result_queue_size)
        )
        self.heatmap = HeatmapBuffer()
        self._tasks: list[asyncio.Task] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        #: signalled from the decode threads when a result is queued, so the
        #: consumer wakes up immediately instead of polling every 30 ms.
        self._wake: asyncio.Event | None = None
        self._running = False
        self._last_health_write: dict[int, float] = {}
        # ``None`` = never pruned, so the first tick always prunes (a fresh boot
        # has a small ``time.monotonic()`` value and must not skip it).
        self._last_health_prune: float | None = None
        self._last_detect_publish: dict[int, float] = {}
        self._detector_errors: dict[int, str] = {}
        self._dropped_results = 0
        # --- persistence telemetry ----------------------------------------
        self._persisted_results = 0
        self._persist_batches = 0
        self._persist_errors = 0
        self._last_persist_error: str | None = None
        self._last_persist_wait_ms = 0.0
        #: (time, snapshot) of the last CPU/RAM/VRAM probe (it costs ~1 ms).
        self._resources_cache: tuple[float, dict] | None = None
        #: One long-lived psutil handle: ``cpu_percent(None)`` reports the share
        #: since the *previous* call on the same object, so creating a new
        #: ``Process()`` per poll always answered 0.0%.
        self._process_handle: object | None = None

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        bus.bind_loop(self._loop)
        self._wake = asyncio.Event()
        self._running = True
        # Apply the saved performance profile (Settings page) before any camera
        # starts, so the first model load already uses the right device/FP16.
        try:
            apply_profile(load_profile())
        except Exception:  # noqa: BLE001 - a bad profile must not block boot
            logger.exception("could not apply the saved performance profile")
        self._tasks = [
            asyncio.create_task(self._consume_loop(), name="camera-manager-consume"),
            asyncio.create_task(self._heatmap_loop(), name="camera-manager-heatmap"),
            asyncio.create_task(self._health_loop(), name="camera-manager-health"),
        ]
        await self.sync_from_db()
        logger.info("camera manager started with %d cameras", len(self._streams))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()
        for stream in list(self._streams.values()):
            stream.stop()
            _detach_detector(stream)
        self._streams.clear()
        scheduler.shutdown()
        self._wake = None
        await self._flush_heatmap()
        # Snapshots already encoded and waiting for the disk used to be dropped
        # here (daemon threads), so a restart silently lost evidence.
        try:
            snapshot_writer().shutdown()
        except Exception:  # noqa: BLE001 - shutdown must never raise
            logger.exception("snapshot writer did not stop cleanly")
        logger.info("camera manager stopped")

    # -- synchronisation with the database --------------------------------
    async def sync_from_db(self) -> None:
        async with session_scope() as session:
            cameras = (
                (
                    await session.execute(
                        select(Camera).options(selectinload(Camera.zones)).order_by(Camera.id)
                    )
                )
                .scalars()
                .all()
            )
            preloads = [
                preload
                for preload in (self.detector_preload_args(camera) for camera in cameras)
                if preload is not None
            ]
            known = {camera.id for camera in cameras}
        # A cold model load takes seconds and used to run inside the event loop
        # during boot, freezing the API and the WebSocket feed. Load the weights
        # on worker threads first; ``apply_camera`` then finds them cached.
        for preload in preloads:
            await asyncio.to_thread(self.preload_detector, **preload)
        for camera in cameras:
            self.apply_camera(camera)
        for camera_id in list(self._streams):
            if camera_id not in known:
                self.remove_camera(camera_id)

    def apply_camera(self, camera: Camera) -> None:
        """Create, update or remove the stream for a camera (sync, no I/O)."""
        config = runtime_config_from_camera(camera)
        stream = self._streams.get(camera.id)

        if not config.enabled:
            if stream is not None:
                stream.stop()
                _detach_detector(stream)
                self._streams.pop(camera.id, None)
                self._configs.pop(camera.id, None)
                bus.publish(
                    TOPIC_STATUS,
                    event_payload(
                        kind="status",
                        camera_id=config.id,
                        camera_name=config.name,
                        message="دوربین غیرفعال شد",
                        state="disabled",
                    ),
                )
            return

        needs_new_pipeline = stream is None or (
            config.detection_enabled and stream.pipeline is None
        )

        if stream is None:
            stream = CameraStream(
                config,
                pipeline=self._build_pipeline(config) if config.detection_enabled else None,
                on_result=self._on_result,
                on_state_change=self._on_state_change,
            )
            self._streams[camera.id] = stream
        else:
            if needs_new_pipeline:
                stream.pipeline = stream.pipeline or self._build_pipeline(config)
            stream.reload(config)

        self._configs[camera.id] = config
        self._persisters.setdefault(camera.id, Persister())
        # Streams only run while the manager is active; configuration changes
        # made before ``start()`` are applied by ``sync_from_db`` at boot.
        if self._running:
            stream.start()

    @property
    def running(self) -> bool:
        return self._running

    def _build_pipeline(self, config: CameraRuntimeConfig) -> DetectionPipeline | None:
        try:
            detector = detector_registry.acquire(
                config.model_name,
                weights_path=config.model_weights,
                device=settings.device,
            )
        except Exception as exc:  # noqa: BLE001 - no AI stack -> no detection, still streams
            self._detector_errors[config.id] = f"{type(exc).__name__}: {exc}"
            logger.warning("detector unavailable for camera %s: %s", config.id, exc)
            return None

        # ``acquire`` only raises when the *import* explodes; a detector that
        # failed to load the weights comes back with ``available=False`` and a
        # ``detect()`` that always returns nothing. Running that would waste a
        # detection pass per frame and report a ``detection_fps`` that can never
        # find anything, so treat it as "detection off" and remember why.
        if not detector.available:
            reason = detector.load_error or "RF-DETR در دسترس نیست"
            self._detector_errors[config.id] = reason
            detector_registry.release(detector)
            logger.warning("detector not loaded for camera %s: %s", config.id, reason)
            return None

        self._detector_errors.pop(config.id, None)
        return DetectionPipeline(config.to_pipeline_config(), detector)

    @staticmethod
    def detector_preload_args(camera: Camera) -> dict | None:
        """Plain arguments for :meth:`preload_detector`, or ``None``.

        Reading the ORM object must happen on the session's own thread, so the
        values are extracted here and only plain data crosses into the worker.
        """
        if not camera.enabled or not camera.detection_enabled:
            return None
        return {
            "camera_id": camera.id,
            "model_name": camera.model_name,
            "model_weights": camera.model_weights,
        }

    def preload_detector(
        self,
        *,
        camera_id: int,
        model_name: str,
        model_weights: str | None = None,
    ) -> None:
        """Load a model **outside** the event loop (blocking, plain arguments).

        Call it through ``asyncio.to_thread`` before ``apply_camera``/
        ``reload_camera``: importing torch and reading the weights can take
        seconds (or minutes on a cold first run), and doing it inline froze
        every API request and the WebSocket feed for that whole time.
        """
        try:
            detector_registry.preload(
                model_name,
                weights_path=model_weights,
                device=settings.device,
            )
        except Exception as exc:  # noqa: BLE001 - no AI stack must not break CRUD
            self._detector_errors[camera_id] = f"{type(exc).__name__}: {exc}"
            logger.warning("detector preload failed for camera %s: %s", camera_id, exc)

    def detector_error(self, camera_id: int) -> str | None:
        """Why this camera's detector is not running, or ``None`` if it is.

        Only report a problem while detection is actually wanted: a camera with
        detection switched off has no pipeline for a perfectly good reason.
        """
        stream = self._streams.get(camera_id)
        if stream is None or stream.pipeline is not None:
            return None
        if not stream.config.detection_enabled:
            return None
        return self._detector_errors.get(camera_id)

    def reset(self) -> None:
        """Stop and forget every stream (used by tests and by ``--reload``)."""
        for camera_id, stream in list(self._streams.items()):
            try:
                stream.stop()
                _detach_detector(stream)
            except Exception:  # noqa: BLE001
                # Logging must never re-raise: this runs during shutdown, and a
                # broken stream must not keep the others from being stopped.
                logger.exception("failed to stop stream %s", camera_id)
        self._streams.clear()
        self._configs.clear()
        self._persisters.clear()
        self._last_health_write.clear()
        self._last_health_prune = None
        self._last_detect_publish.clear()
        self._detector_errors.clear()
        self.heatmap.clear()

    def refresh_metadata(self, camera: Camera) -> bool:
        """Apply display-only changes (name) without restarting the stream."""
        config = self._configs.get(camera.id)
        stream = self._streams.get(camera.id)
        if config is None or stream is None:
            return False
        config.name = camera.name
        stream.config.name = camera.name
        return True

    def remove_camera(self, camera_id: int) -> None:
        stream = self._streams.pop(camera_id, None)
        if stream is not None:
            try:
                stream.stop()
                _detach_detector(stream)
            except Exception:  # noqa: BLE001
                logger.exception("failed to stop stream %s", camera_id)
        self._configs.pop(camera_id, None)
        self._persisters.pop(camera_id, None)
        self._detector_errors.pop(camera_id, None)

    async def reload_camera(self, camera_id: int) -> None:
        async with session_scope() as session:
            camera = (
                await session.execute(
                    select(Camera)
                    .options(selectinload(Camera.zones))
                    .where(Camera.id == camera_id)
                )
            ).scalar_one_or_none()
            if camera is None:
                self.remove_camera(camera_id)
                return
            preload = self.detector_preload_args(camera)
        # Load the model on a worker thread, but only after the session is closed:
        # a cold RF-DETR load can take tens of seconds, and holding a pooled
        # connection for it timed every other request out (QueuePool exhaustion)
        # exactly when an operator saved a camera. The instance stays usable
        # because ``expire_on_commit`` is off and the zones were eager-loaded.
        if preload is not None:
            await asyncio.to_thread(self.preload_detector, **preload)
        self.apply_camera(camera)

    # -- live accessors ----------------------------------------------------
    def stream_ids(self) -> list[int]:
        """Ids of every camera that currently has a stream object."""
        return list(self._streams)

    def states(self) -> dict[int, str]:
        return {camera_id: stream.health.state for camera_id, stream in self._streams.items()}

    def fps_map(self) -> dict[int, float]:
        return {camera_id: stream.health.fps for camera_id, stream in self._streams.items()}

    def detection_fps_total(self) -> float:
        return sum(stream.health.detection_fps for stream in self._streams.values())

    @property
    def detection_budget(self) -> dict:
        """Configured detection demand compared with the model's capacity.

        Inference is serialised per model instance, so the demand is the sum of
        every detecting camera's cadence. A negative budget means the cameras
        are configured to ask for more frames than the GPU can deliver; the UI
        uses this to suggest a lighter model instead of silently falling behind.
        """
        demand: dict[str, float] = {}
        cameras = 0
        for stream in self._streams.values():
            config = stream.config
            if not config.detection_enabled:
                continue
            cameras += 1
            demand[config.model_name] = (
                demand.get(config.model_name, 0.0) + float(config.detection_fps)
            )
        models: list[dict] = []
        overloaded = False
        for key, used in sorted(demand.items(), key=lambda item: -item[1]):
            capacity = detection_capacity(key)
            over = used > capacity
            overloaded = overloaded or over
            models.append(
                {
                    "model": key,
                    "demand_fps": round(used, 2),
                    "capacity_fps": capacity,
                    "overloaded": over,
                }
            )
        average = (sum(demand.values()) / cameras) if cameras else 0.0
        return {
            "cameras": cameras,
            "models": models,
            "overloaded": overloaded,
            "suggested_model": suggest_model(cameras, average) if cameras else None,
            # 1.0 = the reference GPU; >1 means this card measured faster, so
            # every capacity above is already scaled by it.
            "speed_factor": speed_factor(),
        }

    def active_detection_cameras(self) -> int:
        """Cameras actually running a detector (not merely flagged for it).

        A camera whose detector could not be loaded has no pipeline, so it uses
        no GPU and must not be counted here.
        """
        return sum(
            1
            for stream in self._streams.values()
            if stream.config.detection_enabled and stream.pipeline is not None and stream.running
        )

    def health(self, camera_id: int) -> dict | None:
        stream = self._streams.get(camera_id)
        return stream.status() if stream is not None else None

    def all_health(self) -> list[dict]:
        return [stream.status() for stream in self._streams.values()]

    def latest_jpeg(
        self, camera_id: int, *, overlay: bool = False, encode_if_missing: bool = True
    ) -> bytes | None:
        """Newest live JPEG for a camera (``None`` when there is nothing yet).

        ``encode_if_missing=False`` never encodes inside the caller: the MJPEG
        loop uses it so an idle camera costs no CPU (the decode thread encodes
        as soon as ``mark_live_view`` has registered a viewer). The snapshot
        endpoint keeps the default and encodes on demand.
        """
        stream = self._streams.get(camera_id)
        if stream is None:
            return None
        if overlay:
            # Overlays are drawn on demand because the decode thread only
            # produces raw frames; the result is cached for a fraction of a
            # second so several viewers share one render.
            return stream.latest_overlay_jpeg()
        return stream.latest_jpeg(encode_if_missing=encode_if_missing)

    def mark_live_view(self, camera_id: int, fps: float | None = None) -> bool:
        """Register a live-view client so the decoder keeps encoding JPEGs.

        ``fps`` is the rate that client consumes; the decoder encodes at that
        cadence instead of the full stream rate.
        """
        stream = self._streams.get(camera_id)
        if stream is None:
            return False
        mark = getattr(stream, "mark_live_view", None)
        if callable(mark):
            try:
                mark(fps)
            except TypeError:  # test double with the old signature
                mark()
        return True

    def stream_count(self) -> int:
        return len(self._streams)

    def set_detection(self, camera_id: int, enabled: bool) -> bool:
        stream = self._streams.get(camera_id)
        if stream is None:
            return False
        if enabled and stream.pipeline is None:
            stream.pipeline = self._build_pipeline(stream.config)
        stream.config.detection_enabled = enabled
        if stream.pipeline is not None:
            stream.pipeline.rebuild_rules()
        elif not enabled:
            # Detection off: give the model back so the VRAM is actually free
            # (the UI reports "detection off" either way, this keeps it honest).
            _detach_detector(stream)
        return True

    def update_zones(self, camera_id: int, zones: list[ZoneSpec]) -> bool:
        stream = self._streams.get(camera_id)
        if stream is None:
            return False
        stream.config.zones = zones
        if stream.pipeline is not None:
            stream.pipeline.config.zones = zones
            stream.pipeline.rebuild_rules()
        return True

    # -- result handling ---------------------------------------------------
    def _on_result(self, config: CameraRuntimeConfig, result: PipelineResult) -> None:
        # The enqueue timestamp is what makes "queue latency" measurable: a
        # growing wait here means the database cannot keep up with detection.
        item = (config, result, time.monotonic())
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self._dropped_results += 1
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(item)
            except queue.Full:  # pragma: no cover - extreme backpressure
                pass
        loop = self._loop
        wake = self._wake
        if loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:  # loop closed during shutdown
                pass

    def _on_state_change(
        self, config: CameraRuntimeConfig, state: str, message: str | None
    ) -> None:
        bus.publish(
            TOPIC_STATUS,
            event_payload(
                kind="status",
                camera_id=config.id,
                camera_name=config.name,
                message=message or f"وضعیت دوربین: {state}",
                state=state,
            ),
        )

    async def _consume_loop(self) -> None:
        while self._running:
            try:
                first = self._queue.get_nowait()
            except queue.Empty:
                wake = self._wake
                if wake is None:  # no loop-bound event (tests / shutdown)
                    await asyncio.sleep(0.03)
                    continue
                # Sleep until a decode thread signals new work (or a slow
                # heartbeat): polling every 30 ms woke the loop 33 times a
                # second for nothing and added 30 ms of latency to every result.
                try:
                    await asyncio.wait_for(wake.wait(), 1.0)
                except asyncio.TimeoutError:
                    pass
                wake.clear()
                continue

            # Drain a few more results and write them together: every
            # ``session_scope`` opens a fresh SQLite connection (PRAGMAs
            # included) and commits, which measured ~6 ms per single result
            # versus ~2.4 ms when batched. Frames of one camera share a
            # transaction so a failure cannot roll back another camera's data.
            batch = [first]
            limit = max(1, int(settings.persist_batch_max))
            while len(batch) < limit:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break

            self._last_persist_wait_ms = (time.monotonic() - first[2]) * 1000.0
            grouped_results: dict[int, list[PipelineResult]] = {}
            grouped_configs: dict[int, CameraRuntimeConfig] = {}
            for config, result, _queued_at in batch:
                grouped_results.setdefault(config.id, []).append(result)
                grouped_configs.setdefault(config.id, config)

            self._persist_batches += 1
            for camera_id, results in grouped_results.items():
                try:
                    await self._persist_batch(grouped_configs[camera_id], results)
                except Exception as exc:  # noqa: BLE001 - must not stop the loop
                    self._persist_errors += 1
                    self._last_persist_error = f"{type(exc).__name__}: {exc}"
                    logger.exception("persisting results for camera %s failed", camera_id)

    async def _persist_batch(
        self, config: CameraRuntimeConfig, results: list[PipelineResult]
    ) -> None:
        """Persist every result of one camera in a single transaction.

        A transient SQLite lock (another process or an API write committing in
        between) is retried instead of dropping the frames on the floor.
        """
        if not await self._camera_exists(config.id):
            logger.info("dropping queued results for deleted camera %s", config.id)
            self._persisters.pop(config.id, None)
            return
        persister = self._persisters.setdefault(config.id, Persister())
        pipeline_config = config.to_pipeline_config()
        outcomes: list[tuple[PipelineResult, PersistOutcome]] = []
        for attempt in range(PERSIST_RETRIES):
            outcomes = []
            try:
                async with db_write_lock(), session_scope() as session:
                    for result in results:
                        outcomes.append(
                            (result, await persister.persist(session, pipeline_config, result))
                        )
            except IntegrityError as exc:
                # The camera row can disappear between the existence check and
                # the insert (an operator deletes the camera while its stream is
                # still draining results, or a reload races the delete). That is
                # not a bug in the data: re-check, and drop the results instead
                # of logging a foreign-key traceback per queued frame.
                self._camera_cache.pop(config.id, None)
                if not await self._camera_exists(config.id):
                    logger.info(
                        "camera %s was deleted while persisting; dropped %d result(s)",
                        config.id,
                        len(results),
                    )
                    self._persisters.pop(config.id, None)
                    return
                if attempt + 1 >= PERSIST_RETRIES:
                    raise
                logger.warning(
                    "integrity error while persisting camera %s (retry %d/%d): %s",
                    config.id,
                    attempt + 1,
                    PERSIST_RETRIES - 1,
                    exc,
                )
                await asyncio.sleep(PERSIST_RETRY_DELAY * (attempt + 1))
                continue
            except OperationalError as exc:
                if attempt + 1 >= PERSIST_RETRIES:
                    raise
                logger.warning(
                    "database busy while persisting camera %s (retry %d/%d): %s",
                    config.id,
                    attempt + 1,
                    PERSIST_RETRIES - 1,
                    exc,
                )
                await asyncio.sleep(PERSIST_RETRY_DELAY * (attempt + 1))
                continue
            break
        self._persisted_results += len(results)
        # Publishing happens only after the commit: the UI must never see an
        # event that was rolled back.
        for result, outcome in outcomes:
            self._publish_outcome(config, result, outcome)

    async def _camera_exists(self, camera_id: int) -> bool:
        """Whether the camera row still exists (cached for a few seconds)."""
        now = time.monotonic()
        cached = self._camera_cache.get(camera_id)
        if cached is not None and now - cached[1] < 5.0:
            return cached[0]
        async with session_scope() as session:
            exists = (
                await session.execute(select(Camera.id).where(Camera.id == camera_id))
            ).scalar() is not None
        self._camera_cache[camera_id] = (exists, now)
        return exists

    def _publish_outcome(
        self, config: CameraRuntimeConfig, result: PipelineResult, outcome: PersistOutcome
    ) -> None:
        for point, object_type in result.heatmap_points:
            self.heatmap.add(config.id, point, object_type=object_type, when=result.timestamp)

        for event in outcome.events:
            bus.publish(
                TOPIC_EVENT,
                event_payload(
                    kind="event",
                    camera_id=config.id,
                    camera_name=config.name,
                    message=event.message,
                    occurred_at=event.occurred_at,
                    event=EventRead.model_validate(event).model_dump(mode="json"),
                ),
            )

        for alert in outcome.alerts:
            bus.publish(
                TOPIC_ALERT,
                event_payload(
                    kind="alert",
                    camera_id=config.id,
                    camera_name=config.name,
                    message=alert.message,
                    occurred_at=alert.triggered_at,
                    alert=AlertRead.model_validate(alert).model_dump(mode="json"),
                ),
            )

        now = time.monotonic()
        if result.active_tracks and (
            now - self._last_detect_publish.get(config.id, 0.0) >= DETECTION_PUBLISH_INTERVAL
        ):
            self._last_detect_publish[config.id] = now
            # The live payload also carries the database id of each track, so a
            # click on an object in the live image can open its replay page.
            db_ids = {
                record.track_id: record.id
                for record in (*outcome.new_tracks, *outcome.updated_tracks)
            }
            bus.publish(
                TOPIC_DETECTION,
                event_payload(
                    kind="detection",
                    camera_id=config.id,
                    camera_name=config.name,
                    message="",
                    occurred_at=result.timestamp,
                    tracks=[
                        {
                            "track_id": track.runtime_track_id,
                            "db_id": db_ids.get(track.runtime_track_id),
                            "object_type": track.object_type,
                            "confidence": track.confidence,
                            "bbox": track.bbox_last,
                            "direction": track.direction,
                            "motion_state": track.motion_state,
                        }
                        for track in result.active_tracks
                    ],
                ),
            )

    # -- background tasks --------------------------------------------------
    async def _heatmap_loop(self) -> None:
        while self._running:
            await asyncio.sleep(HEATMAP_INTERVAL)
            try:
                await self._flush_heatmap()
            except Exception:  # noqa: BLE001
                logger.exception("heatmap flush failed")

    async def _flush_heatmap(self) -> None:
        if self.heatmap.pending_count == 0:
            return
        async with db_write_lock(), session_scope() as session:
            await flush_buffer(session, self.heatmap)

    async def _health_loop(self) -> None:
        while self._running:
            await asyncio.sleep(HEALTH_INTERVAL)
            try:
                await self._write_health()
            except Exception:  # noqa: BLE001
                logger.exception("health metrics write failed")

    async def _write_health(self) -> None:
        now = time.monotonic()
        pending: list[tuple[int, dict]] = []
        for camera_id, stream in self._streams.items():
            last = self._last_health_write.get(camera_id, 0.0)
            if now - last < HEALTH_INTERVAL:
                continue
            self._last_health_write[camera_id] = now
            pending.append((camera_id, stream.health.as_dict() | {"raw_state": stream.health.state}))
        if not pending:
            return
        async with db_write_lock(), session_scope() as session:
            # A camera can be deleted while its stream is still draining (for example when
            # the delete happens between two health ticks, or after a crash-restart).
            # Inserting metrics for a missing row would abort the whole transaction with a
            # FOREIGN KEY failure, so keep only the cameras that still exist.
            known = set(
                (
                    await session.execute(
                        select(Camera.id).where(Camera.id.in_([cid for cid, _ in pending]))
                    )
                ).scalars()
            )
            for camera_id, health in pending:
                if camera_id not in known:
                    logger.debug("skipping health metrics for deleted camera %s", camera_id)
                    continue
                session.add(
                    HealthMetric(
                        camera_id=camera_id,
                        state=health.get("raw_state") or "unknown",
                        fps=float(health.get("fps") or 0.0),
                        detection_fps=float(health.get("detection_fps") or 0.0),
                        bitrate_kbps=float(health.get("bitrate_kbps") or 0.0),
                        width=int(health.get("width") or 0),
                        height=int(health.get("height") or 0),
                        latency_ms=float(health.get("latency_ms") or 0.0),
                        jitter_ms=float(health.get("jitter_ms") or 0.0),
                        dropped_frames=int(health.get("dropped_frames") or 0),
                        reconnect_count=int(health.get("reconnect_count") or 0),
                        recorded_at=utcnow(),
                        last_frame_at=_parse_iso(health.get("last_frame_at")),
                    )
                )

            # keep the table from growing without bound
            if (
                self._last_health_prune is None
                or now - self._last_health_prune >= HEALTH_PRUNE_INTERVAL
            ):
                self._last_health_prune = now
                cutoff = utcnow() - timedelta(days=max(1, settings.retention_days))
                await session.execute(
                    HealthMetric.__table__.delete().where(HealthMetric.recorded_at < cutoff)
                )

    def _resource_stats(self) -> dict:
        """CPU/RAM/VRAM of this process, sampled at most every two seconds.

        ``psutil`` reads /proc (or the Windows equivalent) for the whole process;
        doing it on every dashboard poll added measurable event-loop time, so the
        last sample is reused. ``cpu_percent`` is normalised by the core count,
        which is what an operator expects to see next to "8 threads".
        """
        now = time.monotonic()
        cached = self._resources_cache
        if cached is not None and now - cached[0] < 2.0:
            return cached[1]
        data: dict = {
            "process_cpu_percent": None,
            "process_rss_mb": None,
            "threads": None,
            "system_cpu_percent": None,
            "system_memory_percent": None,
            "vram_used_mb": None,
            "vram_total_mb": None,
        }
        try:
            import psutil  # noqa: PLC0415 - optional telemetry dependency

            process = self._process_handle
            if process is None:
                process = psutil.Process()
                self._process_handle = process
                process.cpu_percent(None)  # prime the since-last-call counter
            with process.oneshot():
                cores = psutil.cpu_count(logical=True) or 1
                data["process_cpu_percent"] = round(process.cpu_percent(None) / cores, 1)
                data["process_rss_mb"] = round(process.memory_info().rss / (1024 * 1024), 1)
                data["threads"] = process.num_threads()
                data["system_cpu_percent"] = psutil.cpu_percent(None)
                data["system_memory_percent"] = psutil.virtual_memory().percent
        except Exception:  # noqa: BLE001 - telemetry must never break the dashboard
            logger.debug("process telemetry unavailable", exc_info=True)
        try:
            import torch  # noqa: PLC0415

            if torch.cuda.is_available():
                data["vram_used_mb"] = round(
                    torch.cuda.memory_reserved(0) / (1024 * 1024), 1
                )
                data["vram_total_mb"] = round(
                    torch.cuda.get_device_properties(0).total_memory / (1024 * 1024), 1
                )
        except Exception:  # noqa: BLE001
            pass
        self._resources_cache = (now, data)
        return data

    @property
    def stats(self) -> dict:
        inference = scheduler.stats()
        return {
            "streams": len(self._streams),
            "detection_cameras": self.active_detection_cameras(),
            "detection_fps": round(self.detection_fps_total(), 2),
            "queue_depth": self._queue.qsize(),
            "dropped_results": self._dropped_results,
            "subscribers": bus.subscriber_count,
            "models": detector_registry.loaded(),
            "detection_budget": self.detection_budget,
            "inference": inference,
            "persistence": {
                "queue_depth": self._queue.qsize(),
                "queue_capacity": self._queue.maxsize,
                "batch_max": max(1, int(settings.persist_batch_max)),
                "processed": self._persisted_results,
                "batches": self._persist_batches,
                "dropped": self._dropped_results,
                "errors": self._persist_errors,
                "last_error": self._last_persist_error,
                "queue_wait_ms": round(self._last_persist_wait_ms, 1),
            },
            "snapshots": snapshot_writer().stats(),
            "resources": self._resource_stats(),
        }


def _parse_iso(value: str | None):  # noqa: ANN201
    if not value:
        return None
    try:
        from datetime import datetime  # noqa: PLC0415

        return datetime.fromisoformat(value)
    except ValueError:
        return None


manager = CameraManager()

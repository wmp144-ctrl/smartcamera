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
from sqlalchemy.exc import OperationalError
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
from app.services.persistence import PersistOutcome, Persister
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
        self._queue: queue.Queue[tuple[CameraRuntimeConfig, PipelineResult]] = queue.Queue(
            maxsize=settings.result_queue_size
        )
        self.heatmap = HeatmapBuffer()
        self._tasks: list[asyncio.Task] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._last_health_write: dict[int, float] = {}
        # ``None`` = never pruned, so the first tick always prunes (a fresh boot
        # has a small ``time.monotonic()`` value and must not skip it).
        self._last_health_prune: float | None = None
        self._last_detect_publish: dict[int, float] = {}
        self._detector_errors: dict[int, str] = {}
        self._dropped_results = 0

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        bus.bind_loop(self._loop)
        self._running = True
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
        await self._flush_heatmap()
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
            for camera in cameras:
                self.apply_camera(camera)
            known = {camera.id for camera in cameras}
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
            # Load the model on a worker thread first: a cold load (weights
            # download) would otherwise freeze the API for its whole duration.
            preload = self.detector_preload_args(camera)
            if preload is not None:
                await asyncio.to_thread(self.preload_detector, **preload)
            self.apply_camera(camera)

    # -- live accessors ----------------------------------------------------
    def states(self) -> dict[int, str]:
        return {camera_id: stream.health.state for camera_id, stream in self._streams.items()}

    def fps_map(self) -> dict[int, float]:
        return {camera_id: stream.health.fps for camera_id, stream in self._streams.items()}

    def detection_fps_total(self) -> float:
        return sum(stream.health.detection_fps for stream in self._streams.values())

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

    def mark_live_view(self, camera_id: int) -> bool:
        """Register a live-view client so the decoder keeps encoding JPEGs."""
        stream = self._streams.get(camera_id)
        if stream is None:
            return False
        mark = getattr(stream, "mark_live_view", None)
        if callable(mark):
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
        item = (config, result)
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
                await asyncio.sleep(0.03)
                continue

            # Drain a few more results and write them together: every
            # ``session_scope`` opens a fresh SQLite connection (PRAGMAs
            # included) and commits, which measured ~6 ms per single result
            # versus ~2.4 ms when batched. Frames of one camera share a
            # transaction so a failure cannot roll back another camera's data.
            batch = [first]
            while len(batch) < PERSIST_BATCH_MAX:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break

            grouped_results: dict[int, list[PipelineResult]] = {}
            grouped_configs: dict[int, CameraRuntimeConfig] = {}
            for config, result in batch:
                grouped_results.setdefault(config.id, []).append(result)
                grouped_configs.setdefault(config.id, config)

            for camera_id, results in grouped_results.items():
                try:
                    await self._persist_batch(grouped_configs[camera_id], results)
                except Exception:  # noqa: BLE001 - must not stop the loop
                    logger.exception("persisting results for camera %s failed", camera_id)

    async def _persist_batch(
        self, config: CameraRuntimeConfig, results: list[PipelineResult]
    ) -> None:
        """Persist every result of one camera in a single transaction.

        A transient SQLite lock (another process or an API write committing in
        between) is retried instead of dropping the frames on the floor.
        """
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
        # Publishing happens only after the commit: the UI must never see an
        # event that was rolled back.
        for result, outcome in outcomes:
            self._publish_outcome(config, result, outcome)

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

    @property
    def stats(self) -> dict:
        return {
            "streams": len(self._streams),
            "detection_cameras": self.active_detection_cameras(),
            "detection_fps": round(self.detection_fps_total(), 2),
            "queue_depth": self._queue.qsize(),
            "dropped_results": self._dropped_results,
            "subscribers": bus.subscriber_count,
            "models": detector_registry.loaded(),
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

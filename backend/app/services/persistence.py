"""Persist a :class:`PipelineResult` to the database and the filesystem.

All database and file I/O for the detection path lives here so the pipeline
itself stays pure. The caller owns the ``track_map`` (runtime track id -> DB
track id), which makes persisting idempotent across frames.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.timeutil import utcnow
from app.models.camera import Zone
from app.models.detection import Event, ObjectImage, TrackRecord
from app.models.system import Alert
from app.services.alerts import AlertDecision
from app.services.bus import bus
from app.services.geometry import box_center
from app.services.pipeline import PipelineConfig, PipelineResult, SnapshotRequest, TrackSnapshot
from app.services.storage import SavedImages, delete_relative, save_object_images

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# snapshot writer (JPEG encode + disk, off the video path)
# --------------------------------------------------------------------------
class SnapshotWriter:
    """Bounded worker pool that writes stored snapshots.

    Encoding three JPEGs per object used to happen on the video thread (20-40 ms)
    and later on an *unbounded* ``ThreadPoolExecutor``: with eight cameras in
    motion the work piled up without limit, RAM filled with frames and the live
    view lagged further and further behind. A bounded queue turns that into a
    visible, counted drop instead of an invisible slowdown: the newest evidence
    still gets stored, and ``/api/system/runtime`` says how much was lost.
    """

    def __init__(self, workers: int = 2, capacity: int = 64) -> None:
        self._lock = threading.Lock()
        self._queue: queue.Queue[Callable[[], Any] | None] = queue.Queue(
            maxsize=max(1, int(capacity))
        )
        self._workers = max(1, int(workers))
        self._capacity = max(1, int(capacity))
        #: serial -> thread. Serials are never reused, so a worker that retired
        #: during a shrink can never be confused with a newly spawned one; the
        #: list-of-threads version counted *retired* threads as active and a
        #: ``resize(2)`` followed by ``resize(4)`` left the pool stuck at two
        #: workers while reporting four (reproduced before this fix).
        self._threads: dict[int, threading.Thread] = {}
        self._next_serial = 0
        self._closed = False
        self._submitted = 0
        self._done = 0
        self._dropped = 0
        self._errors = 0
        self._last_error: str | None = None
        #: one timestamp per queued task; a deque so a resize that drops the
        #: oldest tasks can drop their timestamps with them (a plain list drifted
        #: out of sync and reported nonsense ``queue_wait_ms`` forever).
        self._queued_at: deque[float] = deque()
        self._last_wait_ms = 0.0
        for _ in range(self._workers):
            self._spawn()

    @property
    def closed(self) -> bool:
        return self._closed

    # -- workers -----------------------------------------------------------
    def _active(self) -> list[int]:
        """Serials of the workers that are alive right now."""
        return [serial for serial, thread in self._threads.items() if thread.is_alive()]

    def _is_excess(self, serial: int) -> bool:
        """Whether this worker is one of the extras left by a shrink.

        Retirement used to compare ``serial >= self._workers``, which assumed
        serials stayed dense from zero. They do not: after 2 -> 4 -> 2 -> 4 the
        new serial is 4 while ``workers`` is also 4, so every freshly spawned
        worker retired immediately and ``resize`` spawned another one forever
        (measured: 13 744 threads until ``can't start new thread``).
        """
        alive = sorted(self._active())
        if len(alive) <= self._workers:
            return False
        # Keep the oldest workers, retire the highest serials (they are the ones
        # most likely to be idle, never a thread that was just spawned).
        return serial in alive[self._workers:]

    def _spawn(self) -> bool:
        serial = self._next_serial
        self._next_serial += 1
        thread = threading.Thread(
            target=self._work,
            args=(serial,),
            name=f"snapshot-writer-{serial}",
            daemon=True,
        )
        self._threads[serial] = thread
        try:
            thread.start()
        except RuntimeError as exc:  # thread exhaustion: never loop on it
            self._threads.pop(serial, None)
            self._errors += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("could not start a snapshot worker: %s", exc)
            return False
        return True

    def _retire(self, serial: int) -> None:
        with self._lock:
            self._threads.pop(serial, None)

    def _work(self, serial: int) -> None:
        while True:
            with self._lock:
                if self._closed or self._is_excess(serial):
                    # resized down (or shutting down): this worker retires
                    self._threads.pop(serial, None)
                    return
                pending = self._queue
            try:
                task = pending.get(timeout=0.3)
            except queue.Empty:
                continue
            if task is None:
                self._retire(serial)
                return
            started = time.monotonic()
            try:
                task()
            except Exception as exc:  # noqa: BLE001 - a bad frame must not kill the writer
                with self._lock:
                    self._errors += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("snapshot write failed: %s", exc)
            finally:
                with self._lock:
                    self._done += 1
                    if self._queued_at:
                        self._last_wait_ms = (started - self._queued_at.popleft()) * 1000.0

    # -- api ---------------------------------------------------------------
    def submit(self, task: Callable[[], Any]) -> bool:
        """Queue work; ``False`` means the queue was full and it was dropped."""
        with self._lock:
            if self._closed:
                return False
            self._submitted += 1
            try:
                self._queue.put_nowait(task)
            except queue.Full:
                self._dropped += 1
                if self._dropped == 1 or self._dropped % 50 == 0:
                    logger.warning(
                        "snapshot queue full (capacity %d); %d snapshot(s) dropped so far",
                        self._capacity,
                        self._dropped,
                    )
                return False
            self._queued_at.append(time.monotonic())
        return True

    def resize(self, workers: int, capacity: int) -> None:
        """Apply new worker/queue sizes without losing what is already queued."""
        workers = max(1, int(workers))
        capacity = max(1, int(capacity))
        with self._lock:
            if workers == self._workers and capacity == self._capacity:
                return
            preserved: list[Callable[[], Any] | None] = []
            while True:
                try:
                    preserved.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            overflow = max(0, len(preserved) - capacity)
            if overflow:
                # Oldest pending frames are the ones an operator least needs.
                preserved = preserved[overflow:]
                self._dropped += overflow
                for _ in range(overflow):
                    if self._queued_at:
                        self._queued_at.popleft()
            self._capacity = capacity
            self._queue = queue.Queue(maxsize=capacity)
            for item in preserved:
                self._queue.put_nowait(item)
            self._workers = workers
            self._threads = {
                serial: thread for serial, thread in self._threads.items() if thread.is_alive()
            }
            # Bounded: ``_spawn`` reports failure instead of retrying forever.
            for _ in range(workers):
                if len(self._active()) >= workers:
                    break
                if not self._spawn():
                    break
            # Workers that are now excess notice ``_is_excess`` on their next
            # loop iteration and remove themselves.

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "workers": len(self._active()),
                "workers_configured": self._workers,
                "capacity": self._capacity,
                "queued": self._queue.qsize(),
                "submitted": self._submitted,
                "written": self._done,
                "dropped": self._dropped,
                "errors": self._errors,
                "last_error": self._last_error,
                "queue_wait_ms": round(self._last_wait_ms, 1),
            }

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop every worker and drop whatever is still queued.

        Called from ``CameraManager.stop()``: without it a restart discarded the
        snapshots already encoded and waiting on disk, silently losing evidence.
        """
        with self._lock:
            self._closed = True
            for _ in list(self._threads.values()):
                try:
                    self._queue.put_nowait(None)
                except queue.Full:
                    break
            threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout=timeout)
        with self._lock:
            self._threads.clear()
            self._queued_at.clear()


_writer: SnapshotWriter | None = None
_writer_lock = threading.Lock()


def snapshot_writer() -> SnapshotWriter:
    """Process-wide writer, created from the current performance profile."""
    global _writer  # noqa: PLW0603 - one writer per process
    with _writer_lock:
        # A shutdown writer stays in the module global; reusing it would accept
        # work that no thread will ever run, so a later start() gets a fresh one.
        if _writer is None or _writer.closed:
            _writer = SnapshotWriter(
                workers=settings.snapshot_workers,
                capacity=settings.snapshot_queue_size,
            )
        return _writer


def materialize_snapshots(config: PipelineConfig, result: PipelineResult) -> int:
    """Write the requested snapshots to disk on the video thread.

    Doing the file I/O here means the pipeline result can travel to the async
    persistence task holding only small, plain metadata instead of full frames.
    Returns the number of images written.
    """
    written = 0
    if not config.record_objects:
        # The operator turned object image storage off: release the frames
        # without touching the disk (the persistence step drops the requests).
        for request in result.snapshots:
            request.frame = None
        return 0
    for request in result.snapshots:
        if request.saved is not None or request.frame is None:
            continue
        try:
            request.saved = save_object_images(
                request.frame,
                request.bbox,
                camera_id=config.camera_id,
                object_type=request.object_type,
                track_id=request.runtime_track_id,
                kind=request.kind,
                captured_at=request.captured_at,
                best=request.best,
            )
            written += 1
        except Exception as exc:
            logger.warning("snapshot materialisation failed: %s", exc)
        finally:
            request.frame = None
    return written


@dataclass
class PersistOutcome:
    new_tracks: list[TrackRecord] = field(default_factory=list)
    updated_tracks: list[TrackRecord] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    images: list[ObjectImage] = field(default_factory=list)
    pruned_images: int = 0
    errors: list[str] = field(default_factory=list)


ZONE_CACHE_TTL = 30.0


class Persister:
    """Stateful persister holding the runtime->DB track id map."""

    def __init__(self) -> None:
        self.track_map: dict[int, int] = {}
        self._zone_cache: dict[int, tuple[float, set[int]]] = {}

    def reset(self) -> None:
        self.track_map.clear()
        self._zone_cache.clear()

    async def _valid_zone_ids(self, session: AsyncSession, camera_id: int) -> set[int]:
        """Zone ids that actually exist for this camera.

        A zone can be deleted while a stream is still running with a stale
        configuration; dropping the reference here keeps the foreign keys sound
        instead of failing the whole write.
        """
        now = time.monotonic()
        cached = self._zone_cache.get(camera_id)
        if cached is not None and now - cached[0] < ZONE_CACHE_TTL:
            return cached[1]
        ids = set(
            (await session.execute(select(Zone.id).where(Zone.camera_id == camera_id)))
            .scalars()
            .all()
        )
        self._zone_cache[camera_id] = (now, ids)
        return ids

    def forget(self, track_ids: list[int]) -> None:
        for track_id in track_ids:
            self.track_map.pop(track_id, None)

    # ------------------------------------------------------------------
    async def persist(
        self,
        session: AsyncSession,
        config: PipelineConfig,
        result: PipelineResult,
    ) -> PersistOutcome:
        outcome = PersistOutcome()
        valid_zones = await self._valid_zone_ids(session, config.camera_id)

        def zone_ref(zone_id: int | None) -> int | None:
            return zone_id if zone_id in valid_zones else None

        # 1) tracks ---------------------------------------------------------
        snapshots: list[tuple[TrackSnapshot, str]] = []
        for snapshot in result.started_tracks:
            snapshots.append((snapshot, "active"))
        for snapshot in result.active_tracks:
            snapshots.append((snapshot, "active"))
        for snapshot in result.ended_tracks:
            snapshots.append((snapshot, "ended"))

        seen: set[int] = set()
        for snapshot, status in snapshots:
            if snapshot.runtime_track_id in seen:
                # still refresh the mutable fields (position, motion state)
                record = await self._get_record(session, snapshot.runtime_track_id)
                if record is not None:
                    self._apply_snapshot(record, snapshot, status)
                    record.zone_id = zone_ref(record.zone_id)
                    record.crossed_line_id = zone_ref(record.crossed_line_id)
                    outcome.updated_tracks.append(record)
                continue
            seen.add(snapshot.runtime_track_id)
            record = await self._get_record(session, snapshot.runtime_track_id)
            if record is None:
                record = self._create_record(config, snapshot, status)
                record.zone_id = zone_ref(record.zone_id)
                record.crossed_line_id = zone_ref(record.crossed_line_id)
                session.add(record)
                await session.flush()
                self.track_map[snapshot.runtime_track_id] = record.id
                outcome.new_tracks.append(record)
            else:
                self._apply_snapshot(record, snapshot, status)
                record.zone_id = zone_ref(record.zone_id)
                record.crossed_line_id = zone_ref(record.crossed_line_id)
                outcome.updated_tracks.append(record)

        # 2) images ---------------------------------------------------------
        images_by_track: dict[int, list[ObjectImage]] = defaultdict(list)
        for request in result.snapshots:
            if not config.record_snapshots or not config.record_objects:
                break
            track_id = self.track_map.get(request.runtime_track_id)
            if track_id is None:
                continue
            try:
                image = await self._save_image(session, config, track_id, request)
            except Exception as exc:
                logger.warning("failed to save snapshot: %s", exc)
                outcome.errors.append(f"snapshot: {exc}")
                continue
            images_by_track[track_id].append(image)
            outcome.images.append(image)

            if request.best:
                record = await session.get(TrackRecord, track_id)
                if record is not None:
                    record.best_image_id = image.id

        # 3) prune images of static objects ---------------------------------
        for runtime_id in result.prune_tracks:
            track_id = self.track_map.get(runtime_id)
            if track_id is None:
                continue
            outcome.pruned_images += await self._prune_images(session, track_id)

        # 4) events ---------------------------------------------------------
        image_cache: dict[int, dict[str, ObjectImage]] = {}
        for draft in result.events:
            track_id = (
                self.track_map.get(draft.runtime_track_id)
                if draft.runtime_track_id is not None
                else None
            )
            event = Event(
                camera_id=config.camera_id,
                track_id=track_id,
                zone_id=zone_ref(draft.zone_id),
                event_type=draft.event_type,
                severity=draft.severity,
                object_type=draft.object_type,
                confidence=draft.confidence,
                direction=draft.direction,
                message=draft.message,
                occurred_at=draft.occurred_at,
                ended_at=draft.ended_at,
                bbox=list(draft.bbox or []),
                meta=dict(draft.meta or {}),
            )
            if track_id is not None and draft.attach_image_kind:
                image = await self._pick_image(
                    session, track_id, draft.attach_image_kind, images_by_track, image_cache
                )
                if image is not None:
                    event.snapshot_full_path = image.full_path
                    event.snapshot_crop_path = image.crop_path
            session.add(event)
            outcome.events.append(event)
        await session.flush()

        # 5) alerts ---------------------------------------------------------
        # ``delivered`` records whether a dashboard was actually listening, so
        # the flag stops being permanently false in the API responses.
        listeners = bus.subscriber_count > 0
        for decision in result.alerts:
            event = self._matching_event(outcome.events, decision)
            alert = Alert(
                camera_id=config.camera_id,
                zone_id=zone_ref(decision.rule.zone_id),
                event_id=event.id if event is not None else None,
                rule_name=decision.rule.name,
                severity=decision.severity,
                title=decision.title,
                message=decision.message,
                object_type=decision.object_type,
                triggered_at=event.occurred_at if event is not None else utcnow(),
                sound=decision.sound,
                delivered=listeners,
                delivered_at=utcnow() if listeners else None,
                meta={"event_type": event.event_type if event is not None else "detection"},
            )
            session.add(alert)
            outcome.alerts.append(alert)
        await session.flush()

        self.forget([t.runtime_track_id for t in result.ended_tracks])
        return outcome

    # ------------------------------------------------------------------
    @staticmethod
    def _matching_event(events: list[Event], decision: AlertDecision) -> Event | None:
        for event in events:
            if event.event_type == "alert" and event.zone_id == decision.rule.zone_id:
                return event
        for event in events:
            if event.event_type == "alert":
                return event
        return None

    async def _get_record(self, session: AsyncSession, runtime_id: int) -> TrackRecord | None:
        track_id = self.track_map.get(runtime_id)
        if track_id is None:
            return None
        return await session.get(TrackRecord, track_id)

    @staticmethod
    def _create_record(
        config: PipelineConfig, snapshot: TrackSnapshot, status: str
    ) -> TrackRecord:
        return TrackRecord(
            camera_id=config.camera_id,
            track_id=snapshot.runtime_track_id,
            object_type=snapshot.object_type,
            confidence=snapshot.confidence,
            first_seen=snapshot.first_seen,
            last_seen=snapshot.last_seen,
            duration_seconds=snapshot.duration_seconds,
            motion_state=snapshot.motion_state,
            direction=snapshot.direction,
            velocity_x=snapshot.velocity_x,
            velocity_y=snapshot.velocity_y,
            distance_travelled=snapshot.distance_travelled,
            bbox_first=list(snapshot.bbox_first),
            bbox_last=list(snapshot.bbox_last),
            center_x=round(box_center(snapshot.bbox_last)[0], 6) if snapshot.bbox_last else None,
            center_y=round(box_center(snapshot.bbox_last)[1], 6) if snapshot.bbox_last else None,
            status=status,
            entered_frame=snapshot.entered_frame,
            exited_frame=snapshot.exited_frame,
            zone_id=snapshot.zone_id,
            crossed_line_id=snapshot.crossed_line_id,
            line_direction=snapshot.line_direction,
            frame_count=snapshot.frame_count,
            path=[list(point) for point in snapshot.path],
            meta=dict(snapshot.meta or {}),
        )

    @staticmethod
    def _apply_snapshot(record: TrackRecord, snapshot: TrackSnapshot, status: str) -> None:
        record.last_seen = snapshot.last_seen
        record.duration_seconds = snapshot.duration_seconds
        record.motion_state = snapshot.motion_state
        record.direction = snapshot.direction
        record.velocity_x = snapshot.velocity_x
        record.velocity_y = snapshot.velocity_y
        record.distance_travelled = snapshot.distance_travelled
        record.bbox_last = list(snapshot.bbox_last)
        if snapshot.bbox_last:
            cx, cy = box_center(snapshot.bbox_last)
            record.center_x = round(cx, 6)
            record.center_y = round(cy, 6)
        record.confidence = max(record.confidence, snapshot.confidence)
        record.zone_id = snapshot.zone_id if snapshot.zone_id is not None else record.zone_id
        record.crossed_line_id = snapshot.crossed_line_id or record.crossed_line_id
        record.line_direction = snapshot.line_direction or record.line_direction
        record.frame_count = snapshot.frame_count
        if snapshot.path:
            record.path = [list(point) for point in snapshot.path]
        record.status = status
        if status == "ended":
            record.exited_frame = True
        record.meta = {**(record.meta or {}), **(snapshot.meta or {})}

    async def _save_image(
        self,
        session: AsyncSession,
        config: PipelineConfig,
        track_id: int,
        request: SnapshotRequest,
    ) -> ObjectImage:
        saved = (
            request.saved
            if isinstance(request.saved, SavedImages)
            else save_object_images(
                request.frame,
                request.bbox,
                camera_id=config.camera_id,
                object_type=request.object_type,
                track_id=request.runtime_track_id,
                kind=request.kind,
                captured_at=request.captured_at,
                best=request.best,
            )
        )

        if request.replace_previous:
            # Drop the outdated rolling "best" frame but keep the entry/exit anchors.
            await self._drop_kinds(session, track_id, {"best"})

        image = ObjectImage(
            track_id=track_id,
            camera_id=config.camera_id,
            kind=request.kind,
            full_path=saved.full_path,
            crop_path=saved.crop_path,
            thumb_path=saved.thumb_path,
            bbox=list(request.bbox),
            confidence=request.confidence,
            quality_score=request.quality.score,
            sharpness=request.quality.sharpness,
            brightness=request.quality.brightness,
            frame_width=saved.width,
            frame_height=saved.height,
            captured_at=request.captured_at,
            is_best=request.best,
        )
        session.add(image)
        await session.flush()
        if image.is_best:
            # Exactly one image per track may be flagged as the best one.
            await session.execute(
                ObjectImage.__table__.update()
                .where(ObjectImage.track_id == track_id, ObjectImage.id != image.id)
                .values(is_best=False)
            )
            await session.flush()
        return image

    async def _pick_image(
        self,
        session: AsyncSession,
        track_id: int,
        kind: str,
        batch: dict[int, list[ObjectImage]],
        cache: dict[int, dict[str, ObjectImage]],
    ) -> ObjectImage | None:
        from_batch = batch.get(track_id) or []
        if kind != "best":
            for image in from_batch:
                if image.kind == kind:
                    return image
        for image in from_batch:
            if image.is_best:
                return image
        if from_batch:
            return from_batch[0]

        if track_id not in cache:
            rows = (
                await session.execute(
                    select(ObjectImage)
                    .where(ObjectImage.track_id == track_id)
                    .order_by(ObjectImage.is_best.desc(), ObjectImage.quality_score.desc())
                )
            ).scalars().all()
            cache[track_id] = {row.kind: row for row in rows}
            cache[track_id]["__best__"] = next((r for r in rows if r.is_best), rows[0] if rows else None)
        mapping = cache[track_id]
        if kind in mapping and mapping[kind] is not None:
            return mapping[kind]
        return mapping.get("__best__")

    async def _prune_images(self, session: AsyncSession, track_id: int) -> int:
        """Collapse a track to its single best image.

        This is what turns a stationary object into exactly one saved picture:
        a static object stops producing better frames, and any entry/exit
        anchors left over are removed here.
        """
        return await self._delete_images(
            session, track_id, keep=lambda rows: {
                (next((r for r in rows if r.is_best), None) or max(rows, key=lambda r: r.quality_score)).id
            }
        )

    async def _drop_kinds(self, session: AsyncSession, track_id: int, kinds: set[str]) -> int:
        """Delete images of specific kinds (outdated rolling "best" frames)."""
        if not kinds:
            return 0
        return await self._delete_images(
            session, track_id, keep=lambda rows: {r.id for r in rows if r.kind not in kinds}
        )

    async def _delete_images(self, session: AsyncSession, track_id: int, keep) -> int:
        rows = list(
            (
                await session.execute(select(ObjectImage).where(ObjectImage.track_id == track_id))
            )
            .scalars()
            .all()
        )
        if len(rows) <= 1:
            return 0
        keep_ids = keep(rows)
        removed = 0
        for row in rows:
            if row.id in keep_ids:
                continue
            delete_relative(row.full_path)
            delete_relative(row.crop_path)
            delete_relative(row.thumb_path)
            await session.delete(row)
            removed += 1
        if removed:
            await session.flush()
        return removed

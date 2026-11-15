"""Central inference scheduler: one GPU, many cameras, no starvation.

RF-DETR can only run one frame at a time on a single GPU, so every camera used
to own a detector thread that simply blocked on a shared lock. That had three
consequences that looked like "the app hangs" once more than two cameras
detected:

* **unfairness** — a camera that won the lock twice in a row made another wait
  for a whole extra inference, so with four cameras one could be blind for a
  second at a time;
* **staleness** — each camera's own queue was fine, but results still arrived
  out of order, seconds after the frame they described;
* **thread pile-up** — N threads spent their lives inside ``lock.wait()``,
  doing nothing but competing.

This module replaces that with a single dispatcher thread and a *slot* per
camera:

* a slot holds at most ``queue_size`` frames (default 1) and a new frame
  **replaces** the waiting one — a scheduler never works on a frame that is no
  longer the newest one the camera has;
* the dispatcher walks the cameras with waiting frames round-robin, so every
  camera gets at most one turn per round and none can starve another;
* the model is loaded once (the detector registry) and called from this single
  thread, which is also the only place that measures inference time, queue wait
  and dropped frames.

The scheduler knows nothing about cameras, pipelines or torch: it runs callables.
That keeps it unit-testable and usable with a CPU-only setup (the same path,
just a slower callable).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: A job is one sampled frame's worth of work (motion + detect + track).
Job = Callable[[], Any]


@dataclass
class CameraStats:
    submitted: int = 0
    processed: int = 0
    dropped: int = 0
    errors: int = 0
    last_wait_ms: float = 0.0
    max_wait_ms: float = 0.0
    last_inference_ms: float = 0.0
    #: exponential moving average of the time one job takes
    avg_inference_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "submitted": self.submitted,
            "processed": self.processed,
            "dropped": self.dropped,
            "errors": self.errors,
            "wait_ms": round(self.last_wait_ms, 1),
            "max_wait_ms": round(self.max_wait_ms, 1),
            "inference_ms": round(self.last_inference_ms, 1),
            "avg_inference_ms": round(self.avg_inference_ms, 1),
        }


@dataclass
class _Slot:
    #: waiting frames, newest last; guarded by the scheduler lock (never a
    #: per-slot lock: the dispatcher holds that lock for microseconds only)
    frames: deque[tuple[float, Job]] = field(default_factory=deque)


class InferenceScheduler:
    """Run every camera's detection work on one fair, round-robin thread."""

    def __init__(self, *, queue_size: int = 1, name: str = "inference") -> None:
        self._queue_size = max(1, int(queue_size))
        self._name = name
        self._slots: dict[int, _Slot] = {}
        #: cameras that were unregistered: a decode thread that is still
        #: finishing one frame must not resurrect their slot (a deleted camera
        #: used to stay in the stats and in ``pending`` for the whole uptime).
        self._removed: set[int] = set()
        self._ready: deque[int] = deque()
        #: Re-entrant because the idle condition is built on this same lock:
        #: ``wait_camera_idle`` and the dispatcher used to take the lock and the
        #: condition in opposite orders, which deadlocked the two of them.
        self._lock = threading.RLock()
        #: Signalled when the dispatcher finishes (or abandons) a job
        self._idle = threading.Condition(self._lock)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stats: dict[int, CameraStats] = {}
        #: camera whose job the dispatcher is executing right now
        self._current: int | None = None
        #: camera whose job was taken off the queue but has not started yet.
        #: ``_next_job`` releases the lock between claiming and the dispatcher
        #: setting ``_current``; without this field ``wait_camera_idle`` saw an
        #: idle camera in that window and the caller released the pipeline one
        #: instruction before the job entered it.
        self._inflight: int | None = None
        #: dispatcher threads a ``shutdown`` could not join (still inside a job);
        #: a later ``start`` joins them before spawning a new one so a camera
        #: server never ends up with two round-robin dispatchers.
        self._stale: list[threading.Thread] = []
        self._rounds = 0
        self._last_job_ms = 0.0
        self._last_wait_ms = 0.0
        self._started_at = 0.0

    # -- configuration -----------------------------------------------------
    @property
    def queue_size(self) -> int:
        return self._queue_size

    def set_queue_size(self, size: int, *, drain: bool = True) -> None:
        """Change the per-camera slot capacity at runtime.

        Lowering it keeps only the newest frame of every camera, which is what
        the new capacity would have allowed anyway.
        """
        size = max(1, int(size))
        with self._lock:
            self._queue_size = size
            if drain:
                for camera_id, slot in self._slots.items():
                    stats = self._stats.get(camera_id)
                    while len(slot.frames) > size:
                        slot.frames.popleft()
                        if stats is not None:
                            stats.dropped += 1

    # -- camera registration ----------------------------------------------
    def register(self, camera_id: int) -> None:
        with self._lock:
            self._removed.discard(camera_id)
            self._slots.setdefault(camera_id, _Slot())
            self._stats.setdefault(camera_id, CameraStats())

    def unregister(self, camera_id: int) -> None:
        """Forget a camera: its waiting frames are dropped, not run.

        Called on stop/delete/reload so a removed camera cannot keep the GPU
        busy (or deliver results after its pipeline was released).
        """
        with self._lock:
            slot = self._slots.pop(camera_id, None)
            if slot is not None:
                stats = self._stats.get(camera_id)
                if stats is not None:
                    stats.dropped += len(slot.frames)
            self._stats.pop(camera_id, None)
            self._removed.add(camera_id)
            self._ready = deque(cid for cid in self._ready if cid != camera_id)

    # -- submission --------------------------------------------------------
    def submit(self, camera_id: int, job: Job) -> bool:
        """Queue one job for a camera.

        Returns ``False`` when the slot was full, i.e. the previous waiting
        frame was replaced: the caller can count that as a dropped detection
        (the newest frame is always the one that runs).
        """
        if camera_id in self._removed:
            # The camera was stopped/deleted: a frame already in flight must not
            # start a new dispatch round for it.
            return False
        self._ensure_thread()
        with self._lock:
            if camera_id in self._removed:
                return False
            slot = self._slots.get(camera_id)
            if slot is None:
                slot = self._slots[camera_id] = _Slot()
            stats = self._stats.setdefault(camera_id, CameraStats())
            stats.submitted += 1
            fresh = not slot.frames
            # Queued is not dropped: with ``queue_size > 1`` the second frame used
            # to report ``False`` (counted as a lost detection) even though it
            # was waiting normally.
            accepted = True
            slot.frames.append((time.monotonic(), job))
            if len(slot.frames) > self._queue_size:
                slot.frames.popleft()
                stats.dropped += 1
                accepted = False
            if fresh:
                self._ready.append(camera_id)
        self._wake.set()
        return accepted

    def wait_ms(self, camera_id: int) -> float:
        """How long the last job of this camera waited for the shared thread."""
        with self._lock:
            stats = self._stats.get(camera_id)
            return round(stats.last_wait_ms, 1) if stats is not None else 0.0

    def pending(self) -> int:
        with self._lock:
            return sum(len(slot.frames) for slot in self._slots.values())

    # -- dispatcher --------------------------------------------------------
    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            # A dispatcher that outlived its shutdown keeps its *own* (already
            # set) stop event, so it finishes its current job and exits without
            # touching the queue again. No join here: the caller is a decode
            # thread and must not be stalled for the duration of a slow job.
            self._stale = [thread for thread in self._stale if thread.is_alive()]
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            stop = threading.Event()
            self._stop = stop
            self._started_at = time.monotonic()
            self._thread = threading.Thread(
                target=self._run, args=(stop,), name=self._name, daemon=True
            )
            self._thread.start()

    def _claim_is_valid(self, camera_id: int) -> bool:
        """Whether a job taken off the queue may still run.

        The job is claimed inside ``_next_job``; between that and the dispatcher
        entering ``fn`` the camera can be unregistered (stop/delete/reload). Such
        a job must be dropped, not handed a pipeline that is being released.
        """
        with self._lock:
            return camera_id not in self._removed

    def wait_camera_idle(self, camera_id: int, timeout: float = 5.0) -> bool:
        """Block until no job of *camera_id* is executing. True = idle.

        A camera that is stopped (or deleted) must not have its pipeline
        released while the shared thread is still inside ``pipeline.process``:
        the release drops the last reference to the model and closes it under
        the running call, which surfaced as random detection errors right after
        an operator pressed Save or Delete. The scheduler lock is *not* held
        while waiting, so other cameras keep running normally.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        with self._idle:  # holds ``_lock`` too: one lock, one order
            while True:
                busy = camera_id in {self._current, self._inflight}
                if not busy:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(min(remaining, 0.2))

    def _next_job(self) -> tuple[int, float, Job, CameraStats] | None:
        with self._lock:
            while self._ready:
                camera_id = self._ready.popleft()
                slot = self._slots.get(camera_id)
                if slot is None or not slot.frames:
                    continue
                # Newest first: with ``queue_size > 1`` the oldest frame used to
                # run first, so a 2-deep queue analysed stale pictures while the
                # newest one waited for the next round.
                queued_at, job = slot.frames.pop()
                if slot.frames:
                    stats_dropped = len(slot.frames)
                    slot.frames.clear()
                else:
                    stats_dropped = 0
                self._inflight = camera_id
                # Fetched under the same lock: a ``setdefault`` in the
                # dispatcher used to re-create the stats of a camera that
                # ``unregister`` had just removed, leaving a ghost camera in
                # /api/system/runtime until the next restart.
                stats = self._stats.setdefault(camera_id, CameraStats())
                stats.dropped += stats_dropped
                return camera_id, queued_at, job, stats
            return None

    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self._wake.wait(0.2)
            self._wake.clear()
            job = self._next_job()
            while job is not None and not stop.is_set():
                camera_id, queued_at, fn, stats = job
                if not self._claim_is_valid(camera_id):
                    # Stopped/deleted while the frame was on its way in.
                    with self._lock:
                        if self._inflight == camera_id:
                            self._inflight = None
                        self._idle.notify_all()
                    job = self._next_job()
                    continue
                wait_ms = (time.monotonic() - queued_at) * 1000.0
                with self._lock:
                    self._last_wait_ms = wait_ms
                    stats.last_wait_ms = wait_ms
                    stats.max_wait_ms = max(stats.max_wait_ms, wait_ms)
                    self._current = camera_id
                started = time.perf_counter()
                try:
                    fn()
                except Exception:  # noqa: BLE001 - one camera must not stop the rest
                    with self._lock:
                        stats.errors += 1
                    logger.exception("inference job failed for camera %s", camera_id)
                finally:
                    elapsed = (time.perf_counter() - started) * 1000.0
                    with self._lock:
                        self._current = None
                        if self._inflight == camera_id:
                            self._inflight = None
                        self._last_job_ms = elapsed
                        stats.last_inference_ms = elapsed
                        stats.avg_inference_ms = (
                            elapsed
                            if stats.avg_inference_ms <= 0
                            else 0.8 * stats.avg_inference_ms + 0.2 * elapsed
                        )
                        stats.processed += 1
                        self._rounds += 1
                        self._idle.notify_all()
                job = self._next_job()

    # -- introspection -----------------------------------------------------
    def stats(self) -> dict[str, Any]:
        # Everything below happens *inside* the lock: a camera registering or
        # stopping while this endpoint was serialising used to raise
        # "dictionary changed size during iteration" and return HTTP 500 from
        # /api/system/runtime (which the dashboard polls every few seconds).
        with self._lock:
            cameras = {str(cid): stats.as_dict() for cid, stats in self._stats.items()}
            pending = sum(len(slot.frames) for slot in self._slots.values())
            current = self._current
            rounds = self._rounds
            total = CameraStats()
            for item in self._stats.values():
                total.submitted += item.submitted
                total.processed += item.processed
                total.dropped += item.dropped
                total.errors += item.errors
        return {
            "running": bool(self._thread is not None and self._thread.is_alive()),
            "queue_size": self._queue_size,
            "pending": pending,
            "cameras": len(cameras),
            "processed": total.processed,
            "dropped": total.dropped,
            "errors": total.errors,
            "busy_camera": current,
            "inference_ms": round(self._last_job_ms, 1),
            "queue_wait_ms": round(self._last_wait_ms, 1),
            "rounds": rounds,
            "uptime_seconds": (
                round(time.monotonic() - self._started_at, 1) if self._started_at else 0.0
            ),
            "detail": cameras,
        }

    def reset(self) -> None:
        """Forget every camera and its counters (tests, camera reset)."""
        with self._lock:
            self._slots.clear()
            self._ready.clear()
            self._stats.clear()
            self._removed.clear()
            self._current = None
            self._inflight = None
            self._rounds = 0
            self._last_job_ms = 0.0
            self._last_wait_ms = 0.0

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop the dispatcher thread, abandoning anything still queued."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                # Keep the reference: ``_ensure_thread`` joins it before starting
                # a replacement. Dropping it here started a *second* dispatcher
                # on the next submit, and clearing the shared stop event made the
                # old one keep looping as well (measured: two live dispatchers).
                self._stale.append(thread)
                logger.warning("inference scheduler did not stop within %.1fs", timeout)
        with self._lock:
            self._idle.notify_all()
        self.reset()


#: Process-wide scheduler. Cameras submit through this, the detector registry
#: keeps the single model instance it runs.
scheduler = InferenceScheduler(queue_size=1)

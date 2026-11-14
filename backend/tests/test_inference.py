"""Inference scheduler: fairness, drop-oldest, isolation and telemetry."""

from __future__ import annotations

import threading
import time

from app.services.inference import InferenceScheduler


def test_round_robin_gives_every_camera_a_turn() -> None:
    """A camera that keeps submitting must not starve the other ones."""
    scheduler = InferenceScheduler(queue_size=4, name="test-rr")
    order: list[str] = []
    gate = threading.Event()

    def blocked() -> None:
        gate.wait(2.0)

    # Camera 1 occupies the thread, camera 2 and 3 queue behind it.
    scheduler.submit(1, blocked)
    scheduler.submit(2, lambda: order.append("c2"))
    scheduler.submit(3, lambda: order.append("c3"))
    scheduler.submit(1, lambda: order.append("c1"))

    gate.set()
    deadline = time.monotonic() + 5.0
    while len(order) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)

    assert order == ["c2", "c3", "c1"], order
    scheduler.shutdown()


def test_newest_frame_replaces_the_waiting_one() -> None:
    """Capacity 1: analyses the newest frame, counts the skipped one."""
    scheduler = InferenceScheduler(queue_size=1, name="test-drop")
    gate = threading.Event()
    ran: list[int] = []

    scheduler.submit(1, lambda: gate.wait(2.0))
    accepted_first = scheduler.submit(2, lambda: ran.append(1))
    accepted_second = scheduler.submit(2, lambda: ran.append(2))

    gate.set()
    deadline = time.monotonic() + 5.0
    while not ran and time.monotonic() < deadline:
        time.sleep(0.02)

    assert accepted_first is True
    assert accepted_second is False, "a full slot must report the replaced frame"
    assert ran == [2], "the newest frame is the one that runs"
    detail = scheduler.stats()["detail"]["2"]
    assert detail["dropped"] == 1
    assert detail["processed"] == 1
    scheduler.shutdown()


def test_unregister_drops_pending_frames() -> None:
    scheduler = InferenceScheduler(queue_size=2, name="test-unregister")
    gate = threading.Event()
    ran: list[str] = []
    scheduler.submit(1, lambda: gate.wait(2.0))
    scheduler.submit(2, lambda: ran.append("c2"))

    scheduler.unregister(2)
    gate.set()
    time.sleep(0.4)

    assert ran == []
    assert scheduler.pending() == 0
    scheduler.shutdown()


def test_a_failing_job_does_not_stop_the_others() -> None:
    scheduler = InferenceScheduler(queue_size=1, name="test-error")
    ran: list[str] = []

    def boom() -> None:
        raise RuntimeError("detector exploded")

    scheduler.submit(1, boom)
    scheduler.submit(2, lambda: ran.append("c2"))

    deadline = time.monotonic() + 5.0
    while not ran and time.monotonic() < deadline:
        time.sleep(0.02)

    stats = scheduler.stats()
    assert ran == ["c2"]
    assert stats["errors"] == 1
    scheduler.shutdown()


def test_cpu_only_jobs_need_no_device() -> None:
    """The scheduler is device-agnostic: this is the CPU-only path."""
    scheduler = InferenceScheduler(name="test-cpu")
    done = threading.Event()
    scheduler.submit(7, done.set)
    assert done.wait(3.0)
    assert scheduler.stats()["detail"]["7"]["processed"] == 1
    scheduler.shutdown()

"""Detection pipeline tests (motion -> detect -> track -> events -> alerts)."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.detector import FakeDetector, filter_detections
from app.services.pipeline import DetectionPipeline, PipelineConfig, ZoneSpec
from app.services.tracker import Detection

WIDTH, HEIGHT = 320, 180
SQUARE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
LEFT_HALF = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
RIGHT_HALF = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]


def frame(value: int = 40) -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8)


def draw(frame_array: np.ndarray, bbox: list[float], value: int = 210) -> np.ndarray:
    out = frame_array.copy()
    x1 = int(bbox[0] * WIDTH)
    y1 = int(bbox[1] * HEIGHT)
    x2 = max(x1 + 2, int(bbox[2] * WIDTH))
    y2 = max(y1 + 2, int(bbox[3] * HEIGHT))
    out[y1:y2, x1:x2] = value
    return out


def person(x: float, y: float = 0.3, w: float = 0.2, h: float = 0.4, conf: float = 0.9) -> Detection:
    return Detection(bbox=[x, y, x + w, y + h], confidence=conf, object_type="person")


def scripted(detections: list[list[Detection]]) -> FakeDetector:
    """Detector that returns detections[i] on call i (repeating the last)."""
    return FakeDetector(lambda _frame, index: detections[min(index, len(detections) - 1)])


def make_config(**overrides) -> PipelineConfig:
    base = dict(
        camera_id=1,
        camera_name="دوربین آزمایشی",
        class_config={"person": {"enabled": True, "threshold": 0.3, "min_motion": 0.0}},
        require_motion=False,
        stationary_after_seconds=0.5,
        track_max_age_seconds=0.5,
        min_track_hits=1,
        best_image_improvement=0.08,
        record_snapshots=True,
    )
    base.update(overrides)
    return PipelineConfig(**base)


def make_pipeline(detector, **overrides) -> DetectionPipeline:
    return DetectionPipeline(make_config(**overrides), detector)


# --------------------------------------------------------------------------
# entry / exit
# --------------------------------------------------------------------------
def test_enter_event_and_snapshot_on_first_frame() -> None:
    pipeline = make_pipeline(scripted([[person(0.3)]]))

    result = pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=_t(0), now=0.0)

    types = [event.event_type for event in result.events]
    assert "enter" in types
    assert len(result.snapshots) == 1
    assert result.snapshots[0].kind == "enter"
    assert result.snapshots[0].best is True
    assert result.heatmap_points
    assert result.started_tracks and result.started_tracks[0].object_type == "person"


def test_enter_event_is_emitted_only_once() -> None:
    pipeline = make_pipeline(scripted([[person(0.3)]]))
    enters = 0
    for index in range(6):
        result = pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=_t(index * 0.1), now=index * 0.1)
        enters += sum(1 for event in result.events if event.event_type == "enter")
    assert enters == 1


def test_exit_event_on_track_loss_uses_best_image() -> None:
    pipeline = make_pipeline(scripted([[person(0.3)], []]))
    pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=_t(0), now=0.0)
    pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=_t(0.1), now=0.1)

    result = pipeline.process(frame(), ts=_t(0.3), now=0.9)  # > max_age

    exits = [event for event in result.events if event.event_type == "exit"]
    assert len(exits) == 1
    assert exits[0].attach_image_kind == "best"
    assert exits[0].meta["exited_frame"] is True
    assert result.ended_tracks and result.ended_tracks[0].status == "ended"


def test_exit_image_is_captured_when_object_touches_border() -> None:
    pipeline = make_pipeline(scripted([[person(0.0, 0.3, 0.08, 0.4)]]))
    result = pipeline.process(draw(frame(), [0.0, 0.3, 0.08, 0.7]), ts=_t(0), now=0.0)
    kinds = {snapshot.kind for snapshot in result.snapshots}
    assert "exit" in kinds


def test_no_exit_event_when_nothing_was_recorded() -> None:
    pipeline = make_pipeline(scripted([[person(0.3)]]), class_config={
        "person": {"enabled": True, "threshold": 0.3, "min_motion": 0.9}
    })
    pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=_t(0), now=0.0)
    result = pipeline.process(frame(), ts=_t(0.2), now=1.0)
    assert [event.event_type for event in result.events] == []


# --------------------------------------------------------------------------
# motion state
# --------------------------------------------------------------------------
def test_stationary_object_fires_once_and_prunes_images() -> None:
    pipeline = make_pipeline(scripted([[person(0.3)]]))
    all_events: list = []
    pruned: list[int] = []
    now = 0.0
    for _ in range(12):
        result = pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=_t(now), now=now)
        all_events.extend(result.events)
        pruned.extend(result.prune_tracks)
        now += 0.1

    stationary = [event for event in all_events if event.event_type == "stationary"]
    assert len(stationary) == 1
    assert stationary[0].meta["motion_state"] == "stationary"
    assert stationary[0].direction == "stationary"
    # a static object collapses to a single stored image
    assert pruned == [stationary[0].runtime_track_id]


def test_moving_again_after_being_stationary() -> None:
    pipeline = make_pipeline(scripted([[person(0.3)]]), direction_window_seconds=0.3)
    events: list[str] = []
    now = 0.0

    # hold still long enough to be marked stationary
    for _ in range(8):
        result = pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=_t(now), now=now)
        events.extend(event.event_type for event in result.events)
        now += 0.1

    # then start walking again
    x = 0.3
    pipeline.set_detector(scripted([[person(x)]]))
    for _ in range(8):
        pipeline.set_detector(scripted([[person(x)]]))
        result = pipeline.process(draw(frame(), [x, 0.3, x + 0.2, 0.7]), ts=_t(now), now=now)
        events.extend(event.event_type for event in result.events)
        x += 0.02
        now += 0.1

    assert "stationary" in events
    assert "moving" in events


# --------------------------------------------------------------------------
# line crossing
# --------------------------------------------------------------------------
def test_line_crossing_event() -> None:
    line = ZoneSpec(id=5, name="خط ورودی", kind="line", points=[[0.5, 0.0], [0.5, 1.0]])
    pipeline = make_pipeline(scripted([[]]), zones=[line])

    events: list = []
    now = 0.0
    for index in range(12):
        x = 0.15 + index * 0.05
        pipeline.set_detector(scripted([[person(x)]]))
        result = pipeline.process(draw(frame(), [x, 0.3, x + 0.2, 0.7]), ts=_t(now), now=now)
        events.extend(event for event in result.events if event.event_type == "line_cross")
        now += 0.05

    assert len(events) == 1
    crossing = events[0]
    assert crossing.zone_id == 5
    assert crossing.meta["line_id"] == 5
    assert crossing.meta["line_side"] in {"positive", "negative"}
    assert crossing.direction == "right"


def test_line_is_only_counted_once_per_track() -> None:
    """Hopping back and forth over one tripwire must not spam events."""
    line = ZoneSpec(id=5, name="line", kind="line", points=[[0.5, 0.0], [0.5, 1.0]])
    pipeline = make_pipeline(scripted([[]]), zones=[line])
    crossing_events: list = []
    now = 0.0
    for index in range(24):
        x = 0.15 + (index * 0.05 if index < 12 else (0.70 - (index - 12) * 0.05))
        pipeline.set_detector(scripted([[person(x)]]))
        result = pipeline.process(draw(frame(), [x, 0.3, x + 0.2, 0.7]), ts=_t(now), now=now)
        crossing_events.extend(
            event for event in result.events if event.event_type == "line_cross"
        )
        now += 0.05

    assert crossing_events, "the tripwire should have fired at least once"
    track_ids = [event.runtime_track_id for event in crossing_events]
    assert len(track_ids) == len(set(track_ids)), "at most one crossing per track"


# --------------------------------------------------------------------------
# regions
# --------------------------------------------------------------------------
def test_process_region_filters_outside_detections() -> None:
    region = ZoneSpec(id=1, name="منطقه پردازش", kind="region", points=LEFT_HALF)
    pipeline = make_pipeline(scripted([[person(0.7)]]), zones=[region])
    result = pipeline.process(draw(frame(), [0.7, 0.3, 0.9, 0.7]), ts=_t(0), now=0.0)
    assert result.detections == []
    assert result.active_tracks == []


def test_process_region_keeps_inside_detections() -> None:
    region = ZoneSpec(id=1, name="منطقه پردازش", kind="region", points=LEFT_HALF)
    pipeline = make_pipeline(scripted([[person(0.1)]]), zones=[region])
    result = pipeline.process(draw(frame(), [0.1, 0.3, 0.3, 0.7]), ts=_t(0), now=0.0)
    assert len(result.detections) == 1


def test_ignore_region_excludes_detections() -> None:
    region = ZoneSpec(id=1, name="منطقه پردازش", kind="region", points=SQUARE)
    ignore = ZoneSpec(id=2, name="نادیده", kind="ignore", points=LEFT_HALF)
    pipeline = make_pipeline(scripted([[person(0.1)]]), zones=[region, ignore])
    result = pipeline.process(draw(frame(), [0.1, 0.3, 0.3, 0.7]), ts=_t(0), now=0.0)
    assert result.detections == []


def test_track_is_assigned_to_its_zone() -> None:
    region = ZoneSpec(id=9, name="ورودی", kind="region", points=LEFT_HALF)
    pipeline = make_pipeline(scripted([[person(0.1)]]), zones=[region])
    result = pipeline.process(draw(frame(), [0.1, 0.3, 0.3, 0.7]), ts=_t(0), now=0.0)
    assert result.started_tracks[0].zone_id == 9
    assert result.events[0].zone_id == 9


# --------------------------------------------------------------------------
# alerts
# --------------------------------------------------------------------------
def test_alert_fires_for_zone_rule() -> None:
    zone = ZoneSpec(
        id=3,
        name="ناحیه حساس",
        kind="alert",
        points=SQUARE,
        alert_enabled=True,
        alert_classes=["person"],
        alert_sound=True,
    )
    pipeline = make_pipeline(scripted([[person(0.3)]]), zones=[zone])
    result = pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=_t(0), now=0.0)

    assert len(result.alerts) == 1
    assert result.alerts[0].sound is True
    assert any(event.event_type == "alert" for event in result.events)


def test_alert_not_fired_for_other_class() -> None:
    zone = ZoneSpec(
        id=3, name="ناحیه", kind="alert", points=SQUARE,
        alert_enabled=True, alert_classes=["car"],
    )
    pipeline = make_pipeline(scripted([[person(0.3)]]), zones=[zone])
    result = pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=_t(0), now=0.0)
    assert result.alerts == []


# --------------------------------------------------------------------------
# detection budgeting / motion gating
# --------------------------------------------------------------------------
def test_require_motion_skips_detection_on_a_still_frame() -> None:
    detector = scripted([[person(0.3)]])
    pipeline = make_pipeline(detector, require_motion=True)
    still = frame()
    result = None
    for index in range(12):
        result = pipeline.process(still, ts=_t(index * 0.1), now=index * 0.1)
    assert result is not None
    assert result.ran_detection is False
    assert detector.calls == 0
    assert result.active_tracks == []


def test_require_motion_runs_detection_when_motion_present() -> None:
    detector = scripted([[person(0.6)]])
    pipeline = make_pipeline(detector, require_motion=True)
    for index in range(12):
        pipeline.process(frame(), ts=_t(index * 0.1), now=index * 0.1)
    result = pipeline.process(draw(frame(), [0.6, 0.3, 0.8, 0.7]), ts=_t(1.2), now=1.2)
    assert result.ran_detection is True
    assert len(result.detections) == 1


def test_detection_continues_while_tracks_are_alive() -> None:
    detector = scripted([[person(0.3)]])
    pipeline = make_pipeline(detector, require_motion=True)
    for index in range(12):
        pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=_t(index * 0.1), now=index * 0.1)
    calls_before = detector.calls
    result = pipeline.process(frame(), ts=_t(2.0), now=2.0)
    assert result.ran_detection is True
    assert detector.calls == calls_before + 1


def test_pipeline_without_detector_reports_no_detections() -> None:
    pipeline = DetectionPipeline(make_config(), None)
    result = pipeline.process(frame(), ts=_t(0), now=0.0)
    assert result.ran_detection is False
    assert result.detections == []


def test_reset_clears_tracking_state() -> None:
    pipeline = make_pipeline(scripted([[person(0.3)]]))
    pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=_t(0), now=0.0)
    assert pipeline.tracker.tracks
    pipeline.reset()
    assert pipeline.tracker.tracks == []


# --------------------------------------------------------------------------
# filtering helper
# --------------------------------------------------------------------------
def test_filter_detections_respects_class_rules() -> None:
    detections = [
        Detection([0.1, 0.1, 0.2, 0.2], 0.9, "person"),
        Detection([0.1, 0.1, 0.2, 0.2], 0.9, "car"),
    ]
    kept = filter_detections(detections, {"person": {"enabled": True, "threshold": 0.5}})
    assert [d.object_type for d in kept] == ["person"]

    kept = filter_detections(detections, {"person": {"enabled": False}})
    assert kept == []

    kept = filter_detections(
        detections, {"person": {"enabled": True, "threshold": 0.1, "min_width": 0.5}}
    )
    assert kept == []


def test_filter_detections_accepts_pydantic_rules() -> None:
    from app.schemas.camera import ClassRule

    detections = [Detection([0.1, 0.1, 0.3, 0.3], 0.9, "person")]
    kept = filter_detections(detections, {"person": ClassRule(threshold=0.5)})
    assert len(kept) == 1
    kept = filter_detections(detections, {"person": ClassRule(threshold=0.95)})
    assert kept == []


def test_filter_detections_sorts_by_confidence() -> None:
    detections = [
        Detection([0.1, 0.1, 0.2, 0.2], 0.5, "person"),
        Detection([0.3, 0.1, 0.4, 0.2], 0.99, "person"),
    ]
    kept = filter_detections(detections, {"person": {"enabled": True, "threshold": 0.1}})
    assert kept[0].confidence == pytest.approx(0.99)


def test_filter_detections_without_config_returns_all() -> None:
    detections = [Detection([0.1, 0.1, 0.2, 0.2], 0.5, "person")]
    assert filter_detections(detections, None) == detections


def _t(seconds: float):
    from datetime import datetime, timedelta, timezone

    return datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


def test_alert_schedule_uses_the_configured_timezone(monkeypatch) -> None:
    """Alert schedules are wall-clock windows in the *configured* timezone."""
    from datetime import datetime, timezone

    from app.core.config import settings

    # 19:35 UTC == 23:05 in Tehran (UTC+03:30) -> inside 22:00-23:30 local.
    ts = datetime(2026, 10, 5, 19, 35, tzinfo=timezone.utc)
    zone = ZoneSpec(
        id=3,
        name="ناحیه شبانه",
        kind="alert",
        points=SQUARE,
        alert_enabled=True,
        alert_classes=["person"],
        schedule={"enabled": True, "start": "22:00", "end": "23:30"},
    )

    pipeline = make_pipeline(scripted([[person(0.3)]]), zones=[zone])
    result = pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=ts, now=0.0)
    assert len(result.alerts) == 1, "23:05 Tehran is inside the window"

    monkeypatch.setattr(settings, "timezone", "UTC")
    pipeline_utc = make_pipeline(scripted([[person(0.3)]]), zones=[zone])
    result_utc = pipeline_utc.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=ts, now=0.0)
    assert result_utc.alerts == [], "19:35 UTC is outside the window"


def test_alert_zone_nested_in_a_region_still_fires() -> None:
    """Regression: only the first containing zone used to be considered."""
    region = ZoneSpec(id=1, name="کل کادر", kind="region", points=SQUARE)
    inner = ZoneSpec(
        id=2,
        name="ناحیه هشدار",
        kind="alert",
        points=[[0.2, 0.2], [0.6, 0.2], [0.6, 0.8], [0.2, 0.8]],
        alert_enabled=True,
        alert_classes=["person"],
    )
    pipeline = make_pipeline(scripted([[person(0.3)]]), zones=[region, inner])
    result = pipeline.process(draw(frame(), [0.3, 0.3, 0.5, 0.7]), ts=_t(0), now=0.0)

    assert len(result.alerts) == 1
    assert any(event.event_type == "alert" and event.zone_id == 2 for event in result.events)

    # An object inside the outer region but outside the alert zone must not fire.
    outside = make_pipeline(scripted([[person(0.75)]]), zones=[region, inner])
    result_outside = outside.process(draw(frame(), [0.75, 0.3, 0.95, 0.7]), ts=_t(0), now=0.0)
    assert result_outside.alerts == []


def test_moving_track_records_path_and_replay_frames() -> None:
    """The object page needs a trajectory and a series of frames, not just the
    entry/exit anchors."""
    pipeline = make_pipeline(
        scripted([[person(0.05 + index * 0.02)] for index in range(14)]),
        track_frame_interval_seconds=0.2,
        track_path_interval_seconds=0.1,
    )

    kinds: list[str] = []
    path: list[list[float]] = []
    for index in range(14):
        x = 0.05 + index * 0.02
        result = pipeline.process(
            draw(frame(), [x, 0.3, x + 0.2, 0.7]),
            ts=_t(index * 0.1),
            now=index * 0.1,
        )
        kinds.extend(snapshot.kind for snapshot in result.snapshots)
        if result.active_tracks:
            path = result.active_tracks[0].path

    assert "track" in kinds, "a moving object must store replay frames"
    assert len(path) >= 3, "the trajectory must be sampled over time"
    # Samples are [offset_seconds, cx, cy] and the offsets are monotonic.
    offsets = [point[0] for point in path]
    assert offsets == sorted(offsets)
    centres = [point[1] for point in path]
    assert centres[-1] > centres[0], "the object moved to the right"


# --------------------------------------------------------------------------
# inference input resolution
# --------------------------------------------------------------------------
def test_detection_max_width_downscales_only_the_model_input() -> None:
    """The detector gets a smaller frame; boxes stay valid on the real one."""
    seen: list[tuple[int, int]] = []

    class _SizeDetector:
        name = "size"
        available = True

        def detect(self, incoming):  # noqa: ANN001, ANN201
            seen.append(incoming.shape[:2])
            assert incoming.flags.writeable, "torch.from_numpy needs a writeable array"
            return [person(0.4)]

    pipeline = DetectionPipeline(make_config(detection_max_width=160), _SizeDetector())
    result = pipeline.process(frame(), ts=_t(0), now=0.0)

    assert seen, "the detector was never called"
    height, width = seen[0]
    assert height <= 90 and width <= 160, seen[0]
    assert width > 80, "a 320px frame must be halved, not shrunk to nothing"
    # Detection coordinates are normalised to the frame they were found in, so
    # the box is unchanged on the full-resolution frame.
    assert result.detections[0].bbox == pytest.approx([0.4, 0.3, 0.6, 0.7])


def test_read_only_frames_are_copied_before_inference() -> None:
    incoming = frame()
    incoming.setflags(write=False)
    pipeline = make_pipeline(scripted([[person(0.2)]]))
    result = pipeline.process(incoming, ts=_t(0), now=0.0)
    assert result.ran_detection is True

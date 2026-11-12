"""Tracker tests: association, birth/death, direction, stationary detection."""

from __future__ import annotations

from app.services.tracker import Detection, ObjectTracker


def _det(x1: float, y1: float, x2: float, y2: float, label: str = "person", conf: float = 0.9):
    return Detection(bbox=[x1, y1, x2, y2], confidence=conf, object_type=label)


def _make_tracker(**kwargs) -> ObjectTracker:
    defaults = {
        "iou_threshold": 0.3,
        "max_age_seconds": 1.0,
        "min_hits": 2,
        "history_seconds": 1.0,
        "direction_min_displacement": 0.01,
        "stationary_after_seconds": 2.0,
    }
    defaults.update(kwargs)
    return ObjectTracker(**defaults)


def test_track_is_created_and_confirmed_after_min_hits() -> None:
    tracker = _make_tracker()
    first = tracker.update([_det(0.1, 0.1, 0.2, 0.3)], now=0.0)
    assert len(first.born) == 1
    assert first.born[0].confirmed is False
    assert first.active == []

    second = tracker.update([_det(0.1, 0.1, 0.2, 0.3)], now=0.1)
    assert len(second.active) == 1
    track = second.active[0]
    assert track.confirmed is True
    assert track.hits == 2
    assert track.track_id == first.born[0].track_id


def test_min_hits_one_confirms_immediately() -> None:
    tracker = _make_tracker(min_hits=1)
    result = tracker.update([_det(0.1, 0.1, 0.2, 0.3)], now=0.0)
    assert len(result.born) == 1
    assert result.born[0].confirmed is True


def test_identity_is_stable_while_moving_right() -> None:
    tracker = _make_tracker(min_hits=1)
    track_id = None
    for index in range(8):
        x = 0.05 + index * 0.06
        result = tracker.update([_det(x, 0.4, x + 0.1, 0.6)], now=index * 0.1)
        track = result.active[0]
        if track_id is None:
            track_id = track.track_id
        assert track.track_id == track_id

    track = result.active[0]
    assert track.direction == "right"
    assert track.velocity[0] > 0
    assert track.distance_travelled > 0.2


def test_detects_leftward_direction() -> None:
    tracker = _make_tracker(min_hits=1, history_seconds=2.0)
    result = None
    for index in range(8):
        x = 0.7 - index * 0.06
        result = tracker.update([_det(x, 0.4, x + 0.1, 0.6)], now=index * 0.1)
    assert result is not None
    assert result.active[0].direction == "left"


def test_vertical_directions() -> None:
    tracker = _make_tracker(min_hits=1)
    result = None
    for index in range(8):
        y = 0.05 + index * 0.06
        result = tracker.update([_det(0.4, y, 0.55, y + 0.1)], now=index * 0.1)
    assert result is not None and result.active[0].direction == "down"

    tracker = _make_tracker(min_hits=1)
    for index in range(8):
        y = 0.7 - index * 0.06
        result = tracker.update([_det(0.4, y, 0.55, y + 0.1)], now=index * 0.1)
    assert result is not None and result.active[0].direction == "up"


def test_stationary_after_configured_seconds() -> None:
    tracker = _make_tracker(min_hits=1, stationary_after_seconds=1.0)
    transitions: list[float] = []
    for index in range(20):
        now = index * 0.1
        result = tracker.update([_det(0.4, 0.4, 0.6, 0.6)], now=now)
        track = result.active[0]
        if track.motion_state_changed:
            transitions.append(now)
        if now < 1.0:
            assert track.motion_state == "moving"
    assert result.active[0].motion_state == "stationary"
    # exactly one transition, at the configured threshold
    assert transitions == [1.0]


def test_stationary_to_moving_transition() -> None:
    tracker = _make_tracker(min_hits=1, stationary_after_seconds=0.5, history_seconds=0.5)
    now = 0.0
    for _ in range(10):
        tracker.update([_det(0.4, 0.4, 0.6, 0.6)], now=now)
        now += 0.1
    assert tracker.tracks[0].motion_state == "stationary"

    x = 0.4
    result = None
    for _ in range(8):
        result = tracker.update([_det(x, 0.4, x + 0.2, 0.6)], now=now)
        x += 0.03
        now += 0.1
    assert len(tracker.tracks) == 1  # identity survived the whole movement
    assert result is not None
    assert result.active[0].motion_state == "moving"


def test_large_jump_creates_a_new_track() -> None:
    """Two objects far apart must never be fused into one identity."""
    tracker = _make_tracker(min_hits=1)
    tracker.update([_det(0.02, 0.4, 0.1, 0.6)], now=0.0)
    result = tracker.update([_det(0.85, 0.4, 0.95, 0.6)], now=0.1)
    assert len(tracker.tracks) == 2
    assert len(result.born) == 1


def test_track_is_lost_after_max_age() -> None:
    tracker = _make_tracker(min_hits=1, max_age_seconds=0.5)
    tracker.update([_det(0.1, 0.1, 0.2, 0.3)], now=0.0)
    result = tracker.update([], now=0.2)
    assert result.lost == []
    result = tracker.update([], now=0.8)
    assert len(result.lost) == 1
    assert result.lost[0].track_id == 1
    assert tracker.tracks == []


def test_class_aware_association_does_not_mix_types() -> None:
    tracker = _make_tracker(min_hits=1)
    tracker.update([_det(0.1, 0.1, 0.3, 0.3, label="person")], now=0.0)
    # Same box, different class -> must create a new track, not steal the first
    result = tracker.update([_det(0.1, 0.1, 0.3, 0.3, label="car")], now=0.1)
    assert len(result.born) == 1
    assert result.born[0].object_type == "car"
    assert len(tracker.tracks) == 2


def test_two_objects_stay_separate() -> None:
    tracker = _make_tracker(min_hits=1)
    now = 0.0
    for _ in range(6):
        result = tracker.update(
            [
                _det(0.05, 0.1, 0.15, 0.3),
                _det(0.7, 0.1, 0.8, 0.3),
            ],
            now=now,
        )
        now += 0.1
    assert len(result.active) == 2
    assert len({track.track_id for track in result.active}) == 2


def test_crossing_tracks_keep_distinct_ids() -> None:
    tracker = _make_tracker(min_hits=1, iou_threshold=0.2)
    now = 0.0
    ids: set[int] = set()
    # Two objects swap sides; IoU association keeps them apart by motion.
    for index in range(10):
        left_x = 0.05 + index * 0.05
        right_x = 0.75 - index * 0.05
        result = tracker.update(
            [_det(left_x, 0.1, left_x + 0.1, 0.3), _det(right_x, 0.6, right_x + 0.1, 0.8)],
            now=now,
        )
        now += 0.1
        ids.update(track.track_id for track in result.active)
    assert len(ids) == 2


def test_reset_clears_state() -> None:
    tracker = _make_tracker(min_hits=1)
    tracker.update([_det(0.1, 0.1, 0.2, 0.3)], now=0.0)
    tracker.reset()
    assert tracker.tracks == []
    result = tracker.update([_det(0.1, 0.1, 0.2, 0.3)], now=0.0)
    assert result.born[0].track_id == 1


def test_summary_and_get() -> None:
    tracker = _make_tracker(min_hits=1)
    tracker.update([_det(0.1, 0.1, 0.2, 0.3)], now=0.0)
    assert tracker.summary()["tracks"] == 1
    assert tracker.get(1) is not None
    assert tracker.get(999) is None


def test_touches_border() -> None:
    at_edge = _make_tracker(min_hits=1).update([_det(0.001, 0.4, 0.06, 0.6)], now=0.0)
    assert at_edge.active[0].touches_border()

    in_centre = _make_tracker(min_hits=1).update([_det(0.4, 0.4, 0.6, 0.6)], now=0.0)
    assert not in_centre.active[0].touches_border()

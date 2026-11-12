"""Motion detector tests."""

from __future__ import annotations

import numpy as np

from app.services.motion import MotionDetector

WIDTH, HEIGHT = 320, 180


def _background(value: int = 30) -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), value, dtype=np.uint8)


def _with_square(frame: np.ndarray, x: int, y: int, size: int = 24, value: int = 220):
    frame = frame.copy()
    frame[y : y + size, x : x + size] = value
    return frame


def _warm(detector: MotionDetector, frames: int = 10) -> None:
    for _ in range(frames):
        detector.update(_background())


def test_first_frame_is_not_warmed_up() -> None:
    detector = MotionDetector()
    result = detector.update(_background())
    assert result.warmed_up is False
    assert result.motion is False
    assert result.ratio == 0.0


def test_static_scene_has_no_motion() -> None:
    detector = MotionDetector()
    _warm(detector)
    result = detector.update(_background())
    assert result.warmed_up is True
    assert result.motion is False
    assert result.ratio == 0.0


def test_moving_square_triggers_motion() -> None:
    detector = MotionDetector()
    _warm(detector)
    moved = detector.update(_with_square(_background(), 100, 80))
    assert moved.warmed_up
    assert moved.motion is True
    assert moved.ratio > 0.004
    assert moved.bbox is not None


def test_motion_bbox_tracks_the_object() -> None:
    detector = MotionDetector()
    _warm(detector)
    result = detector.update(_with_square(_background(), 200, 100, size=32))
    assert result.bbox is not None
    x1, y1, x2, y2 = result.bbox
    assert 0.5 < x1 < 0.75
    assert 0.4 < y1 < 0.7
    assert x2 > x1 and y2 > y1


def test_low_sensitivity_ignores_small_motion() -> None:
    detector = MotionDetector(sensitivity=0.5)
    _warm(detector)
    result = detector.update(_with_square(_background(), 60, 60, size=24))
    assert result.motion is False  # too little of the frame changed


def test_zone_motion_detection() -> None:
    detector = MotionDetector()
    left_zone = [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]
    right_zone = [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]
    zones = [(1, left_zone), (2, right_zone)]

    for _ in range(10):
        detector.update(_background(), zones)

    result = detector.update(_with_square(_background(), 30, 80), zones)
    assert result.zone_motion[1] is True
    assert result.zone_motion[2] is False
    assert result.zone_ratios[1] > result.zone_ratios[2]
    assert result.any_zone_motion() is True


def test_degenerate_zone_is_safe() -> None:
    detector = MotionDetector()
    _warm(detector)
    result = detector.update(_with_square(_background(), 30, 80), [(7, [[0.1, 0.1], [0.2, 0.2]])])
    assert result.zone_ratios[7] == 0.0
    assert result.zone_motion[7] is False


def test_resolution_change_resets_background() -> None:
    detector = MotionDetector()
    _warm(detector)
    bigger = np.full((360, 640, 3), 30, dtype=np.uint8)
    result = detector.update(bigger)
    assert result.warmed_up is False
    assert result.motion is False


def test_reset_clears_background() -> None:
    detector = MotionDetector()
    _warm(detector)
    detector.reset()
    result = detector.update(_background())
    assert result.warmed_up is False


def test_background_is_not_absorbed_by_a_large_object() -> None:
    detector = MotionDetector()
    _warm(detector)
    # Cover most of the frame: the background model must not learn it.
    huge = _with_square(_background(), 0, 0, size=170, value=220)
    first = detector.update(huge)
    second = detector.update(huge)
    assert first.motion is True
    assert second.motion is True


def test_motion_center_helper() -> None:
    detector = MotionDetector()
    _warm(detector)
    result = detector.update(_with_square(_background(), 120, 60, size=32))
    center = MotionDetector.motion_center(result)
    assert center is not None
    assert 0.3 < center[0] < 0.7

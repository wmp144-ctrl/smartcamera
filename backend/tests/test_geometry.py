"""Geometry helper tests."""

from __future__ import annotations

import pytest

from app.services.geometry import (
    box_center,
    box_center_in_polygon,
    classify_direction,
    clip_box,
    crossed_line,
    crossing_direction,
    denormalize_box,
    direction_label_fa,
    grid_cell,
    iou,
    normalize_box,
    point_in_polygon,
    polygon_area,
    segments_intersect,
    side_of_line,
)

SQUARE = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
CONCAVE = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.5, 0.5], [0.1, 0.9]]


def test_iou_identical_boxes() -> None:
    assert iou([0, 0, 1, 1], [0, 0, 1, 1]) == pytest.approx(1.0)


def test_iou_disjoint_boxes() -> None:
    assert iou([0, 0, 0.2, 0.2], [0.5, 0.5, 0.9, 0.9]) == 0.0


def test_iou_half_overlap() -> None:
    value = iou([0, 0, 0.2, 0.2], [0.1, 0, 0.3, 0.2])
    assert value == pytest.approx(1 / 3, rel=1e-6)


def test_box_center_and_area() -> None:
    assert box_center([0.0, 0.0, 0.4, 0.6]) == (0.2, 0.3)
    assert polygon_area(SQUARE) == pytest.approx(0.36)


def test_point_in_polygon_convex_and_concave() -> None:
    assert point_in_polygon((0.5, 0.5), SQUARE)
    assert not point_in_polygon((0.05, 0.5), SQUARE)
    # Inside the concave notch -> outside the polygon
    assert not point_in_polygon((0.5, 0.75), CONCAVE)
    assert point_in_polygon((0.25, 0.25), CONCAVE)


def test_point_in_polygon_degenerate() -> None:
    assert not point_in_polygon((0.5, 0.5), [[0, 0], [1, 1]])


def test_box_center_in_polygon() -> None:
    assert box_center_in_polygon([0.4, 0.4, 0.6, 0.6], SQUARE)
    assert not box_center_in_polygon([0.0, 0.0, 0.1, 0.1], SQUARE)


@pytest.mark.parametrize(
    ("dx", "dy", "expected"),
    [
        (0.1, 0.0, "right"),
        (-0.1, 0.0, "left"),
        (0.0, 0.1, "down"),
        (0.0, -0.1, "up"),
        (0.1, 0.1, "down-right"),
        (-0.1, -0.1, "up-left"),
        (0.1, -0.1, "up-right"),
        (-0.1, 0.1, "down-left"),
        (0.0, 0.0, "stationary"),
        (0.001, 0.001, "stationary"),
    ],
)
def test_classify_direction(dx, dy, expected) -> None:
    threshold = 0.01 if (dx or dy) else 0.0
    assert classify_direction(dx, dy, threshold) == expected


def test_direction_persian_label() -> None:
    assert direction_label_fa("right") == "راست"
    assert direction_label_fa("stationary") == "ثابت"
    assert direction_label_fa("unknown-key") == "unknown-key"


def test_side_of_line_and_crossing() -> None:
    # Line drawn top -> bottom; the sign of the side test follows that order.
    line = [[0.5, 0.0], [0.5, 1.0]]
    left = side_of_line((0.2, 0.5), line)
    right = side_of_line((0.8, 0.5), line)
    assert left * right < 0  # opposite sides

    assert crossed_line((0.4, 0.5), (0.6, 0.5), line)
    assert not crossed_line((0.4, 0.5), (0.45, 0.5), line)

    # Moving towards the negative side of the drawn line
    assert crossing_direction((0.4, 0.5), (0.6, 0.5), line) == "negative"
    assert crossing_direction((0.6, 0.5), (0.4, 0.5), line) == "positive"
    # No crossing -> unknown
    assert crossing_direction((0.4, 0.5), (0.45, 0.5), line) == "unknown"


def test_segments_intersect() -> None:
    assert segments_intersect((0, 0), (1, 1), (0, 1), (1, 0))
    assert not segments_intersect((0, 0), (0.2, 0.2), (0.5, 0), (0.5, 1))
    # Touching endpoints count as intersecting
    assert segments_intersect((0, 0), (1, 1), (1, 1), (2, 0))


def test_crossed_line_vertical_tripwire() -> None:
    line = [[0.5, 0.0], [0.5, 1.0]]
    assert crossed_line((0.49, 0.9), (0.51, 0.9), line)
    assert not crossed_line((0.51, 0.9), (0.52, 0.9), line)


def test_normalize_and_denormalize_roundtrip() -> None:
    box = [100.0, 50.0, 300.0, 250.0]
    norm = normalize_box(box, 1000, 500)
    assert norm == [0.1, 0.1, 0.3, 0.5]
    assert denormalize_box(norm, 1000, 500) == [100.0, 50.0, 300.0, 250.0]


def test_normalize_box_with_zero_dimensions() -> None:
    assert normalize_box([1, 2, 3, 4], 0, 0) == [0.0, 0.0, 0.0, 0.0]


def test_clip_box() -> None:
    assert clip_box([-10, -10, 2000, 2000], 1920, 1080) == [0.0, 0.0, 1920.0, 1080.0]
    assert clip_box([500, 500, 100, 100], 1920, 1080) == [100.0, 100.0, 500.0, 500.0]


def test_grid_cell_clamps() -> None:
    assert grid_cell((0.5, 0.5), 10, 10) == (5, 5)
    assert grid_cell((1.5, -0.2), 10, 10) == (9, 0)
    assert grid_cell((0.5, 0.5), 0, 0) == (0, 0)

"""Pure geometry helpers used by tracking, zones, tripwires and search.

Boxes are ``[x1, y1, x2, y2]``. Coordinates may be normalised (0..1) or pixel
space; the helpers are agnostic. All functions are dependency-free so they can
be exhaustively unit tested without OpenCV, PyTorch or a GPU.
"""

from __future__ import annotations

import math

Point = tuple[float, float]
Box = list[float] | tuple[float, float, float, float]

# 8 compass directions in *screen* space (y grows downwards).
DIRECTION_BUCKETS = (
    "right",
    "down-right",
    "down",
    "down-left",
    "left",
    "up-left",
    "up",
    "up-right",
)

STATIONARY = "stationary"


# --------------------------------------------------------------------------
# boxes
# --------------------------------------------------------------------------
def box_area(box: Box) -> float:
    x1, y1, x2, y2 = box
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def box_center(box: Box) -> Point:
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def iou(box_a: Box, box_b: Box) -> float:
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = box_area(box_a) + box_area(box_b) - inter
    return inter / union if union > 0 else 0.0


def clip_box(box: Box, width: int, height: int) -> list[float]:
    x1, y1, x2, y2 = box
    x1 = min(max(x1, 0.0), float(width))
    x2 = min(max(x2, 0.0), float(width))
    y1 = min(max(y1, 0.0), float(height))
    y2 = min(max(y2, 0.0), float(height))
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return [x1, y1, x2, y2]


def normalize_box(box: Box, width: int, height: int) -> list[float]:
    if width <= 0 or height <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        round(box[0] / width, 6),
        round(box[1] / height, 6),
        round(box[2] / width, 6),
        round(box[3] / height, 6),
    ]


def denormalize_box(box: Box, width: int, height: int) -> list[float]:
    return [box[0] * width, box[1] * height, box[2] * width, box[3] * height]


def normalize_polygon(points: list[list[float]], width: int, height: int) -> list[list[float]]:
    if width <= 0 or height <= 0:
        return [[0.0, 0.0] for _ in points]
    return [[round(p[0] / width, 6), round(p[1] / height, 6)] for p in points]


def box_size_norm(box: Box) -> tuple[float, float]:
    return (max(0.0, box[2] - box[0]), max(0.0, box[3] - box[1]))


# --------------------------------------------------------------------------
# polygons
# --------------------------------------------------------------------------
def polygon_area(points: list[list[float]]) -> float:
    """Signed-less polygon area (shoelace)."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i][0], points[i][1]
        x2, y2 = points[(i + 1) % len(points)][0], points[(i + 1) % len(points)][1]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def point_in_polygon(point: Point, polygon: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test (handles concave polygons)."""
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        intersects = (yi > y) != (yj > y)
        if intersects:
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def box_center_in_polygon(box: Box, polygon: list[list[float]]) -> bool:
    return point_in_polygon(box_center(box), polygon)


# --------------------------------------------------------------------------
# tripwire / line
# --------------------------------------------------------------------------
def side_of_line(point: Point, line: list[list[float]]) -> float:
    """Signed area of (p0 -> p1 -> point). >0 and <0 are the two sides."""
    (x0, y0), (x1, y1) = (line[0][0], line[0][1]), (line[1][0], line[1][1])
    return (x1 - x0) * (point[1] - y0) - (y1 - y0) * (point[0] - x0)


def _orientation(a: Point, b: Point, c: Point) -> int:
    value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if abs(value) < 1e-12:
        return 0
    return 1 if value > 0 else 2


def _on_segment(a: Point, b: Point, c: Point) -> bool:
    return (
        min(a[0], c[0]) - 1e-12 <= b[0] <= max(a[0], c[0]) + 1e-12
        and min(a[1], c[1]) - 1e-12 <= b[1] <= max(a[1], c[1]) + 1e-12
    )


def segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    """Standard segment-segment intersection test."""
    o1 = _orientation(p1, p2, p3)
    o2 = _orientation(p1, p2, p4)
    o3 = _orientation(p3, p4, p1)
    o4 = _orientation(p3, p4, p2)

    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(p1, p3, p2):
        return True
    if o2 == 0 and _on_segment(p1, p4, p2):
        return True
    if o3 == 0 and _on_segment(p3, p1, p4):
        return True
    if o4 == 0 and _on_segment(p3, p2, p4):
        return True
    return False


def crossed_line(prev_center: Point, cur_center: Point, line: list[list[float]]) -> bool:
    """Did the object trajectory cross the tripwire between two samples?"""
    p3 = (line[0][0], line[0][1])
    p4 = (line[1][0], line[1][1])
    return segments_intersect(prev_center, cur_center, p3, p4)


def crossing_direction(prev_center: Point, cur_center: Point, line: list[list[float]]) -> str:
    """``"positive"`` / ``"negative"``: which side of the tripwire it moved to.

    The sign convention follows the point order the user drew the line with
    (left-hand side vs right-hand side), which is what the UI displays.
    """
    before = side_of_line(prev_center, line)
    after = side_of_line(cur_center, line)
    if (before > 0) == (after > 0) and before != 0 and after != 0:
        return "unknown"  # did not actually change side
    if before == 0 and after == 0:
        return "unknown"  # travelling along the line itself
    # The destination side: ``after`` when it is off the line, otherwise the
    # opposite of the side it came from.
    if after > 0:
        return "positive"
    if after < 0:
        return "negative"
    return "negative" if before > 0 else "positive"


# --------------------------------------------------------------------------
# motion direction
# --------------------------------------------------------------------------
def classify_direction(dx: float, dy: float, threshold: float = 0.0) -> str:
    """Map a displacement to one of the 8 compass directions or ``stationary``.

    ``threshold`` is in the same units as ``dx``/``dy`` (normalised here).
    """
    magnitude = math.hypot(dx, dy)
    if magnitude <= threshold or magnitude == 0:
        return STATIONARY
    angle = math.degrees(math.atan2(dy, dx))  # -180..180, y down
    sector = int(round(angle / 45.0)) % 8
    return DIRECTION_BUCKETS[sector]


def direction_vector(direction: str) -> tuple[float, float]:
    """Unit vector for a direction name (screen space)."""
    vectors = {
        "right": (1.0, 0.0),
        "down-right": (0.7071, 0.7071),
        "down": (0.0, 1.0),
        "down-left": (-0.7071, 0.7071),
        "left": (-1.0, 0.0),
        "up-left": (-0.7071, -0.7071),
        "up": (0.0, -1.0),
        "up-right": (0.7071, -0.7071),
    }
    return vectors.get(direction, (0.0, 0.0))


def direction_label_fa(direction: str) -> str:
    """Persian label for a direction (used in reports / notifications)."""
    labels = {
        "stationary": "ثابت",
        "left": "چپ",
        "right": "راست",
        "up": "بالا",
        "down": "پایین",
        "up-left": "بالا-چپ",
        "up-right": "بالا-راست",
        "down-left": "پایین-چپ",
        "down-right": "پایین-راست",
    }
    return labels.get(direction, direction)


def grid_cell(point: Point, grid_w: int, grid_h: int) -> tuple[int, int]:
    """Map a normalised point to a grid cell, clamped to the grid."""
    if grid_w <= 0 or grid_h <= 0:
        return 0, 0
    cx = min(grid_w - 1, max(0, int(point[0] * grid_w)))
    cy = min(grid_h - 1, max(0, int(point[1] * grid_h)))
    return cx, cy

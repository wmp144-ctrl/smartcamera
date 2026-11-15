"""Draw detection boxes, labels and zone outlines on frames.

Used for the *annotated* live view (opt-in) and for enriching saved event
snapshots. Pillow only — no OpenCV dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from PIL import Image, ImageDraw

from app.services.geometry import denormalize_box
from app.services.models_registry import class_label

DEFAULT_COLORS = {
    "person": "#f97316",
    "car": "#22d3ee",
    "truck": "#a855f7",
    "bus": "#eab308",
    "motorcycle": "#ef4444",
    "bicycle": "#10b981",
}
FALLBACK_COLOR = "#38bdf8"

PALETTE = (
    "#f97316",
    "#22d3ee",
    "#a855f7",
    "#eab308",
    "#ef4444",
    "#10b981",
    "#6366f1",
    "#ec4899",
)


def color_for(object_type: str, seed: int = 0) -> str:
    if object_type in DEFAULT_COLORS:
        return DEFAULT_COLORS[object_type]
    return PALETTE[seed % len(PALETTE)]


@dataclass(slots=True)
class OverlayBox:
    bbox: list[float]  # normalised
    label: str
    confidence: float = 0.0
    track_id: int | None = None
    color: str | None = None
    direction: str | None = None
    motion_state: str | None = None


@dataclass(slots=True)
class OverlayZone:
    points: list[list[float]]
    color: str = "#22d3ee"
    name: str = ""
    kind: str = "region"


def draw_overlay(
    image: Image.Image,
    boxes: Sequence[OverlayBox] | None = None,
    zones: Sequence[OverlayZone] | None = None,
    *,
    draw_zones: bool = True,
    timestamp_label: str | None = None,
) -> Image.Image:
    """Return a copy of ``image`` with boxes/zones drawn."""
    canvas = image.copy()
    draw = ImageDraw.Draw(canvas, "RGBA")
    width, height = canvas.size
    line_width = max(2, width // 500)

    if zones and draw_zones:
        for zone in zones:
            points = [(p[0] * width, p[1] * height) for p in zone.points]
            if len(points) >= 3 and zone.kind != "line":
                draw.polygon(points, outline=zone.color, width=line_width)
                fill = zone.color
                overlay = tuple(int(fill[i : i + 2], 16) for i in (1, 3, 5)) + (40,)
                draw.polygon(points, fill=overlay)
            elif len(points) >= 2:
                draw.line(points[:2], fill=zone.color, width=line_width * 2)
            if zone.name and points:
                draw.text((points[0][0] + 4, points[0][1] + 4), zone.name, fill=zone.color)

    for index, box in enumerate(boxes or []):
        x1, y1, x2, y2 = denormalize_box(box.bbox, width, height)
        color = box.color or color_for(box.label, index)
        draw.rectangle([x1, y1, x2, y2], outline=color, width=line_width)

        suffix = ""
        if box.track_id is not None:
            suffix += f" #{box.track_id}"
        if box.confidence:
            suffix += f" {box.confidence * 100:.0f}%"
        if box.motion_state == "stationary":
            suffix += " ثابت"
        text = f"{class_label(box.label)}{suffix}"
        text_width = len(text) * 7 + 10
        text_height = 16
        top = max(0, y1 - text_height)
        draw.rectangle([x1, top, x1 + text_width, top + text_height], fill=color)
        draw.text((x1 + 5, top + 2), text, fill="#07131a")

    if timestamp_label:
        draw.rectangle([0, 0, len(timestamp_label) * 7 + 12, 20], fill=(0, 0, 0, 140))
        draw.text((6, 3), timestamp_label, fill="#e2e8f0")

    return canvas

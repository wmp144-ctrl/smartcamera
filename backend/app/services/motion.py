"""Motion detection by running-average background subtraction.

This is deliberately separate from object detection: the pipeline only runs
RF-DETR on frames where motion was detected inside a process region, which is
what keeps a 5 fps detection budget viable for many cameras on one GPU.

Pure numpy — no OpenCV required — so it is fully unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services.geometry import box_center, point_in_polygon
from app.services.quality import to_gray

DEFAULT_PIXEL_THRESHOLD = 25.0  # luma delta that counts as "changed"


@dataclass(slots=True)
class MotionResult:
    motion: bool
    ratio: float  # fraction of the frame that changed
    bbox: list[float] | None = None  # normalised bounding box of the motion
    zone_ratios: dict[int, float] = field(default_factory=dict)
    zone_motion: dict[int, bool] = field(default_factory=dict)
    warmed_up: bool = True

    def any_zone_motion(self) -> bool:
        return any(self.zone_motion.values()) if self.zone_motion else self.motion


class MotionDetector:
    """Per-camera motion detector.

    Args:
        sensitivity: minimum fraction of changed pixels to call it motion.
        pixel_threshold: per-pixel luma delta that counts as changed.
        background_alpha: running-average background learning rate.
        warmup_frames: frames used to build the initial background.
        downscale: integer stride used to speed up the analysis.
    """

    def __init__(
        self,
        *,
        sensitivity: float = 0.004,
        pixel_threshold: float = DEFAULT_PIXEL_THRESHOLD,
        background_alpha: float = 0.08,
        warmup_frames: int = 8,
        downscale: int = 4,
        min_pixels: int = 12,
    ) -> None:
        self.sensitivity = float(sensitivity)
        self.pixel_threshold = float(pixel_threshold)
        self.background_alpha = float(background_alpha)
        self.warmup_frames = int(warmup_frames)
        self.downscale = max(1, int(downscale))
        self.min_pixels = int(min_pixels)

        self._background: np.ndarray | None = None
        self._frames = 0
        self._mask_cache: dict[tuple, np.ndarray] = {}

    def reset(self) -> None:
        self._background = None
        self._frames = 0
        self._mask_cache.clear()

    # ------------------------------------------------------------------
    def _gray_small(self, frame: np.ndarray) -> np.ndarray:
        """Downscale *before* the gray conversion.

        ``to_gray`` is the expensive half (float32 math over every pixel): on a
        1080p frame it costs ~19 ms, and 15/16 of the result was then thrown
        away by the stride. Striding first measures the same thing ~10x
        cheaper (21.9 ms -> 2.2 ms for the whole update).
        """
        array = np.asarray(frame)
        if self.downscale > 1 and array.ndim >= 2:
            array = array[:: self.downscale, :: self.downscale]
        return to_gray(array)

    def update(
        self,
        frame: np.ndarray,
        zones: list[tuple[int, list[list[float]]]] | None = None,
    ) -> MotionResult:
        """Analyse a frame.

        ``zones`` is an optional list of ``(zone_id, normalised_polygon)`` pairs;
        per-zone motion ratios are reported for the zones whose centroid falls
        inside the changed region.
        """
        small = self._gray_small(frame)
        self._frames += 1

        if self._background is None:
            self._background = small.copy()
            return MotionResult(motion=False, ratio=0.0, warmed_up=False)

        if self._background.shape != small.shape:  # resolution changed
            self._background = small.copy()
            return MotionResult(motion=False, ratio=0.0, warmed_up=False)

        diff = np.abs(small - self._background)
        mask = diff > self.pixel_threshold

        # Keep learning the background, but never absorb a large moving object.
        changed_ratio = float(mask.mean())
        if changed_ratio < 0.35:
            self._background = (
                self.background_alpha * small + (1.0 - self.background_alpha) * self._background
            )

        warmed_up = self._frames > self.warmup_frames
        changed = int(mask.sum())
        if changed < self.min_pixels:
            mask = np.zeros_like(mask)
            changed = 0
            changed_ratio = 0.0

        bbox: list[float] | None = None
        if changed:
            ys, xs = np.nonzero(mask)
            h, w = mask.shape
            bbox = [
                float(xs.min() / w),
                float(ys.min() / h),
                float((xs.max() + 1) / w),
                float((ys.max() + 1) / h),
            ]

        zone_ratios: dict[int, float] = {}
        zone_motion: dict[int, bool] = {}
        if zones:
            zone_ratios, zone_motion = self._zone_stats(mask, zones)

        motion = warmed_up and changed_ratio >= self.sensitivity
        return MotionResult(
            motion=motion,
            ratio=round(changed_ratio, 6),
            bbox=bbox,
            zone_ratios=zone_ratios,
            zone_motion=zone_motion,
            warmed_up=warmed_up,
        )

    def _rasterize(self, polygon: list[list[float]], shape: tuple[int, int]) -> np.ndarray:
        """Scanline-rasterise a normalised polygon onto the mask grid (cached)."""
        key = (shape, tuple((round(p[0], 5), round(p[1], 5)) for p in polygon))
        cached = self._mask_cache.get(key)
        if cached is not None:
            return cached

        h, w = shape
        inside = np.zeros(shape, dtype=bool)
        xs = np.arange(w) / w + 0.5 / w
        n = len(polygon)
        for gy in range(h):
            row_y = gy / h + 0.5 / h
            crossings: list[float] = []
            for i in range(n):
                x1, y1 = polygon[i][0], polygon[i][1]
                x2, y2 = polygon[(i + 1) % n][0], polygon[(i + 1) % n][1]
                if (y1 > row_y) != (y2 > row_y):
                    t = (row_y - y1) / (y2 - y1)
                    crossings.append(x1 + t * (x2 - x1))
            if len(crossings) < 2:
                continue
            crossings.sort()
            for i in range(0, len(crossings) - 1, 2):
                idx = np.where((xs >= crossings[i]) & (xs <= crossings[i + 1]))[0]
                if idx.size:
                    inside[gy, idx] = True

        if len(self._mask_cache) > 256:  # bound the cache
            self._mask_cache.clear()
        self._mask_cache[key] = inside
        return inside

    def _zone_stats(
        self, mask: np.ndarray, zones: list[tuple[int, list[list[float]]]]
    ) -> tuple[dict[int, float], dict[int, bool]]:
        ratios: dict[int, float] = {}
        flags: dict[int, bool] = {}
        for zone_id, polygon in zones:
            if len(polygon) < 3:
                ratios[zone_id] = 0.0
                flags[zone_id] = False
                continue
            inside = self._rasterize(polygon, mask.shape)
            total = int(inside.sum())
            if total == 0:
                ratios[zone_id] = 0.0
                flags[zone_id] = False
                continue
            changed_in_zone = int(np.count_nonzero(mask & inside))
            ratio = changed_in_zone / total
            ratios[zone_id] = round(ratio, 6)
            flags[zone_id] = ratio >= self.sensitivity
        return ratios, flags

    def is_in_any_zone(
        self, point: tuple[float, float], zones: list[tuple[int, list[list[float]]]]
    ) -> int | None:
        for zone_id, polygon in zones:
            if polygon and point_in_polygon(point, polygon):
                return zone_id
        return None

    @staticmethod
    def motion_center(result: MotionResult) -> tuple[float, float] | None:
        if result.bbox is None:
            return None
        return box_center(result.bbox)

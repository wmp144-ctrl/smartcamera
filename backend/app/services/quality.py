"""Frame quality scoring.

Instead of blindly keeping the *first* frame of a track we score every frame
and keep the best one. The score blends:

* **sharpness** — variance of the Laplacian over the object crop
* **size** — how much of the frame the object covers (bigger = more detail)
* **confidence** — the detector's confidence for that frame
* **brightness** — frames that are far too dark/bright are penalised

The Laplacian is implemented with plain numpy slices (no OpenCV) so scoring is
fast, deterministic and unit-testable without the AI stack installed.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

# Empirical constants tuned for 1080p IP-camera imagery.
_SHARPNESS_REFERENCE = 500.0
_TARGET_AREA = 0.12  # a bounding box covering 12% of the frame is "ideal"

DEFAULT_WEIGHTS: dict[str, float] = {
    "sharpness": 0.45,
    "size": 0.25,
    "confidence": 0.20,
    "brightness": 0.10,
}


@dataclass(slots=True)
class QualityReport:
    sharpness: float
    brightness: float
    contrast: float
    size: float
    confidence: float
    score: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def to_gray(frame: np.ndarray) -> np.ndarray:
    """Convert a BGR/RGB uint8 frame (or pass through a 2-D array) to float gray."""
    array = np.asarray(frame)
    if array.ndim == 2:
        gray = array
    elif array.ndim == 3:
        if array.shape[2] == 1:
            gray = array[:, :, 0]
        else:
            # Weights work for both RGB and BGR because luma weights are
            # symmetric for R/B in practice at this precision.
            gray = (
                array[:, :, 0].astype(np.float32) * 0.114
                + array[:, :, 1].astype(np.float32) * 0.587
                + array[:, :, 2].astype(np.float32) * 0.299
            )
    else:
        raise ValueError(f"unsupported frame shape {array.shape}")
    return gray.astype(np.float32)


def laplacian_variance(gray: np.ndarray) -> float:
    """Variance of the 4-neighbour Laplacian — a standard blur/sharpness metric."""
    g = np.asarray(gray, dtype=np.float32)
    if g.ndim != 2:
        g = to_gray(g)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return 0.0
    lap = (
        -4.0 * g[1:-1, 1:-1]
        + g[:-2, 1:-1]
        + g[2:, 1:-1]
        + g[1:-1, :-2]
        + g[1:-1, 2:]
    )
    return float(lap.var())


def brightness(gray: np.ndarray) -> float:
    """Mean luma normalised to 0..1."""
    g = np.asarray(gray, dtype=np.float32)
    if g.size == 0:
        return 0.0
    return float(np.clip(g.mean() / 255.0, 0.0, 1.0))


def contrast(gray: np.ndarray) -> float:
    """Standard deviation of luma normalised to 0..1."""
    g = np.asarray(gray, dtype=np.float32)
    if g.size == 0:
        return 0.0
    return float(np.clip(g.std() / 128.0, 0.0, 1.0))


def crop(frame: np.ndarray, box_px: list[float] | tuple[float, ...]) -> np.ndarray:
    """Clip a pixel box to the frame and return the sub-image (never empty)."""
    array = np.asarray(frame)
    h, w = array.shape[:2]
    x1 = int(max(0, min(round(box_px[0]), w - 1)))
    y1 = int(max(0, min(round(box_px[1]), h - 1)))
    x2 = int(max(x1 + 1, min(round(box_px[2]), w)))
    y2 = int(max(y1 + 1, min(round(box_px[3]), h)))
    return array[y1:y2, x1:x2]


def _normalise_sharpness(sharpness: float) -> float:
    if sharpness <= 0:
        return 0.0
    return float(min(1.0, math.log1p(sharpness) / math.log1p(_SHARPNESS_REFERENCE)))


def _brightness_score(value: float) -> float:
    """1.0 at mid-luma, decaying towards the extremes (all-black / blown-out)."""
    return float(max(0.0, 1.0 - abs(value - 0.5) * 2.0))


def _size_score(area_norm: float) -> float:
    if area_norm <= 0:
        return 0.0
    return float(min(1.0, math.sqrt(area_norm / _TARGET_AREA)))


def quality_score(
    *,
    sharpness: float,
    brightness_value: float,
    area_norm: float,
    confidence: float,
    weights: dict[str, float] | None = None,
) -> float:
    """Combine the sub-metrics into a single 0..1 score."""
    weights = weights or DEFAULT_WEIGHTS
    total_weight = sum(weights.values()) or 1.0
    score = (
        _normalise_sharpness(sharpness) * weights.get("sharpness", 0.0)
        + _size_score(area_norm) * weights.get("size", 0.0)
        + float(np.clip(confidence, 0.0, 1.0)) * weights.get("confidence", 0.0)
        + _brightness_score(brightness_value) * weights.get("brightness", 0.0)
    )
    return float(np.clip(score / total_weight, 0.0, 1.0))


def evaluate_frame(
    frame: np.ndarray,
    box_px: list[float] | tuple[float, ...],
    confidence: float,
    *,
    frame_area: float | None = None,
    weights: dict[str, float] | None = None,
) -> QualityReport:
    """Score a single detection within a frame."""
    patch = crop(frame, box_px)
    gray = to_gray(patch)
    sharp = laplacian_variance(gray)
    bright = brightness(gray)
    cont = contrast(gray)

    x1, y1, x2, y2 = box_px
    area = max(0.0, (x2 - x1)) * max(0.0, (y2 - y1))
    total = frame_area
    if total is None:
        h, w = np.asarray(frame).shape[:2]
        total = float(h * w) or 1.0
    area_norm = area / total if total else 0.0

    score = quality_score(
        sharpness=sharp,
        brightness_value=bright,
        area_norm=area_norm,
        confidence=confidence,
        weights=weights,
    )
    return QualityReport(
        sharpness=round(sharp, 3),
        brightness=round(bright, 4),
        contrast=round(cont, 4),
        size=round(area_norm, 6),
        confidence=round(float(confidence), 4),
        score=round(score, 4),
    )

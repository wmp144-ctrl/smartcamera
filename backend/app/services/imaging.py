"""Pixel-level helpers shared by the decoder, the pipeline and the live view.

The one rule this module enforces: a width limit is a *width*, not a hint for a
stride. ``array[::step, ::step]`` was used to shrink frames, and because the
stride had to be an integer a 1920 px camera with a 1280 limit became 960 px
(the stride rounded to 2) while a 4K camera hit the limit exactly — the
detector silently saw a different picture depending on the camera model, with
nearest-neighbour aliasing on top (thin objects disappear from a subsampled
frame, which is exactly what a detector is looking for).
"""

from __future__ import annotations

import logging
import math

import numpy as np

logger = logging.getLogger(__name__)


def fit_width(array: np.ndarray, max_width: int) -> np.ndarray:
    """Downscale *array* to at most ``max_width`` px, keeping the aspect ratio.

    Returns the array unchanged when it is already narrow enough, when no limit
    is set, or when it is not a 2-D/3-D image. Area interpolation is used
    because it averages the pixels being merged instead of dropping them.
    """
    array = np.asarray(array)
    if max_width <= 0 or array.ndim < 2:
        return array
    height, width = array.shape[:2]
    if width <= max_width or height < 1:
        return array
    target_height = max(1, round(height * (max_width / width)))
    try:
        import cv2  # noqa: PLC0415 - OpenCV is optional at import time

        resized = cv2.resize(
            array, (int(max_width), target_height), interpolation=cv2.INTER_AREA
        )
    except Exception:  # noqa: BLE001 - no OpenCV: fall back to Pillow
        # A stride fallback reintroduced the very bug this module exists for
        # (1920 px with a 1280 limit became 960 px) and aliased thin objects
        # away; Pillow is already a hard dependency, so use it for an exact,
        # aspect-preserving resize instead.
        try:
            from PIL import Image  # noqa: PLC0415

            image = Image.fromarray(array)
            resized = np.asarray(
                image.resize((int(max_width), target_height), Image.BILINEAR)
            )
        except Exception:  # noqa: BLE001 - last resort: nearest-neighbour stride
            step = max(1, math.ceil(width / max_width))
            resized = array[::step, ::step]
            logger.debug("no cv2/Pillow; downscaled with a stride of %d", step)
    return np.ascontiguousarray(resized)

"""Frame quality scoring tests."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.quality import (
    QualityReport,
    brightness,
    contrast,
    crop,
    evaluate_frame,
    laplacian_variance,
    quality_score,
    to_gray,
)


def _frame(value: int = 120, size: tuple[int, int] = (200, 320)) -> np.ndarray:
    height, width = size
    return np.full((height, width, 3), value, dtype=np.uint8)


def _checkerboard(size: tuple[int, int] = (200, 320), block: int = 4) -> np.ndarray:
    height, width = size
    y, x = np.indices((height, width))
    pattern = (((x // block) + (y // block)) % 2).astype(np.uint8) * 255
    return np.stack([pattern] * 3, axis=-1)


def test_to_gray_shapes() -> None:
    assert to_gray(_frame()).shape == (200, 320)
    gray = np.zeros((10, 10), dtype=np.uint8)
    assert to_gray(gray).shape == (10, 10)
    single = np.zeros((10, 10, 1), dtype=np.uint8)
    assert to_gray(single).shape == (10, 10)


def test_to_gray_rejects_bad_shape() -> None:
    with pytest.raises(ValueError):
        to_gray(np.zeros((2, 2, 2, 2), dtype=np.uint8))


def test_flat_image_has_no_sharpness() -> None:
    assert laplacian_variance(to_gray(_frame())) == pytest.approx(0.0, abs=1e-6)


def test_high_frequency_image_has_sharpness() -> None:
    sharp = laplacian_variance(to_gray(_checkerboard()))
    assert sharp > 1000


def test_small_patch_returns_zero_sharpness() -> None:
    assert laplacian_variance(np.zeros((2, 2), dtype=np.float32)) == 0.0


def test_brightness_and_contrast_bounds() -> None:
    assert brightness(to_gray(_frame(0))) == 0.0
    assert brightness(to_gray(_frame(255))) == pytest.approx(1.0)
    assert brightness(to_gray(_frame(128))) == pytest.approx(128 / 255, rel=1e-3)
    assert brightness(np.zeros((0, 0), dtype=np.float32)) == 0.0
    assert contrast(to_gray(_frame())) == 0.0
    assert contrast(to_gray(_checkerboard())) > 0.4


def test_crop_is_clamped_to_frame() -> None:
    frame = _frame()
    patch = crop(frame, [-50, -50, 500, 500])
    assert patch.shape[0] == 200 and patch.shape[1] == 320
    patch = crop(frame, [10, 10, 20, 20])
    assert patch.shape[:2] == (10, 10)


def test_quality_score_is_monotonic_in_sharpness() -> None:
    blurry = quality_score(
        sharpness=1.0, brightness_value=0.5, area_norm=0.1, confidence=0.9
    )
    sharp = quality_score(
        sharpness=800.0, brightness_value=0.5, area_norm=0.1, confidence=0.9
    )
    assert sharp > blurry


def test_quality_score_bounds() -> None:
    assert 0.0 <= quality_score(
        sharpness=0, brightness_value=0, area_norm=0, confidence=0
    ) <= 1.0
    assert quality_score(
        sharpness=100000, brightness_value=0.5, area_norm=1.0, confidence=1.0
    ) <= 1.0


def test_brightness_score_penalises_extremes() -> None:
    dark = quality_score(sharpness=200, brightness_value=0.02, area_norm=0.1, confidence=0.8)
    mid = quality_score(sharpness=200, brightness_value=0.5, area_norm=0.1, confidence=0.8)
    blown = quality_score(sharpness=200, brightness_value=0.99, area_norm=0.1, confidence=0.8)
    assert mid > dark and mid > blown


def test_evaluate_frame_returns_report() -> None:
    frame = _checkerboard()
    report = evaluate_frame(frame, [40, 40, 160, 160], 0.91)
    assert isinstance(report, QualityReport)
    assert 0.0 <= report.score <= 1.0
    assert report.confidence == pytest.approx(0.91)
    assert report.size == pytest.approx((120 * 120) / (200 * 320), rel=1e-3)
    assert set(report.as_dict()) == {
        "sharpness",
        "brightness",
        "contrast",
        "size",
        "confidence",
        "score",
    }


def test_sharper_frame_scores_higher_for_same_box() -> None:
    blurry = evaluate_frame(_frame(100), [40, 40, 160, 160], 0.9)
    sharp = evaluate_frame(_checkerboard(), [40, 40, 160, 160], 0.9)
    assert sharp.score > blurry.score

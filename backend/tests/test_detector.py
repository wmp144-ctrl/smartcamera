"""Detector lifecycle: a failed load must stay failed and quiet.

Regression tests for the case where the optional AI stack (torch / rfdetr) is
not installed. The streaming path used to call ``detect()`` on every detection
frame, which retried the model load each time: one missing dependency produced a
warning per frame plus a repeated failed import.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from app.services.detector import NullDetector, RFDetrDetector, build_detector


def frame() -> np.ndarray:
    return np.zeros((64, 64, 3), dtype=np.uint8)


def test_detect_does_not_retry_a_failed_load(monkeypatch) -> None:
    detector = RFDetrDetector("rf-detr-nano")
    attempts = {"count": 0}

    def fake_load() -> bool:
        attempts["count"] += 1
        return False

    monkeypatch.setattr(detector, "load", fake_load)

    for _ in range(5):
        assert detector.detect(frame()) == []

    assert attempts["count"] == 0, "detect() must not attempt a load per frame"


def test_load_warns_once_per_reason(monkeypatch, caplog) -> None:
    detector = RFDetrDetector("rf-detr-nano")

    def boom() -> None:
        raise ModuleNotFoundError("No module named 'rfdetr'")

    monkeypatch.setattr(detector, "_build_model", boom)

    with caplog.at_level(logging.WARNING, logger="app.services.detector"):
        for _ in range(4):
            assert detector.load() is False

    warnings = [r for r in caplog.records if "RF-DETR unavailable" in r.getMessage()]
    assert len(warnings) == 1, f"expected a single warning, got {len(warnings)}"
    assert detector.available is False
    assert "rfdetr" in (detector.load_error or "")


def test_build_detector_falls_back_to_null(monkeypatch) -> None:
    def boom(self) -> None:  # noqa: ARG001 - replaces a bound method
        raise ModuleNotFoundError("No module named 'rfdetr'")

    monkeypatch.setattr(RFDetrDetector, "_build_model", boom)

    detector = build_detector("rf-detr-nano")

    assert isinstance(detector, NullDetector)
    assert detector.available is False
    assert detector.detect(frame()) == []
    assert "rfdetr" in detector.reason


@pytest.mark.parametrize("model_key", ["rf-detr-nano", "rf-detr-small", "rf-detr-base"])
def test_unsupported_models_are_reported_not_raised(monkeypatch, model_key: str) -> None:
    def boom(self) -> None:  # noqa: ARG001 - replaces a bound method
        raise ModuleNotFoundError("No module named 'rfdetr'")

    monkeypatch.setattr(RFDetrDetector, "_build_model", boom)

    detector = RFDetrDetector(model_key)
    assert detector.load() is False
    assert detector.load_error


def test_registry_preload_keeps_the_weights_resident(monkeypatch) -> None:
    """``preload`` loads off-loop and a later ``acquire`` must not reload."""
    from app.services.detector import DetectorRegistry, RFDetrDetector

    builds = {"count": 0}

    def fake_build(self):  # noqa: ANN001, ARG001
        builds["count"] += 1
        return object()

    monkeypatch.setattr(RFDetrDetector, "_build_model", fake_build)

    registry = DetectorRegistry()
    registry.preload("rf-detr-base", device="cpu")
    assert builds["count"] == 1
    assert registry.loaded()[0]["refs"] == 0, "preload holds no reference"

    detector = registry.acquire("rf-detr-base", device="cpu")
    assert detector.available is True
    assert builds["count"] == 1, "acquire reused the preloaded model"

    # A plain release unloads once nothing uses the model (frees the VRAM).
    registry.release(detector)
    assert registry.loaded() == []

    registry.clear()

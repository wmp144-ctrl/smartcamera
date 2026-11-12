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

from app.services.detector import (
    NullDetector,
    RFDetrDetector,
    build_detector,
    parse_raw_output,
)


def frame() -> np.ndarray:
    return np.zeros((64, 64, 3), dtype=np.uint8)


class _StubDetections:
    """Shape of ``supervision.Detections`` as returned by ``rfdetr.predict``.

    Pretrained COCO checkpoints report category ids (person = 1, bus = 6) and
    attach the resolved names in ``data['class_name']``.
    """

    def __init__(self, boxes, scores, class_ids, names) -> None:
        self.xyxy = np.asarray(boxes, dtype=float)
        self.confidence = np.asarray(scores, dtype=float)
        self.class_id = np.asarray(class_ids, dtype=int)
        self.data = {"class_name": np.asarray(names)}


def test_parse_raw_output_prefers_reported_class_names() -> None:
    raw = _StubDetections(
        boxes=[[10, 10, 50, 90], [60, 40, 140, 120]],
        scores=[0.95, 0.88],
        class_ids=[1, 6],
        names=["person", "bus"],
    )
    detections = parse_raw_output(raw, 200, 200)
    assert [item.object_type for item in detections] == ["person", "bus"]
    # Boxes stay normalised to the frame they came from.
    assert detections[0].bbox == pytest.approx([0.05, 0.05, 0.25, 0.45])


def test_parse_raw_output_falls_back_to_zero_based_ids() -> None:
    raw = _StubDetections(
        boxes=[[0, 0, 10, 10]],
        scores=[0.9],
        class_ids=[1],
        names=[""],  # no resolved name -> numeric fallback (0-based list)
    )
    detections = parse_raw_output(raw, 10, 10)
    assert [item.object_type for item in detections] == ["bicycle"]


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

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
    wants_half_precision,
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


def test_detector_uses_the_checkpoint_installed_with_the_app(tmp_path, monkeypatch) -> None:
    """An offline install must never fall back to downloading the checkpoint."""
    import app.services.detector as detector_module

    monkeypatch.setattr(
        detector_module, "settings", type("S", (), {"models_dir": tmp_path})(), raising=False
    )
    detector = RFDetrDetector("rf-detr-base")
    assert detector.weights_path is None

    weights = tmp_path / "rf-detr-base.pth"
    weights.write_bytes(b"not-a-real-checkpoint")

    detector = RFDetrDetector("rf-detr-base")
    assert detector.weights_path == weights

    # An explicit per-camera path still wins over the installed checkpoint.
    explicit = tmp_path / "custom.pth"
    explicit.write_bytes(b"x")
    assert RFDetrDetector("rf-detr-base", weights_path=explicit).weights_path == explicit


# --------------------------------------------------------------------------
# precision / device scaling
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("choice", "cuda", "name", "capability", "expected"),
    [
        # Ampere workstation card with Tensor Cores: FP16 is the fast path.
        ("auto", True, "NVIDIA RTX A2000", (8, 6), True),
        ("auto", True, "NVIDIA GeForce RTX 3060", (8, 6), True),
        # GTX 16xx has Turing capability but no Tensor Cores: FP16 was 3.6x
        # slower when measured on that card, so it must stay on FP32.
        ("auto", True, "NVIDIA GeForce GTX 1650", (7, 5), False),
        # Pascal and older, unknown capability, and CPU: never FP16.
        ("auto", True, "NVIDIA GeForce GTX 1050", (6, 1), False),
        ("auto", True, "Some GPU", None, False),
        ("auto", False, "NVIDIA RTX A2000", (8, 6), False),
        # Forced choices win over the heuristic.
        ("on", True, "NVIDIA GeForce GTX 1650", (7, 5), True),
        ("off", True, "NVIDIA RTX A2000", (8, 6), False),
    ],
)
def test_wants_half_precision(choice, cuda, name, capability, expected) -> None:
    assert wants_half_precision(choice, cuda, name, capability) is expected


def test_optimize_is_skipped_when_disabled(monkeypatch) -> None:
    """``optimize_for_inference=false`` must not touch the model at all."""
    import app.services.detector as detector_module

    class _Model:
        calls = 0

        def inference(self, **kwargs):  # noqa: ANN003, ANN201
            self.calls += 1

    monkeypatch.setattr(
        detector_module,
        "settings",
        type("S", (), {"optimize_for_inference": False})(),
        raising=False,
    )
    model = _Model()
    RFDetrDetector("rf-detr-nano")._optimize(model)
    assert model.calls == 0


def test_measured_latency_scales_the_detection_budget() -> None:
    """An RTX card must not be budgeted like the reference GTX 1650."""
    from app.services import models_registry as registry_module

    registry_module.reset_speed_factor()
    try:
        # 45 ms/frame for base = ~22 fps, roughly twice the 9 fps baseline.
        factor = registry_module.note_measured_latency("rf-detr-base", 45.0)
        assert factor == pytest.approx(22.22 / 9.0, rel=0.02)
        assert registry_module.detection_capacity("rf-detr-base") > 18
        # Two cameras at 5 fps now fit Base instead of being pushed to Small.
        assert registry_module.suggest_model(2, 5.0) == "rf-detr-base"
        assert registry_module.suggest_model(4, 5.0) == "rf-detr-small"

        # A slower-than-reference measurement scales the other way.
        registry_module.note_measured_latency("rf-detr-base", 400.0)
        assert registry_module.detection_capacity("rf-detr-base") < 9
    finally:
        registry_module.reset_speed_factor()
    assert registry_module.detection_capacity("rf-detr-base") == 9

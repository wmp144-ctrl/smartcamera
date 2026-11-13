"""Object detection.

``RFDetrDetector`` wraps Roboflow's RF-DETR. All heavyweight imports (torch,
rfdetr, supervision) happen lazily inside :meth:`RFDetrDetector.load` so the
API, the database layer and the whole test-suite work on a machine that does
not have the GPU stack installed.

The class does *no* per-camera filtering: it returns raw detections above a low
floor threshold. Per-camera class rules are applied by
:func:`filter_detections`, which lets one model be **shared** between cameras
while every camera keeps its own thresholds.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

import numpy as np

from app.core.config import settings
from app.services.geometry import box_size_norm
from app.services.models_registry import (
    COCO_CLASSES,
    ModelSpec,
    get_model_spec,
)
from app.services.tracker import Detection

logger = logging.getLogger(__name__)

# Detections below this are dropped before per-camera filtering so the shared
# model output stays small.
FLOOR_THRESHOLD = 0.05


@runtime_checkable
class Detector(Protocol):
    """Anything that can turn a frame into detections."""

    name: str
    available: bool

    def detect(self, frame: np.ndarray) -> list[Detection]: ...


# --------------------------------------------------------------------------
# filtering
# --------------------------------------------------------------------------
def filter_detections(
    detections: Sequence[Detection],
    class_config: dict[str, Any] | None,
    *,
    max_detections: int = 100,
) -> list[Detection]:
    """Apply a camera's per-class rules (enabled / threshold / min size)."""
    if class_config is None:
        return list(detections)

    kept: list[Detection] = []
    for det in detections:
        rule = class_config.get(det.object_type)
        if rule is None:
            continue
        if isinstance(rule, dict):
            enabled = bool(rule.get("enabled", True))
            threshold = float(rule.get("threshold", 0.3))
            min_w = float(rule.get("min_width", 0.0) or 0.0)
            min_h = float(rule.get("min_height", 0.0) or 0.0)
        else:  # pydantic ClassRule
            enabled = bool(getattr(rule, "enabled", True))
            threshold = float(getattr(rule, "threshold", 0.3))
            min_w = float(getattr(rule, "min_width", 0.0) or 0.0)
            min_h = float(getattr(rule, "min_height", 0.0) or 0.0)

        if not enabled or det.confidence < threshold:
            continue
        width, height = box_size_norm(det.bbox)
        if width < min_w or height < min_h:
            continue
        kept.append(det)

    kept.sort(key=lambda d: d.confidence, reverse=True)
    return kept[:max_detections]


# --------------------------------------------------------------------------
# real detector
# --------------------------------------------------------------------------
class RFDetrDetector:
    """RF-DETR wrapper. Loads weights lazily and is safe to share between threads."""

    def __init__(
        self,
        model_key: str = "rf-detr-base",
        *,
        weights_path: str | Path | None = None,
        device: str | None = None,
        spec: ModelSpec | None = None,
    ) -> None:
        self.spec = spec or get_model_spec(model_key)
        self.model_key = self.spec.key
        self.weights_path = Path(weights_path) if weights_path else self._installed_weights()
        self.device = device
        self.name = f"rfdetr:{self.model_key}"
        self._model: Any = None
        self._lock = threading.RLock()
        self._load_error: str | None = None
        self._reported_error: str | None = None

    def _installed_weights(self) -> Path | None:
        """Checkpoint installed with the app (``backend/data/models``).

        The offline installer drops the .pth files there. Without this lookup
        the rfdetr library would look in its own user cache and try to download
        the checkpoint, which fails on a machine without internet.
        """
        try:
            candidate = settings.models_dir / self.spec.weights_filename
            if candidate.is_file():
                return candidate
        except Exception:  # noqa: BLE001 - a missing config must not break detection
            logger.debug("could not resolve the bundled weights path", exc_info=True)
        return None

    # -- lifecycle ---------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def load(self) -> bool:
        """Import torch/rfdetr and build the model. Returns success."""
        with self._lock:
            if self._model is not None:
                return True
            try:
                model = self._build_model()
            except Exception as exc:  # noqa: BLE001 - report, never crash the API
                self._load_error = f"{type(exc).__name__}: {exc}"
                # Warn once per distinct reason, not once per retry.
                if self._load_error != self._reported_error:
                    self._reported_error = self._load_error
                    logger.warning("RF-DETR unavailable (%s): %s", self.model_key, exc)
                return False
            self._model = model
            self._load_error = None
            return True

    def _build_model(self) -> Any:
        import rfdetr  # noqa: PLC0415 - optional heavy dependency

        class_map = {
            "rf-detr-nano": ("RFDETRNano", {"resolution": self.spec.resolution}),
            "rf-detr-small": ("RFDETRSmall", {"resolution": self.spec.resolution}),
            "rf-detr-base": ("RFDETRBase", {"resolution": self.spec.resolution}),
            "rf-detr-medium": ("RFDETRMedium", {"resolution": self.spec.resolution}),
            "rf-detr-large": ("RFDETRLarge", {"resolution": self.spec.resolution}),
        }
        cls_name, kwargs = class_map.get(self.model_key, ("RFDETRBase", {"resolution": 560}))
        cls = getattr(rfdetr, cls_name, None)
        if cls is None:
            raise RuntimeError(f"rfdetr has no class {cls_name}; upgrade the rfdetr package")

        if self.weights_path and Path(self.weights_path).exists():
            kwargs["pretrain_weights"] = str(self.weights_path)
        if self.device:
            kwargs["device"] = self.device
        model = cls(**kwargs)
        return model

    def warmup(self) -> bool:
        if not self.load():
            return False
        blank = np.zeros((self.spec.resolution, self.spec.resolution, 3), dtype=np.uint8)
        try:
            self.detect(blank)
            return True
        except Exception as exc:  # noqa: BLE001
            self._load_error = f"warmup failed: {exc}"
            return False

    def close(self) -> None:
        with self._lock:
            self._model = None
            try:
                import torch  # noqa: PLC0415

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass

    # -- inference ---------------------------------------------------------
    def detect(self, frame: np.ndarray) -> list[Detection]:
        # ``load()`` is attempted once by whoever obtains the detector (see
        # ``DetectorRegistry.acquire`` and ``build_detector``). Retrying it here
        # turned a single missing dependency into one log line and one failed
        # ``import torch`` *per detected frame*; a detector that could not be
        # loaded simply finds nothing until the camera is reloaded.
        if self._model is None:
            return []
        height, width = np.asarray(frame).shape[:2]
        with self._lock:
            raw = self._infer(frame)
        return parse_raw_output(raw, width, height)

    def _infer(self, frame: np.ndarray) -> Any:
        model = self._model
        # RF-DETR's predict() accepts numpy/PIL and returns supervision
        # Detections. We pass an explicit low threshold and let the shared
        # per-camera filter do the rest.
        try:
            return model.predict(frame, threshold=FLOOR_THRESHOLD)
        except TypeError:
            return model.predict(frame)


def parse_raw_output(raw: Any, width: int, height: int) -> list[Detection]:
    """Normalise whatever the detector returned into normalised ``Detection``s.

    Handles supervision ``Detections`` (``xyxy`` / ``confidence`` / ``class_id``),
    plain dicts and ``(boxes, scores, labels)`` tuples.
    """
    detections: list[Detection] = []
    if raw is None:
        return detections

    boxes = scores = labels = None
    names: list[str] | None = None
    if hasattr(raw, "xyxy") and hasattr(raw, "confidence"):
        boxes = _tolist(getattr(raw, "xyxy"))
        scores = _tolist(getattr(raw, "confidence"))
        labels = getattr(raw, "class_id", None)
        labels = _tolist(labels) if labels is not None else [0] * len(boxes or [])
        # Pretrained COCO checkpoints report **COCO category ids** (1-based, with
        # gaps) rather than indexes into a 0-based class list, so mapping them
        # directly shifted every label by one (person -> bicycle, bus -> train).
        # rfdetr already attaches the resolved name; prefer it when present.
        meta = getattr(raw, "data", None)
        if isinstance(meta, dict):
            attached = meta.get("class_name")
            if attached is not None:
                names = ["" if item is None else str(item) for item in list(attached)]
    elif isinstance(raw, dict):
        boxes = _tolist(raw.get("boxes"))
        scores = _tolist(raw.get("scores") or raw.get("confidence"))
        labels = _tolist(raw.get("labels") or raw.get("class_id"))
    elif isinstance(raw, (list, tuple)) and len(raw) == 3:
        boxes, scores, labels = (_tolist(part) for part in raw)

    if boxes is None or scores is None:
        return detections
    if labels is None:
        labels = [0] * len(boxes)

    for position, (box, score, label) in enumerate(zip(boxes, scores, labels, strict=False)):
        if box is None or len(box) < 4:
            continue
        x1, y1, x2, y2 = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
        resolved = names[position] if names is not None and position < len(names) else ""
        if resolved:
            class_name = resolved
        else:
            idx = int(label) if label is not None else -1
            class_name = COCO_CLASSES[idx] if 0 <= idx < len(COCO_CLASSES) else f"class_{idx}"
        detections.append(
            Detection(
                bbox=[
                    round(max(0.0, min(x1 / width, 1.0)), 6),
                    round(max(0.0, min(y1 / height, 1.0)), 6),
                    round(max(0.0, min(x2 / width, 1.0)), 6),
                    round(max(0.0, min(y2 / height, 1.0)), 6),
                ],
                confidence=float(score),
                object_type=class_name,
            )
        )
    return detections


def _tolist(value: Any) -> list | None:
    if value is None:
        return None
    if hasattr(value, "tolist"):
        out = value.tolist()
        if out and isinstance(out[0], (list, tuple)):
            return [list(row) for row in out]
        return list(out)
    if isinstance(value, (list, tuple)):
        return list(value)
    return None


# --------------------------------------------------------------------------
# test doubles / fallbacks
# --------------------------------------------------------------------------
class NullDetector:
    """A detector that never finds anything (used when RF-DETR is missing)."""

    name = "null"
    available = False

    def __init__(self, reason: str = "RF-DETR not installed") -> None:
        self.reason = reason

    def detect(self, frame: np.ndarray) -> list[Detection]:  # noqa: ARG002
        return []


class FakeDetector:
    """Deterministic detector for tests.

    ``script`` is either a callable ``(frame, index) -> list[Detection]`` or a
    list of detection batches returned frame by frame (the last batch repeats).
    """

    name = "fake"
    available = True

    def __init__(
        self,
        script: list[list[Detection]] | Callable[[np.ndarray, int], list[Detection]],
    ) -> None:
        self._script = script
        self.calls = 0

    def detect(self, frame: np.ndarray) -> list[Detection]:
        index = self.calls
        self.calls += 1
        if callable(self._script):
            return list(self._script(frame, index))
        if not self._script:
            return []
        return list(self._script[min(index, len(self._script) - 1)])


# --------------------------------------------------------------------------
# shared model registry
# --------------------------------------------------------------------------
class DetectorRegistry:
    """Reference-counted cache so cameras sharing a model share the weights.

    This is the compromise that makes a single GPU practical: N cameras on the
    same RF-DETR variant load the weights **once**, while per-camera thresholds
    and enabled classes stay independent.
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[RFDetrDetector, int]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(model_key: str, weights_path: str | None, device: str | None) -> str:
        return f"{model_key}|{weights_path or '-'}|{device or 'auto'}"

    def acquire(
        self,
        model_key: str,
        *,
        weights_path: str | None = None,
        device: str | None = None,
    ) -> Detector:
        key = self._key(model_key, weights_path, device)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                detector = RFDetrDetector(model_key, weights_path=weights_path, device=device)
                self._cache[key] = (detector, 1)
                detector.load()
                return detector
            detector, count = entry
            self._cache[key] = (detector, count + 1)
            return detector

    def preload(
        self,
        model_key: str,
        *,
        weights_path: str | None = None,
        device: str | None = None,
    ) -> Detector:
        """Load the weights **without holding a reference**.

        Importing torch/rfdetr and reading a few hundred megabytes of weights
        takes seconds to minutes; this is meant to be called on a worker thread
        so the API stays responsive. The loaded model stays in the registry at
        reference count zero, so the first camera that really uses it does not
        pay for the load a second time.
        """
        detector = self.acquire(model_key, weights_path=weights_path, device=device)
        self.release(detector, unload=False)
        return detector

    def release(self, detector: Detector, *, unload: bool = True) -> None:
        """Drop one reference. ``unload`` closes the model when nothing uses it.

        ``unload=False`` keeps the weights resident for a later ``acquire``
        (used by :meth:`preload`).
        """
        with self._lock:
            for key, (cached, count) in list(self._cache.items()):
                if cached is detector:
                    remaining = max(0, count - 1)
                    if remaining == 0 and unload:
                        cached.close()
                        del self._cache[key]
                    else:
                        self._cache[key] = (cached, remaining)
                    return

    def clear(self) -> None:
        with self._lock:
            for detector, _ in self._cache.values():
                detector.close()
            self._cache.clear()

    def loaded(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "model_key": det.model_key,
                    "refs": count,
                    "available": det.available,
                    "error": det.load_error,
                }
                for det, count in self._cache.values()
            ]


registry = DetectorRegistry()


def build_detector(
    model_key: str,
    *,
    weights_path: str | None = None,
    device: str | None = None,
) -> Detector:
    """Best-effort detector: real RF-DETR when installed, otherwise a null one."""
    detector = RFDetrDetector(model_key, weights_path=weights_path, device=device)
    if detector.load():
        return detector
    return NullDetector(detector.load_error or "RF-DETR unavailable")

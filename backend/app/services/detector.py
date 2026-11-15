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
import time
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

import numpy as np

from app.core.config import settings
from app.services.geometry import box_size_norm
from app.services.models_registry import (
    COCO_CLASSES,
    ModelSpec,
    get_model_spec,
    note_measured_latency,
)
from app.services.tracker import Detection

logger = logging.getLogger(__name__)

# Detections below this are dropped before per-camera filtering so the shared
# model output stays small.
FLOOR_THRESHOLD = 0.05

#: How long a failed load is remembered before it is attempted again.
# ``acquire()`` runs on whichever request touches the camera next; retrying a
# broken model on every request froze the event loop for the whole load time.
LOAD_RETRY_COOLDOWN = 60.0


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
        #: ``time.monotonic()`` of the last failed load (see LOAD_RETRY_COOLDOWN)
        self._failed_at = 0.0
        #: "fp16" / "fp32" — what the last optimisation actually used.
        self.precision = "fp32"
        #: Mean time of one ``predict`` call measured at load (0 = not measured).
        self.latency_ms = 0.0

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
            if self._load_error and (time.monotonic() - self._failed_at) < LOAD_RETRY_COOLDOWN:
                # A model that failed to load (missing weights, OOM, broken
                # driver) stays failed for a minute instead of making the next
                # request pay the full load again.
                return False
            try:
                self._configure_torch()
                model = self._build_model()
                self._optimize(model)
            except Exception as exc:  # noqa: BLE001 - report, never crash the API
                self._load_error = f"{type(exc).__name__}: {exc}"
                self._failed_at = time.monotonic()
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

    # -- optimisation ------------------------------------------------------
    def _configure_torch(self) -> None:
        """Thread count for CPU inference; GPUs do not need it.

        torch defaults to one thread per core, so a CPU-only box had every core
        inside the model while eight ffmpeg decoders competed for the same cores
        (frame rate collapsed under load). Leaving ``cpu_reserve_cores`` free
        keeps decode independent of inference. A GPU run is unaffected.
        """
        try:
            import os  # noqa: PLC0415

            import torch  # noqa: PLC0415
        except Exception:  # noqa: BLE001 - torch is optional
            return
        if self.device and str(self.device).startswith("cuda"):
            return
        cores = os.cpu_count() or 2
        usable = max(1, cores - max(0, int(settings.cpu_reserve_cores)))
        try:
            torch.set_num_threads(usable)
            logger.info("CPU inference uses %d of %d cores", usable, cores)
        except Exception:  # noqa: BLE001
            logger.debug("could not set torch thread count", exc_info=True)

    def _on_cuda(self, torch: Any) -> bool:
        if self.device and not str(self.device).startswith("cuda"):
            return False
        return bool(torch.cuda.is_available())

    def _optimize(self, model: Any) -> None:
        """Trace the model once, in the precision this GPU actually wins at.

        RF-DETR prints "Model is not optimized for inference" on every frame
        until ``inference()`` is called; that call also traces the model at a
        fixed resolution, which is where most of the per-frame win comes from.

        FP16 is chosen from the hardware (see :func:`wants_half_precision`) and
        **traced once**. Benchmarking both precisions here looked attractive but
        cost 1.3 GB of host RAM and 12 extra seconds at load (three traced
        copies of the model live at the same time, measured on this machine); a
        camera server that runs for weeks cannot pay that.

        Any failure leaves the plain model in place: detection still works, just
        without the compiled graph.
        """
        if not settings.optimize_for_inference:
            return
        try:
            import torch  # noqa: PLC0415
        except Exception:  # noqa: BLE001 - optional dependency
            return

        cuda = self._on_cuda(torch)
        if cuda:
            torch.backends.cudnn.benchmark = True
            if settings.allow_tf32:
                torch.backends.cudnn.allow_tf32 = True
                torch.backends.cuda.matmul.allow_tf32 = True
                try:
                    torch.set_float32_matmul_precision("high")
                except Exception:  # noqa: BLE001 - older torch
                    pass

        choice = (settings.fp16 or "auto").strip().lower()
        if choice not in {"auto", "on", "off"}:
            choice = "auto"
        name = ""
        capability: tuple[int, int] | None = None
        if cuda:
            try:
                name = str(torch.cuda.get_device_name(0))
                major, minor = torch.cuda.get_device_capability(0)
                capability = (int(major), int(minor))
            except Exception:  # noqa: BLE001
                name, capability = "", None

        use_half = wants_half_precision(choice, cuda, name, capability)
        dtype = torch.float16 if use_half else torch.float32
        try:
            model.inference(compile=True, dtype=dtype)
        except Exception as exc:  # noqa: BLE001 - a plain model still detects
            logger.warning(
                "could not optimize %s for inference (%s); running uncompiled",
                self.model_key,
                exc,
            )
            return
        self.precision = "fp16" if use_half else "fp32"

        blank = np.zeros((self.spec.resolution, self.spec.resolution, 3), dtype=np.uint8)
        measured = self._time_predict(model, blank)
        if measured:
            self.latency_ms = round(float(measured), 1)
            if cuda:
                factor = note_measured_latency(self.model_key, self.latency_ms)
                logger.info(
                    "%s ready on %s: %s, %.1f ms/frame (device speed x%.2f)",
                    self.model_key,
                    name or self.device or "auto",
                    self.precision,
                    self.latency_ms,
                    factor,
                )

    @staticmethod
    def _time_predict(model: Any, frame: np.ndarray, runs: int = 3) -> float:
        """Fastest of ``runs`` predictions, ignoring the first (graph warm-up)."""
        best: float | None = None
        for index in range(runs + 1):
            started = time.perf_counter()
            model.predict(frame, threshold=FLOOR_THRESHOLD)
            elapsed = (time.perf_counter() - started) * 1000.0
            if index and (best is None or elapsed < best):
                best = elapsed
        return float(best or 0.0)

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
            self.precision = "fp32"
            self.latency_ms = 0.0
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


def wants_half_precision(
    choice: str,
    cuda: bool,
    device_name: str = "",
    capability: tuple[int, int] | None = None,
) -> bool:
    """Whether FP16 inference should be used on this device.

    ``auto`` enables FP16 on GPUs that have FP16 Tensor Cores, because the win
    there is large (An A2000-class card halves the time per frame) and it is a
    loss everywhere else: a GTX 16xx has the compute capability of Turing but
    *no* Tensor Cores, and measured 106 ms → 382 ms per frame in FP16. It is
    therefore excluded by name rather than by capability.

    This is a hardware question, not a measurement, so the answer costs nothing
    at startup and the model is traced only once.
    """
    if not cuda:
        return False
    if choice == "on":
        return True
    if choice == "off":
        return False
    if capability is None:
        return False
    major, _minor = capability
    if major < 7:  # Pascal and older: FP16 is emulated or slower
        return False
    lowered = (device_name or "").lower()
    if "gtx 16" in lowered:  # Turing die without Tensor Cores
        return False
    return True


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
                    "precision": det.precision,
                    "latency_ms": det.latency_ms,
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

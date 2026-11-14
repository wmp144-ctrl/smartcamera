"""RF-DETR model catalogue.

Roboflow's RF-DETR ships several weight variants. Each camera can be pointed at
a different variant (and different weights file), so the catalogue is the single
source of truth for what is selectable in the UI and what the offline download
script must fetch.
"""

from __future__ import annotations

from dataclasses import dataclass

# The 80 COCO classes RF-DETR is trained on.
COCO_CLASSES: tuple[str, ...] = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)

# Persian labels for the classes most relevant to CCTV (the rest fall back to
# their English name in the UI).
CLASS_LABELS_FA: dict[str, str] = {
    "person": "انسان",
    "bicycle": "دوچرخه",
    "car": "خودرو",
    "motorcycle": "موتورسیکلت",
    "airplane": "هواپیما",
    "bus": "اتوبوس",
    "train": "قطار",
    "truck": "کامیون",
    "boat": "قایق",
    "traffic light": "چراغ راهنما",
    "fire hydrant": "شیر آتشنشانی",
    "stop sign": "تابلوی ایست",
    "bench": "نیمکت",
    "dog": "سگ",
    "cat": "گربه",
    "backpack": "کولهپشتی",
    "handbag": "کیف دستی",
    "suitcase": "چمدان",
    "bottle": "بطری",
    "chair": "صندلی",
    "tv": "تلویزیون",
    "laptop": "لپتاپ",
    "cell phone": "تلفن همراه",
}

# Classes that make sense to record by default for CCTV.
DEFAULT_RECORD_CLASSES: tuple[str, ...] = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "bus",
    "truck",
)


@dataclass(frozen=True, slots=True)
class ModelSpec:
    key: str
    name: str
    resolution: int
    weights_filename: str
    params_millions: float
    description: str

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "name": self.name,
            "resolution": self.resolution,
            "weights_filename": self.weights_filename,
            "description": self.description,
        }


RFDETR_MODELS: dict[str, ModelSpec] = {
    "rf-detr-nano": ModelSpec(
        key="rf-detr-nano",
        name="RF-DETR Nano",
        resolution=384,
        weights_filename="rf-detr-nano.pth",
        params_millions=30.5,
        description="سبک‌ترین مدل برای سیستم‌های ضعیف و تعداد بالای دوربین",
    ),
    "rf-detr-small": ModelSpec(
        key="rf-detr-small",
        name="RF-DETR Small",
        resolution=512,
        weights_filename="rf-detr-small.pth",
        params_millions=32.1,
        description="تعادل مناسب بین دقت و سرعت",
    ),
    "rf-detr-base": ModelSpec(
        key="rf-detr-base",
        name="RF-DETR Base",
        resolution=560,
        weights_filename="rf-detr-base.pth",
        params_millions=33.7,
        description="مدل پیشنهادی برای اکثر سناریوهای نظارتی",
    ),
    "rf-detr-medium": ModelSpec(
        key="rf-detr-medium",
        name="RF-DETR Medium",
        resolution=576,
        weights_filename="rf-detr-medium.pth",
        params_millions=35.1,
        description="دقت بالاتر با مصرف بیشتر حافظه گرافیکی",
    ),
    "rf-detr-large": ModelSpec(
        key="rf-detr-large",
        name="RF-DETR Large",
        resolution=700,
        weights_filename="rf-detr-large.pth",
        params_millions=35.7,
        description="بالاترین دقت؛ برای تعداد کم دوربین یا GPU قدرتمند",
    ),
}

DEFAULT_MODEL_KEY = "rf-detr-base"

#: Single-frame throughput measured on the reference machine (GTX 1650 4 GB,
#: 1080p source): nano 39 ms, small 65 ms, base 106 ms per frame. The numbers
#: only decide whether a configuration is plausible and which model a new
#: camera gets. They are scaled by :data:`_SPEED_FACTOR`, which
#: :func:`note_measured_latency` sets from the GPU actually installed, so an
#: RTX card is not budgeted like a GTX 1650.
MEASURED_FPS: dict[str, float] = {
    "rf-detr-large": 5.0,
    "rf-detr-medium": 7.0,
    "rf-detr-base": 9.0,
    "rf-detr-small": 15.0,
    "rf-detr-nano": 25.0,
}

#: Heaviest first: the budget check walks this order. Medium/Large are left out
#: on purpose: they need more VRAM than a 4 GB card has and the extra accuracy is
#: not what a camera monitor needs, so the default ceiling is Base.
_BY_COST: tuple[str, ...] = (
    "rf-detr-base",
    "rf-detr-small",
    "rf-detr-nano",
)

#: Head-room kept free for the shared model lock, JPEG encoding and the UI.
_HEADROOM = 1.15


#: How much faster (or slower) this machine is than the reference GPU above.
#: 1.0 until the detector measures a real frame.
_SPEED_FACTOR = 1.0
_SPEED_FACTOR_MIN = 0.25
_SPEED_FACTOR_MAX = 8.0


def speed_factor() -> float:
    """Device speed relative to the reference GTX 1650 (1.0 = as measured)."""
    return _SPEED_FACTOR


def note_measured_latency(model_key: str, latency_ms: float) -> float:
    """Scale the capacity table from one real measurement of this machine.

    Called once after the detector loads: a card that runs the model twice as
    fast as the table claims must also be recommended a heavier model for a
    second camera. The ratio is clamped so a single strange sample (cold cache,
    first traced call) can never collapse or explode the budget.
    """
    global _SPEED_FACTOR  # noqa: PLW0603 - process-wide GPU characteristic
    baseline = MEASURED_FPS.get(model_key)
    if not baseline or latency_ms <= 0:
        return _SPEED_FACTOR
    ratio = (1000.0 / float(latency_ms)) / baseline
    _SPEED_FACTOR = min(_SPEED_FACTOR_MAX, max(_SPEED_FACTOR_MIN, ratio))
    return _SPEED_FACTOR


def reset_speed_factor() -> None:
    """Forget the measurement (tests, or after switching the GPU)."""
    global _SPEED_FACTOR  # noqa: PLW0603
    _SPEED_FACTOR = 1.0


def detection_capacity(model_key: str) -> float:
    """Approximate detections per second a single model instance can serve."""
    baseline = MEASURED_FPS.get(model_key, MEASURED_FPS[DEFAULT_MODEL_KEY])
    return round(baseline * _SPEED_FACTOR, 2)


def suggest_model(camera_count: int, per_camera_fps: float) -> str:
    """Heaviest model that still serves ``camera_count`` cameras at ``fps``.

    Detector inference is serialised (one GPU, one model lock), so the demand is
    the sum of every detecting camera's cadence.
    """
    demand = max(1, int(camera_count)) * max(0.5, float(per_camera_fps)) * _HEADROOM
    for key in _BY_COST:
        if detection_capacity(key) >= demand:
            return key
    return "rf-detr-nano"


def get_model_spec(key: str | None) -> ModelSpec:
    """Return the spec for a key, falling back to the default model."""
    if not key:
        return RFDETR_MODELS[DEFAULT_MODEL_KEY]
    return RFDETR_MODELS.get(key, RFDETR_MODELS[DEFAULT_MODEL_KEY])


def class_label(name: str) -> str:
    return CLASS_LABELS_FA.get(name, name)


def default_class_config(classes: tuple[str, ...] = DEFAULT_RECORD_CLASSES) -> dict[str, dict]:
    from app.models.camera import DEFAULT_CLASS_CONFIG  # noqa: PLC0415

    config: dict[str, dict] = {}
    for name in classes:
        config[name] = dict(DEFAULT_CLASS_CONFIG.get(name, {
            "enabled": True,
            "threshold": 0.35,
            "min_width": 0.0,
            "min_height": 0.0,
            "min_motion": 0.0,
        }))
    return config

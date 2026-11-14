"""Hardware-derived performance defaults and the editable performance profile.

Three sources of truth, in this order:

1. ``CAMERASMART_*`` environment variables (what an operator sets in ``.env``),
2. ``data/performance.json`` — values saved from the Settings page, applied at
   startup before the cameras start,
3. the built-in defaults in :class:`app.core.config.Settings`.

The profile only holds settings that can be changed while the app runs. It
exists because the Settings page must survive a restart: camera and detection
rates are per-camera columns in the database, everything else lives here.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

PROFILE_FILENAME = "performance.json"


@dataclass
class PerformanceProfile:
    """Runtime-tunable performance knobs (see ``Settings`` for the meaning)."""

    #: Frame rate requested from the decoder (new cameras + applied on request).
    stream_fps: int = 12
    #: Detection cadence per camera.
    detection_fps: float = 5.0
    #: Downscale width handed to RF-DETR; 0 = hand over the decoded frame.
    detection_max_width: int = 0
    #: Frames that may wait per camera for the shared inference thread.
    inference_queue_size: int = 1
    #: Threads that write stored snapshots (JPEG encode + disk).
    snapshot_workers: int = 2
    #: Snapshots allowed to wait for a writer before new ones are dropped.
    snapshot_queue_size: int = 64
    #: Pipeline results persisted per transaction.
    persist_batch_max: int = 16
    #: ``auto`` / ``on`` / ``off`` for FP16 inference.
    fp16: str = "auto"
    #: ``cuda`` / ``cpu`` (empty = whatever the config says).
    device: str = "cuda"


EDITABLE_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(PerformanceProfile))

#: Upper/lower bounds used to reject nonsense before it reaches a stream.
_LIMITS: dict[str, tuple[float, float]] = {
    "stream_fps": (1, 60),
    "detection_fps": (0.2, 30),
    "detection_max_width": (0, 3840),
    "inference_queue_size": (1, 8),
    "snapshot_workers": (1, 8),
    "snapshot_queue_size": (4, 2048),
    "persist_batch_max": (1, 64),
}


def profile_path() -> Path:
    return settings.data_dir / PROFILE_FILENAME


def default_profile() -> PerformanceProfile:
    """Defaults derived from the machine, not from a fixed table.

    The numbers follow the CPU and the GPU that are actually installed, because
    the same app ships to a 4-core office PC and to a workstation with an RTX
    card: half the cores, half the snapshot writers; two Tensor-core capable
    cards get FP16 automatically (the detector measures and confirms it).
    """
    cores = os.cpu_count() or 4
    profile = PerformanceProfile(
        stream_fps=settings.default_stream_fps,
        detection_fps=settings.default_detection_fps,
        detection_max_width=settings.detection_max_width,
        inference_queue_size=settings.inference_queue_size,
        snapshot_workers=max(1, min(4, cores // 4)),
        snapshot_queue_size=settings.snapshot_queue_size,
        persist_batch_max=settings.persist_batch_max,
        fp16=settings.fp16,
        device=settings.device,
    )
    try:  # a GPU changes how much work per frame is affordable
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            total_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            if total_gb >= 6:
                profile.snapshot_workers = min(4, max(profile.snapshot_workers, 2))
            major = torch.cuda.get_device_capability(0)[0]
            profile.fp16 = "auto" if major >= 7 else "off"
    except Exception:  # noqa: BLE001 - no AI stack: CPU defaults are correct
        profile.fp16 = "off"
    return profile


def load_profile() -> PerformanceProfile:
    """Read the saved profile, falling back to the hardware defaults."""
    path = profile_path()
    profile = default_profile()
    if not path.is_file():
        return profile
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a corrupt file must not block boot
        logger.warning("ignoring unreadable %s: %s", path.name, exc)
        return profile
    if not isinstance(raw, dict):
        return profile
    for key, value in raw.items():
        if key not in EDITABLE_FIELDS:
            continue
        try:
            setattr(profile, key, type(getattr(profile, key))(value))
        except (TypeError, ValueError):
            logger.warning("ignoring invalid performance value %s=%r", key, value)
    return profile


def save_profile(profile: PerformanceProfile) -> None:
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(profile), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def validate(data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Coerce and bound a partial update. Returns (clean, errors)."""
    clean: dict[str, Any] = {}
    errors: list[str] = []
    for key, value in data.items():
        if key not in EDITABLE_FIELDS:
            errors.append(f"ناشناخته: {key}")
            continue
        if key == "fp16":
            if str(value).lower() not in {"auto", "on", "off"}:
                errors.append("fp16 باید auto/on/off باشد")
                continue
            clean[key] = str(value).lower()
            continue
        if key == "device":
            text = str(value).lower()
            if text not in {"cuda", "cpu"}:
                errors.append("device باید cuda یا cpu باشد")
                continue
            clean[key] = text
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            errors.append(f"{key} باید عدد باشد")
            continue
        low, high = _LIMITS.get(key, (0.0, 1e9))
        if not low <= number <= high:
            errors.append(f"{key} باید بین {low} و {high} باشد")
            continue
        if key in {"stream_fps", "inference_queue_size", "snapshot_workers",
                   "snapshot_queue_size", "persist_batch_max", "detection_max_width"}:
            clean[key] = int(number)
        else:
            clean[key] = round(number, 2)
    return clean, errors


def apply_profile(profile: PerformanceProfile) -> None:
    """Push a profile into the live process (config object + scheduler)."""
    settings.default_stream_fps = int(profile.stream_fps)
    settings.default_detection_fps = float(profile.detection_fps)
    settings.detection_max_width = int(profile.detection_max_width)
    settings.inference_queue_size = int(profile.inference_queue_size)
    settings.snapshot_workers = int(profile.snapshot_workers)
    settings.snapshot_queue_size = int(profile.snapshot_queue_size)
    settings.persist_batch_max = int(profile.persist_batch_max)
    settings.fp16 = profile.fp16
    settings.device = profile.device or settings.device

    from app.services.inference import scheduler  # noqa: PLC0415

    scheduler.set_queue_size(int(profile.inference_queue_size))

    from app.services.persistence import snapshot_writer  # noqa: PLC0415

    snapshot_writer().resize(
        int(profile.snapshot_workers), int(profile.snapshot_queue_size)
    )


def hardware_summary() -> dict[str, Any]:
    """What the machine looks like, for the Settings page."""
    summary: dict[str, Any] = {
        "cpu_cores": os.cpu_count() or 0,
        "cpu": os.environ.get("PROCESSOR_IDENTIFIER", ""),
        "gpu": None,
        "vram_gb": None,
        "compute_capability": None,
    }
    try:
        import torch  # noqa: PLC0415

        if torch.cuda.is_available():
            index = 0
            summary["gpu"] = torch.cuda.get_device_name(index)
            summary["vram_gb"] = round(
                torch.cuda.get_device_properties(index).total_memory / (1024**3), 1
            )
            major, minor = torch.cuda.get_device_capability(index)
            summary["compute_capability"] = f"{major}.{minor}"
            # A Tensor-core card earns FP16; anything older is left on FP32.
            summary["tensor_cores"] = major >= 7 and not summary["gpu"].startswith("NVIDIA GeForce GTX 16")
    except Exception:  # noqa: BLE001 - telemetry must never raise
        pass
    disks = shutil.disk_usage(settings.data_dir)
    summary["disk_free_gb"] = round(disks.free / (1024**3), 1)
    return summary

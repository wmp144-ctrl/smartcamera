"""Object image storage on the filesystem.

Layout (categorised, which is what the operator browses and what the retention
job cleans up)::

    data/storage/objects/<camera_id>/<YYYY>/<MM>/<DD>/<object_type>/
        <ts>_t<track_id>_<kind>_full.jpg
        <ts>_t<track_id>_<kind>_crop.jpg
        <ts>_t<track_id>_<kind>_thumb.jpg

Paths persisted in the database are **relative to** ``settings.storage_root`` so
the whole project can be copied to an offline machine unchanged.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from app.core.config import settings
from app.core.timeutil import to_local
from app.services.geometry import denormalize_box

_SAFE = re.compile(r"[^a-zA-Z0-9_\-]+")

FULL_QUALITY = 88
BEST_QUALITY = 95
CROP_QUALITY = 92
THUMB_SIZE = 320


def slugify(value: str) -> str:
    slug = _SAFE.sub("-", (value or "object").strip()).strip("-").lower()
    return slug or "object"


def encode_jpeg(frame, quality: int = FULL_QUALITY) -> bytes:
    """Encode a BGR/RGB numpy frame to JPEG bytes."""
    image = to_pil(frame)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def to_pil(frame) -> Image.Image:
    """numpy (BGR from OpenCV, or RGB) -> PIL image without OpenCV."""
    import numpy as np  # noqa: PLC0415

    array = np.asarray(frame)
    if array.ndim == 2:
        return Image.fromarray(array).convert("RGB")
    if array.shape[2] == 4:
        return Image.fromarray(array[:, :, :3][:, :, ::-1]).convert("RGB")
    # Assume BGR (OpenCV) and flip to RGB.
    return Image.fromarray(array[:, :, ::-1].copy())


@dataclass(slots=True)
class SavedImages:
    full_path: str
    crop_path: str
    thumb_path: str
    width: int
    height: int
    crop_width: int
    crop_height: int


def _date_parts(when: datetime) -> tuple[int, int, int]:
    if settings.storage_date_mode == "utc":
        return when.year, when.month, when.day
    local = to_local(when, settings.timezone) or when
    return local.year, local.month, local.day


def object_dir(camera_id: int, object_type: str, when: datetime) -> Path:
    year, month, day = _date_parts(when)
    return (
        settings.snapshots_dir
        / str(camera_id)
        / f"{year:04d}"
        / f"{month:02d}"
        / f"{day:02d}"
        / slugify(object_type)
    )


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(settings.storage_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _make_thumb(image: Image.Image) -> Image.Image:
    thumb = image.copy()
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    return thumb


def save_object_images(
    frame,
    bbox_norm: list[float],
    *,
    camera_id: int,
    object_type: str,
    track_id: int,
    kind: str,
    captured_at: datetime,
    best: bool = False,
    annotate: bool = True,
    padding: float = 0.12,
    color: str = "#22d3ee",
) -> SavedImages:
    """Persist the full frame, the object crop and a thumbnail."""
    import numpy as np  # noqa: PLC0415

    array = np.asarray(frame)
    height, width = array.shape[:2]

    full_image = to_pil(array)
    # A 4K camera produced a ~1.8 MB evidence JPEG and cost ~8 ms of encoding
    # per stored object; ``snapshot_max_width`` caps the stored frame (0 keeps
    # the original resolution). Boxes are normalised, so they stay valid.
    limit = settings.snapshot_max_width
    if limit and full_image.width > limit:
        ratio = limit / full_image.width
        full_image = full_image.resize(
            (limit, max(1, round(full_image.height * ratio))), Image.Resampling.BILINEAR
        )
        array = np.asarray(full_image)
        height, width = array.shape[:2]
    box_px = denormalize_box(bbox_norm, width, height)

    directory = object_dir(camera_id, object_type, captured_at)
    directory.mkdir(parents=True, exist_ok=True)

    stamp = captured_at.strftime("%Y%m%dT%H%M%S%f")
    prefix = f"{stamp}_t{track_id}_{slugify(kind)}"
    full_file = directory / f"{prefix}_full.jpg"
    crop_file = directory / f"{prefix}_crop.jpg"
    thumb_file = directory / f"{prefix}_thumb.jpg"

    if annotate:
        draw = ImageDraw.Draw(full_image)
        draw.rectangle(box_px, outline=color, width=max(2, width // 400))
        label = object_type
        draw.rectangle(
            [box_px[0], max(0, box_px[1] - 16), box_px[0] + 8 * len(label) + 10, box_px[1]],
            fill=color,
        )
        draw.text((box_px[0] + 5, max(0, box_px[1] - 15)), label, fill="#04121a")

    # ``optimize=False`` on purpose: the Huffman pass costs ~13 ms per 1080p
    # frame (22.5 ms -> 9.5 ms measured) and these saves happen on the video
    # thread, where a stall means dropped frames. The price is ~7% larger files.
    full_quality = BEST_QUALITY if best else FULL_QUALITY
    full_image.save(full_file, format="JPEG", quality=full_quality, optimize=False)
    _make_thumb(full_image).save(thumb_file, format="JPEG", quality=80, optimize=False)

    # crop with a little padding for context
    x1, y1, x2, y2 = box_px
    pad_x = (x2 - x1) * padding
    pad_y = (y2 - y1) * padding
    cx1 = int(max(0, x1 - pad_x))
    cy1 = int(max(0, y1 - pad_y))
    cx2 = int(min(width, x2 + pad_x))
    cy2 = int(min(height, y2 + pad_y))
    if cx2 <= cx1:
        cx2 = min(width, cx1 + 1)
    if cy2 <= cy1:
        cy2 = min(height, cy1 + 1)
    crop_image = full_image.crop((cx1, cy1, cx2, cy2))
    crop_image.save(crop_file, format="JPEG", quality=CROP_QUALITY, optimize=False)

    return SavedImages(
        full_path=relative(full_file),
        crop_path=relative(crop_file),
        thumb_path=relative(thumb_file),
        width=width,
        height=height,
        crop_width=crop_image.width,
        crop_height=crop_image.height,
    )


def delete_relative(rel_path: str | None) -> bool:
    """Delete a stored file (no-op if missing or outside the storage root)."""
    if not rel_path:
        return False
    path = (settings.storage_root / rel_path).resolve()
    try:
        path.relative_to(settings.storage_root.resolve())
    except ValueError:
        return False
    if path.is_file():
        path.unlink()
        return True
    return False


def save_thumb_from_file(rel_path: str, size: int = THUMB_SIZE) -> str | None:
    """Create/refresh a thumbnail for a stored image; returns its relative path."""
    source = settings.storage_root / rel_path
    if not source.is_file():
        return None
    target = source.with_name(source.stem + "_thumb.jpg")
    try:
        with Image.open(source) as image:
            thumb = _make_thumb(image)
            thumb.save(target, format="JPEG", quality=80, optimize=False)
    except Exception:  # noqa: BLE001 - a corrupt image must not break the API
        return None
    return relative(target)


def directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def storage_usage() -> dict[str, object]:
    """Total bytes plus a breakdown by camera and by object type."""
    root = settings.snapshots_dir
    by_camera: dict[str, int] = {}
    by_type: dict[str, int] = {}
    total = 0
    files = 0
    if root.exists():
        for item in root.rglob("*.jpg"):
            try:
                size = item.stat().st_size
            except OSError:
                continue
            total += size
            files += 1
            parts = item.relative_to(root).parts
            if parts:
                by_camera[parts[0]] = by_camera.get(parts[0], 0) + size
            if len(parts) >= 5:
                by_type[parts[4]] = by_type.get(parts[4], 0) + size
    return {
        "root": str(root),
        "total_bytes": total,
        "file_count": files,
        "by_camera": by_camera,
        "by_type": by_type,
    }

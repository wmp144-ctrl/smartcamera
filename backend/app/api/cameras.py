"""Camera, zone, live-view and health endpoints."""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import CameraDep, SessionDep
from app.core.config import settings
from app.core.security import is_masked_url
from app.core.timeutil import utcnow
from app.models.camera import Camera, Zone
from app.models.system import HealthMetric
from app.schemas.camera import (
    CameraCreate,
    CameraRead,
    CameraStatus,
    CameraUpdate,
    CameraWithZones,
    ZoneCreate,
    ZoneRead,
    ZoneUpdate,
    rule_as_dict,
)
from app.schemas.common import Message
from app.schemas.system import HealthRead
from app.services.camera_manager import manager, runtime_config_from_camera, zones_from_camera
from app.services.models_registry import RFDETR_MODELS
from app.services.stream import probe_stream

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cameras", tags=["cameras"])

BOUNDARY = "camerasmartframe"

#: Fields whose change requires restarting the decoder/detector. Everything else
#: (name, location, notes) is applied in place so editing a note does not
#: interrupt the live view.
STREAM_FIELDS = frozenset(
    {
        "rtsp_url",
        "enabled",
        "transport",
        "fps",
        "detection_fps",
        "width",
        "height",
        "model_name",
        "model_weights",
        "detection_enabled",
        "motion_sensitivity",
        "record_objects",
        "record_snapshots",
        "class_config",
        "reconnect_policy",
    }
)

#: Nullable columns: an explicit ``null`` here is meaningful.
NULLABLE_FIELDS = frozenset({"location", "notes", "width", "height", "model_weights"})


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------
@router.get("", response_model=list[CameraWithZones])
async def list_cameras(
    session: SessionDep,
    enabled: bool | None = None,
    search: str | None = None,
) -> list[Camera]:
    from sqlalchemy.orm import selectinload

    stmt = select(Camera).options(selectinload(Camera.zones)).order_by(Camera.id)
    if enabled is not None:
        stmt = stmt.where(Camera.enabled.is_(enabled))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(Camera.name.ilike(like) | Camera.location.ilike(like))
    return list((await session.execute(stmt)).scalars().all())


@router.post("", response_model=CameraWithZones, status_code=status.HTTP_201_CREATED)
async def create_camera(payload: CameraCreate, session: SessionDep) -> Camera:
    data = payload.model_dump(exclude={"class_config", "reconnect_policy"})
    # ``CAMERASMART_DEFAULT_MODEL`` / ``_STREAM_FPS`` are the documented defaults
    # for a new camera; the schema leaves them unset so these actually apply.
    if not data.get("model_name"):
        data["model_name"] = settings.default_model
    if not data.get("fps"):
        data["fps"] = settings.default_stream_fps
    camera = Camera(
        **data,
        class_config={key: rule_as_dict(rule) for key, rule in payload.class_config.items()},
        reconnect_policy=rule_as_dict(payload.reconnect_policy),
    )
    session.add(camera)
    await session.commit()
    await session.refresh(camera)
    # Model loading is slow (and may download weights): never do it in the loop.
    preload = manager.detector_preload_args(camera)
    if preload is not None:
        await asyncio.to_thread(manager.preload_detector, **preload)
    manager.apply_camera(camera)
    return camera


@router.get("/{camera_id}", response_model=CameraWithZones)
async def get_camera_detail(camera: CameraDep) -> Camera:
    return camera


@router.patch("/{camera_id}", response_model=CameraWithZones)
async def update_camera(
    camera: CameraDep, payload: CameraUpdate, session: SessionDep
) -> Camera:
    data = payload.model_dump(exclude_unset=True)
    # The API never hands out real camera credentials, so an unchanged URL comes
    # back redacted. Keeping the stored value makes editing name/notes safe.
    if data.get("rtsp_url") and is_masked_url(data["rtsp_url"]):
        data.pop("rtsp_url")
    data = {
        key: value
        for key, value in data.items()
        if value is not None or key in NULLABLE_FIELDS or key in {"class_config", "reconnect_policy"}
    }
    if data.get("class_config") is not None:
        data["class_config"] = {
            key: rule_as_dict(rule) for key, rule in data["class_config"].items()
        }
    if data.get("reconnect_policy") is not None:
        data["reconnect_policy"] = rule_as_dict(data["reconnect_policy"])

    for field, value in data.items():
        setattr(camera, field, value)
    await session.commit()
    await session.refresh(camera)
    if set(data) & STREAM_FIELDS:
        await manager.reload_camera(camera.id)
    else:
        # Presentation-only edit: never restart the stream for it.
        manager.refresh_metadata(camera)
    return camera


@router.delete("/{camera_id}", response_model=Message)
async def delete_camera(camera: CameraDep, session: SessionDep) -> Message:
    camera_id = camera.id
    await session.delete(camera)
    await session.commit()
    manager.remove_camera(camera_id)
    # The rows are gone, so retention can no longer find these files: without
    # this the images of a deleted camera stayed on disk forever.
    media_dir = settings.snapshots_dir / str(camera_id)
    if media_dir.is_dir():
        await asyncio.to_thread(shutil.rmtree, media_dir, True)
    return Message(detail="دوربین حذف شد")


@router.post("/{camera_id}/reload", response_model=Message)
async def reload_camera(camera_id: int) -> Message:
    await manager.reload_camera(camera_id)
    return Message(detail="دوربین دوباره راه‌اندازی شد")


# --------------------------------------------------------------------------
# detection toggles / model selection
# --------------------------------------------------------------------------
@router.get("/models/available")
async def available_models() -> dict:
    from app.services.detector import registry as detector_registry

    loaded = {item["model_key"]: item for item in detector_registry.loaded()}
    return {
        "models": [
            {**spec.to_dict(), "loaded": key in loaded, "error": loaded.get(key, {}).get("error")}
            for key, spec in RFDETR_MODELS.items()
        ],
        "default": settings.default_model,
        "device": settings.device,
    }


@router.post("/{camera_id}/detection", response_model=Message)
async def toggle_detection(
    camera: CameraDep, session: SessionDep, enabled: Annotated[bool, Query()] = True
) -> Message:
    camera.detection_enabled = enabled
    await session.commit()
    if enabled:
        # Load the weights off the event loop before starting the pipeline.
        preload = manager.detector_preload_args(camera)
        if preload is not None:
            await asyncio.to_thread(manager.preload_detector, **preload)
    if not manager.set_detection(camera.id, enabled):
        await manager.reload_camera(camera.id)
    return Message(detail="تشخیص اشیا فعال شد" if enabled else "تشخیص اشیا غیرفعال شد")


@router.post("/{camera_id}/test", response_model=dict)
async def test_camera(camera: CameraDep) -> dict:
    """Probe the stream to verify connectivity (used by the "test" button)."""
    probe = await asyncio.to_thread(probe_stream, camera.rtsp_url, timeout=8.0)
    if probe is None:
        return {
            "ok": False,
            "message": "دسترسی به استریم امکان‌پذیر نشد یا ffprobe در دسترس نیست",
            "width": None,
            "height": None,
            "fps": None,
        }
    return {
        "ok": True,
        "message": "اتصال برقرار است",
        "width": probe.width,
        "height": probe.height,
        "fps": round(probe.fps, 2),
        "codec": probe.codec,
    }


# --------------------------------------------------------------------------
# status / health
# --------------------------------------------------------------------------
@router.get("/status/all", response_model=list[CameraStatus])
async def all_status(session: SessionDep) -> list[CameraStatus]:
    cameras = (await session.execute(select(Camera).order_by(Camera.id))).scalars().all()
    result: list[CameraStatus] = []
    for camera in cameras:
        health = manager.health(camera.id)
        if health is None:
            result.append(
                CameraStatus(
                    camera_id=camera.id,
                    state="disabled" if not camera.enabled else "unknown",
                    detection_enabled=camera.detection_enabled,
                    message="استریم فعال نیست",
                    detector_error=manager.detector_error(camera.id),
                )
            )
            continue
        result.append(
            CameraStatus(
                camera_id=camera.id,
                state=health.get("state", "unknown"),
                fps=health.get("fps", 0.0),
                detection_fps=health.get("detection_fps", 0.0),
                bitrate_kbps=health.get("bitrate_kbps", 0.0),
                width=health.get("width", 0),
                height=health.get("height", 0),
                latency_ms=health.get("latency_ms", 0.0),
                jitter_ms=health.get("jitter_ms", 0.0),
                dropped_frames=health.get("dropped_frames", 0),
                reconnect_count=health.get("reconnect_count", 0),
                last_frame_at=_parse_dt(health.get("last_frame_at")),
                detection_enabled=camera.detection_enabled,
                message=health.get("message"),
                detector_error=manager.detector_error(camera.id),
            )
        )
    return result


@router.get("/{camera_id}/status", response_model=CameraStatus)
async def camera_status(camera: CameraDep) -> CameraStatus:
    health = manager.health(camera.id)
    if health is None:
        return CameraStatus(
            camera_id=camera.id,
            state="disabled" if not camera.enabled else "unknown",
            detection_enabled=camera.detection_enabled,
            message="استریم فعال نیست",
            detector_error=manager.detector_error(camera.id),
        )
    return CameraStatus(
        camera_id=camera.id,
        state=health.get("state", "unknown"),
        fps=health.get("fps", 0.0),
        detection_fps=health.get("detection_fps", 0.0),
        bitrate_kbps=health.get("bitrate_kbps", 0.0),
        width=health.get("width", 0),
        height=health.get("height", 0),
        latency_ms=health.get("latency_ms", 0.0),
        jitter_ms=health.get("jitter_ms", 0.0),
        dropped_frames=health.get("dropped_frames", 0),
        reconnect_count=health.get("reconnect_count", 0),
        last_frame_at=_parse_dt(health.get("last_frame_at")),
        detection_enabled=camera.detection_enabled,
        message=health.get("message"),
        detector_error=manager.detector_error(camera.id),
    )


@router.get("/{camera_id}/health", response_model=list[HealthRead])
async def camera_health(
    camera: CameraDep,
    session: SessionDep,
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[HealthMetric]:
    from datetime import timedelta

    since = utcnow() - timedelta(hours=hours)
    stmt = (
        select(HealthMetric)
        .where(HealthMetric.camera_id == camera.id, HealthMetric.recorded_at >= since)
        .order_by(HealthMetric.recorded_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


# --------------------------------------------------------------------------
# live view
# --------------------------------------------------------------------------
@router.get("/{camera_id}/snapshot")
async def camera_snapshot(camera: CameraDep, overlay: bool = False) -> Response:
    # ``encode_if_missing`` keeps the snapshot button working even when no live
    # view is open (idle cameras do not encode frames in the background). The
    # encoding runs in a worker thread so it cannot stall the event loop.
    frame = await asyncio.to_thread(
        manager.latest_jpeg, camera.id, overlay=overlay, encode_if_missing=True
    )
    if frame is None:
        raise HTTPException(status_code=404, detail="تصویری از دوربین در دسترس نیست")
    return Response(
        content=frame,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Camera-Id": str(camera.id)},
    )


async def _mjpeg_stream(camera_id: int, overlay: bool, fps: int) -> AsyncIterator[bytes]:
    interval = 1.0 / max(1, min(fps, 60))
    last: bytes | None = None
    idle = 0
    while True:
        # Tell the decoder somebody is watching, then fetch the newest frame in
        # a worker thread: drawing an overlay and encoding a JPEG inside the
        # event loop would freeze every API request and the WebSocket feed.
        manager.mark_live_view(camera_id)
        frame = await asyncio.to_thread(
            manager.latest_jpeg, camera_id, overlay=overlay, encode_if_missing=False
        )
        if frame is None:
            idle += 1
            if idle == 1 or idle % 40 == 0:
                yield (
                    f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n\r\n".encode()
                    + _placeholder_jpeg()
                    + b"\r\n"
                )
            await asyncio.sleep(interval)
            continue
        idle = 0
        if frame is not last:
            last = frame
            yield (
                f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: "
                f"{len(frame)}\r\n\r\n".encode()
                + frame
                + b"\r\n"
            )
        await asyncio.sleep(interval)


@router.get("/{camera_id}/live")
async def camera_live(
    camera: CameraDep,
    overlay: bool = False,
    fps: Annotated[int, Query(ge=1, le=60)] = 20,
) -> StreamingResponse:
    """MJPEG live view. ``overlay=true`` draws detected objects on the frame."""
    return StreamingResponse(
        _mjpeg_stream(camera.id, overlay, fps),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _placeholder_jpeg() -> bytes:
    import io

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (640, 360), "#0b1220")
    draw = ImageDraw.Draw(image)
    draw.text((20, 20), "CAMERASMART - no signal", fill="#94a3b8")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# zones
# --------------------------------------------------------------------------
@router.get("/{camera_id}/zones", response_model=list[ZoneRead])
async def list_zones(camera: CameraDep) -> list[Zone]:
    return list(camera.zones or [])


@router.post("/{camera_id}/zones", response_model=ZoneRead, status_code=status.HTTP_201_CREATED)
async def create_zone(
    camera: CameraDep, payload: ZoneCreate, session: SessionDep
) -> Zone:
    data = payload.model_dump(exclude={"camera_id"})
    if "alert_sound" not in payload.model_fields_set:
        # The global default is otherwise never read: honour it for new zones.
        data["alert_sound"] = settings.alert_sound_default
    zone = Zone(camera_id=camera.id, **data)
    session.add(zone)
    await session.commit()
    await session.refresh(zone)
    await _refresh_zones(camera.id)
    return zone


@router.patch("/{camera_id}/zones/{zone_id}", response_model=ZoneRead)
async def update_zone(
    camera: CameraDep, zone_id: int, payload: ZoneUpdate, session: SessionDep
) -> Zone:
    zone = await session.get(Zone, zone_id)
    if zone is None or zone.camera_id != camera.id:
        raise HTTPException(status_code=404, detail="ناحیه یافت نشد")
    data = payload.model_dump(exclude_unset=True)
    if data.get("kind") and data.get("points"):
        _validate_zone_shape(data["kind"], data["points"])
    for field, value in data.items():
        setattr(zone, field, value)
    if zone.kind == "line" and len(zone.points) != 2:
        raise HTTPException(status_code=422, detail="ناحیه خطی باید دقیقاً دو نقطه داشته باشد")
    if zone.kind != "line" and len(zone.points) < 3:
        raise HTTPException(status_code=422, detail="ناحیه چندضلعی باید حداقل سه نقطه داشته باشد")
    await session.commit()
    await session.refresh(zone)
    await _refresh_zones(camera.id)
    return zone


@router.delete("/{camera_id}/zones/{zone_id}", response_model=Message)
async def delete_zone(camera: CameraDep, zone_id: int, session: SessionDep) -> Message:
    zone = await session.get(Zone, zone_id)
    if zone is None or zone.camera_id != camera.id:
        raise HTTPException(status_code=404, detail="ناحیه یافت نشد")
    await session.delete(zone)
    await session.commit()
    await _refresh_zones(camera.id)
    return Message(detail="ناحیه حذف شد")


async def _refresh_zones(camera_id: int) -> None:
    """Push the new zone geometry into the running pipeline without a restart."""
    from sqlalchemy.orm import selectinload as _selectinload

    from app.core.database import session_scope

    async with session_scope() as session:
        camera = (
            await session.execute(
                select(Camera).options(_selectinload(Camera.zones)).where(Camera.id == camera_id)
            )
        ).scalar_one_or_none()
        if camera is None:
            return
        manager.update_zones(camera_id, zones_from_camera(camera))
        if manager.health(camera_id) is None:
            manager.apply_camera(camera)


def _validate_zone_shape(kind: str, points: list[list[float]]) -> None:
    if kind == "line" and len(points) != 2:
        raise HTTPException(status_code=422, detail="ناحیه خطی باید دقیقاً دو نقطه داشته باشد")
    if kind != "line" and len(points) < 3:
        raise HTTPException(status_code=422, detail="ناحیه چندضلعی باید حداقل سه نقطه داشته باشد")


def _parse_dt(value: str | None):
    if not value:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# Make the runtime config helper importable from the router module namespace so
# other modules (and tests) can reuse it.
__all__ = ["router", "runtime_config_from_camera"]

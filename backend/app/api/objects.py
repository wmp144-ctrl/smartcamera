"""Detected-object gallery endpoints (full + cropped images)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import SessionDep, TimeWindowDep, clamp_page
from app.models.camera import Camera
from app.models.detection import DIRECTIONS, ObjectImage, TrackRecord
from app.schemas.common import Message, Page
from app.schemas.detection import ObjectImageRead, TrackDetail
from app.services.geometry import direction_label_fa
from app.services.models_registry import class_label
from app.services.search import build_details, load_images

router = APIRouter(prefix="/api/objects", tags=["objects"])


@router.get("", response_model=Page[TrackDetail])
async def list_objects(
    session: SessionDep,
    window: TimeWindowDep,
    camera_id: Annotated[list[int] | None, Query()] = None,
    object_type: Annotated[list[str] | None, Query()] = None,
    direction: Annotated[list[str] | None, Query()] = None,
    motion_state: str | None = None,
    min_confidence: Annotated[float | None, Query(ge=0, le=1)] = None,
    min_quality: Annotated[float | None, Query(ge=0, le=1)] = None,
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 40,
) -> Page[TrackDetail]:
    page, page_size = clamp_page(page, page_size)
    stmt = select(TrackRecord)
    if camera_id:
        stmt = stmt.where(TrackRecord.camera_id.in_(camera_id))
    if object_type:
        stmt = stmt.where(TrackRecord.object_type.in_(object_type))
    if direction:
        stmt = stmt.where(TrackRecord.direction.in_(direction))
    if motion_state:
        stmt = stmt.where(TrackRecord.motion_state == motion_state)
    if min_confidence is not None:
        stmt = stmt.where(TrackRecord.confidence >= min_confidence)
    if window.start is not None:
        stmt = stmt.where(TrackRecord.last_seen >= window.start)
    if window.end is not None:
        stmt = stmt.where(TrackRecord.first_seen < window.end)
    if min_quality is not None:
        stmt = stmt.join(ObjectImage, ObjectImage.id == TrackRecord.best_image_id).where(
            ObjectImage.quality_score >= min_quality
        )

    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    )
    order_col = TrackRecord.first_seen.asc() if order == "asc" else TrackRecord.first_seen.desc()
    tracks = (
        (
            await session.execute(
                stmt.order_by(order_col).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    details = await build_details(session, list(tracks))
    return Page[TrackDetail](items=details, total=total, page=page, page_size=page_size)


@router.get("/filters", response_model=dict)
async def object_filters(session: SessionDep) -> dict:
    """Everything the gallery/search side-bars need for their dropdowns."""
    type_rows = (
        await session.execute(
            select(TrackRecord.object_type, func.count())
            .group_by(TrackRecord.object_type)
            .order_by(func.count().desc())
        )
    ).all()
    direction_rows = (
        await session.execute(
            select(TrackRecord.direction, func.count()).group_by(TrackRecord.direction)
        )
    ).all()
    cameras = (await session.execute(select(Camera).order_by(Camera.id))).scalars().all()
    min_max = (
        await session.execute(
            select(func.min(TrackRecord.first_seen), func.max(TrackRecord.first_seen))
        )
    ).one()
    return {
        "object_types": [
            {"value": key, "label": class_label(key), "count": count}
            for key, count in type_rows
            if key
        ],
        "directions": [
            {"value": key, "label": direction_label_fa(key), "count": count}
            for key, count in direction_rows
            if key
        ],
        "directions_all": [
            {"value": key, "label": direction_label_fa(key)} for key in DIRECTIONS
        ],
        "cameras": [{"value": camera.id, "label": camera.name} for camera in cameras],
        "range": {
            "min": min_max[0].isoformat() if min_max[0] else None,
            "max": min_max[1].isoformat() if min_max[1] else None,
        },
    }


@router.get("/{track_id}", response_model=TrackDetail)
async def get_object(track_id: int, session: SessionDep) -> TrackDetail:
    track = await session.get(TrackRecord, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="شیء یافت نشد")
    details = await build_details(session, [track])
    return details[0]


@router.get("/{track_id}/images", response_model=list[ObjectImageRead])
async def get_object_images(track_id: int, session: SessionDep) -> list[ObjectImage]:
    track = await session.get(TrackRecord, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="شیء یافت نشد")
    images = await load_images(session, [track_id])
    return images.get(track_id, [])


@router.delete("/{track_id}", response_model=Message)
async def delete_object(track_id: int, session: SessionDep) -> Message:
    from app.services.storage import delete_relative

    track = await session.get(TrackRecord, track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="شیء یافت نشد")
    images = await load_images(session, [track_id])
    for image in images.get(track_id, []):
        for path in (image.full_path, image.crop_path, image.thumb_path):
            delete_relative(path)
    await session.delete(track)
    await session.commit()
    return Message(detail="شیء حذف شد")


@router.get("/{track_id}/best", response_model=ObjectImageRead | None)
async def get_best_image(track_id: int, session: SessionDep) -> ObjectImage | None:
    images = await load_images(session, [track_id], include_all=False)
    track_images = images.get(track_id) or []
    return track_images[0] if track_images else None


__all__ = ["router"]

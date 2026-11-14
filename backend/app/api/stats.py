"""Dashboard statistics, heatmap and storage endpoints."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import SessionDep, TimeWindowDep
from app.core.config import settings
from app.core.timeutil import jalali_label, to_local, utcnow
from app.models.camera import Camera
from app.models.detection import Event, TrackRecord
from app.schemas.system import (
    CountByKey,
    HeatmapResponse,
    OverviewStats,
    StorageUsage,
)
from app.services import stats as stats_service
from app.services.camera_manager import manager
from app.services.heatmap import get_heatmap
from app.services.storage import storage_usage_async

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/overview", response_model=OverviewStats)
async def overview(session: SessionDep, days: Annotated[int, Query(ge=1, le=365)] = 14) -> OverviewStats:
    usage = await storage_usage_async()
    return await stats_service.overview(
        session,
        states=manager.states(),
        fps=manager.fps_map(),
        storage_bytes=int(usage["total_bytes"]),
        days=days,
    )


@router.get("/cameras", response_model=list[stats_service.CameraStats])
async def per_camera_stats(
    session: SessionDep, days: Annotated[int, Query(ge=1, le=365)] = 7
) -> list[dict]:
    window_start = utcnow() - timedelta(days=days)
    return await stats_service.camera_stats(
        session, states=manager.states(), fps=manager.fps_map(), window_start=window_start
    )


@router.get("/activity", response_model=list)
async def activity(
    session: SessionDep,
    window: TimeWindowDep,
    camera_id: Annotated[list[int] | None, Query()] = None,
    hourly: bool = True,
) -> list[dict]:
    if window.start is None or window.end is None:
        end = utcnow()
        start = end - timedelta(days=1 if hourly else 14)
    else:
        start, end = window.start, window.end
    buckets = await stats_service.activity(
        session, start=start, end=end, camera_ids=camera_id, hourly=hourly
    )
    return [bucket.model_dump() for bucket in buckets]


@router.get("/objects", response_model=list[CountByKey])
async def objects_by_type(
    session: SessionDep,
    window: TimeWindowDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[CountByKey]:
    return await stats_service.counts_by(
        session,
        TrackRecord.object_type,
        start=window.start,
        end=window.end,
        limit=limit,
    )


@router.get("/directions", response_model=list[CountByKey])
async def objects_by_direction(
    session: SessionDep,
    window: TimeWindowDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[CountByKey]:
    return await stats_service.counts_by(
        session,
        TrackRecord.direction,
        start=window.start,
        end=window.end,
        limit=limit,
    )


@router.get("/event-types", response_model=list[CountByKey])
async def events_by_type(
    session: SessionDep,
    window: TimeWindowDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[CountByKey]:
    return await stats_service.counts_by(
        session,
        Event.event_type,
        start=window.start,
        end=window.end,
        limit=limit,
        model=Event,
        time_column=Event.occurred_at,
    )


@router.get("/busiest-hours", response_model=list[CountByKey])
async def busiest_hours(
    session: SessionDep, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> list[CountByKey]:
    return await stats_service.busiest_hours(session, days=days)


@router.get("/top-zones", response_model=list[CountByKey])
async def top_zones(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=50)] = 10
) -> list[CountByKey]:
    return await stats_service.top_zones(session, limit=limit)


@router.get("/heatmap/{camera_id}", response_model=HeatmapResponse)
async def heatmap(
    camera_id: int,
    session: SessionDep,
    day: Annotated[str | None, Query(description="YYYY-MM-DD (local) or Jalali YYYY/MM/DD")] = None,
    object_type: str | None = None,
    days_back: Annotated[int, Query(ge=0, le=365)] = 0,
    grid_w: Annotated[int | None, Query(ge=4, le=160, description="ستون‌های شبکه")] = None,
    grid_h: Annotated[int | None, Query(ge=4, le=120, description="ردیف‌های شبکه")] = None,
    hour_from: Annotated[int | None, Query(ge=0, le=23)] = None,
    hour_to: Annotated[int | None, Query(ge=0, le=23)] = None,
) -> HeatmapResponse:
    camera = await session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="دوربین یافت نشد")

    target: date | None = None
    if day:
        target = _parse_day(day)
    elif days_back:
        local = to_local(utcnow(), settings.timezone)
        target = (local.date() if local else date.today()) - timedelta(days=days_back)

    data = await get_heatmap(
        session,
        camera_id,
        day=target,
        object_type=object_type,
        grid_w=grid_w,
        grid_h=grid_h,
        hour_from=hour_from,
        hour_to=hour_to,
    )
    if target is not None:
        data["jalali"] = jalali_label(target)
    return HeatmapResponse(**data)


@router.get("/storage", response_model=StorageUsage)
async def storage() -> StorageUsage:
    usage = await storage_usage_async()
    return StorageUsage(**usage)


def _parse_day(value: str) -> date:
    text = value.strip().replace("-", "/")
    parts = text.split("/")
    if len(parts) != 3:
        raise HTTPException(status_code=422, detail="قالب تاریخ نامعتبر است")
    try:
        year, month, day = (int(part) for part in parts)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="قالب تاریخ نامعتبر است") from exc

    if year < 1700:  # Jalali
        from app.core.timeutil import jalali_to_gregorian

        gy, gm, gd = jalali_to_gregorian(year, month, day)
        try:
            return date(gy, gm, gd)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="تاریخ شمسی نامعتبر است") from exc
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="تاریخ میلادی نامعتبر است") from exc

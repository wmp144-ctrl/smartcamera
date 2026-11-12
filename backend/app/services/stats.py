"""Dashboard aggregations.

All bucketing is done on **local** (display-timezone) day/hour boundaries so the
charts line up with what a Persian operator sees, while the underlying query
windows are UTC.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.timeutil import UTC, jalali_label, to_local, utcnow
from app.models.camera import Camera
from app.models.detection import Event, ObjectImage, TrackRecord
from app.models.system import Alert
from app.schemas.detection import EventRead
from app.schemas.system import (
    CameraStats,
    CountByKey,
    OverviewStats,
    TimeBucket,
)


def local_day_bounds(moment: datetime | None = None) -> tuple[datetime, datetime]:
    """UTC [start, end) of the local calendar day containing ``moment``.

    The bounds must be **UTC**: SQLite stores the wall clock of whatever it is
    handed and ignores ``tzinfo``, so passing a Tehran-aware midnight would be
    compared against UTC values and shift every "today" filter by +03:30.
    """
    moment = moment or utcnow()
    local = to_local(moment, settings.timezone) or moment
    start_local = datetime(local.year, local.month, local.day, tzinfo=local.tzinfo)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


async def _count(session: AsyncSession, stmt) -> int:
    return int((await session.execute(stmt)).scalar() or 0)


async def counts_by(
    session: AsyncSession,
    column,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 20,
    model=TrackRecord,
    time_column=None,
) -> list[CountByKey]:
    time_column = time_column or getattr(model, "first_seen", None)
    stmt = select(column, func.count()).select_from(model)
    if time_column is not None and start is not None:
        stmt = stmt.where(time_column >= start)
    if time_column is not None and end is not None:
        stmt = stmt.where(time_column < end)
    stmt = stmt.group_by(column).order_by(func.count().desc()).limit(limit)
    rows = (await session.execute(stmt)).all()
    return [CountByKey(key=str(key or "unknown"), count=int(count)) for key, count in rows]


def _bucket(
    moments: list[datetime],
    *,
    start: datetime,
    end: datetime,
    hourly: bool,
) -> list[TimeBucket]:
    """Bucket UTC datetimes into local hour/day buckets covering [start, end)."""
    start_local = to_local(start, settings.timezone) or start
    end_local = to_local(end, settings.timezone) or end
    step = timedelta(hours=1) if hourly else timedelta(days=1)

    cursor = start_local.replace(minute=0, second=0, microsecond=0) if hourly else start_local.replace(hour=0, minute=0, second=0, microsecond=0)
    slots: list[datetime] = []
    while cursor < end_local:
        slots.append(cursor)
        cursor = cursor + step

    index = {slot: i for i, slot in enumerate(slots)}
    counts = [0] * len(slots)
    for moment in moments:
        local = to_local(moment, settings.timezone)
        if local is None:
            continue
        key = (
            local.replace(minute=0, second=0, microsecond=0)
            if hourly
            else local.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        position = index.get(key)
        if position is not None:
            counts[position] += 1

    return [
        TimeBucket(bucket=slot.astimezone(slot.tzinfo).isoformat(), jalali=jalali_label(slot), count=counts[i])
        for i, slot in enumerate(slots)
    ]


async def activity(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    camera_ids: list[int] | None = None,
    hourly: bool = True,
) -> list[TimeBucket]:
    stmt = select(TrackRecord.first_seen).where(
        TrackRecord.first_seen >= start, TrackRecord.first_seen < end
    )
    if camera_ids:
        stmt = stmt.where(TrackRecord.camera_id.in_(camera_ids))
    moments = [row[0] for row in (await session.execute(stmt)).all()]
    return _bucket(moments, start=start, end=end, hourly=hourly)


async def camera_stats(
    session: AsyncSession,
    *,
    states: dict[int, str] | None = None,
    fps: dict[int, float] | None = None,
    window_start: datetime | None = None,
) -> list[CameraStats]:
    cameras = (await session.execute(select(Camera).order_by(Camera.id))).scalars().all()
    states = states or {}
    fps = fps or {}

    results: list[CameraStats] = []
    for camera in cameras:
        track_stmt = select(func.count()).select_from(TrackRecord).where(
            TrackRecord.camera_id == camera.id
        )
        event_stmt = select(func.count()).select_from(Event).where(Event.camera_id == camera.id)
        alert_stmt = select(func.count()).select_from(Alert).where(Alert.camera_id == camera.id)
        if window_start is not None:
            track_stmt = track_stmt.where(TrackRecord.first_seen >= window_start)
            event_stmt = event_stmt.where(Event.occurred_at >= window_start)
            alert_stmt = alert_stmt.where(Alert.triggered_at >= window_start)

        by_type_rows = (
            await session.execute(
                select(TrackRecord.object_type, func.count())
                .where(TrackRecord.camera_id == camera.id)
                .group_by(TrackRecord.object_type)
                .order_by(func.count().desc())
                .limit(10)
            )
        ).all()

        results.append(
            CameraStats(
                camera_id=camera.id,
                camera_name=camera.name,
                total_tracks=await _count(session, track_stmt),
                total_events=await _count(session, event_stmt),
                total_alerts=await _count(session, alert_stmt),
                by_type=[
                    CountByKey(key=str(key or "unknown"), count=int(count))
                    for key, count in by_type_rows
                ],
                state=states.get(camera.id, "unknown"),
                fps=float(fps.get(camera.id, 0.0)),
            )
        )
    return results


async def overview(
    session: AsyncSession,
    *,
    states: dict[int, str] | None = None,
    fps: dict[int, float] | None = None,
    storage_bytes: int = 0,
    recent_events: int = 8,
    days: int = 14,
) -> OverviewStats:
    now = utcnow()
    day_start, day_end = local_day_bounds(now)
    states = states or {}

    total_cameras = await _count(session, select(func.count()).select_from(Camera))
    by_type = await counts_by(session, TrackRecord.object_type)
    by_direction = await counts_by(session, TrackRecord.direction)
    by_event_type = await counts_by(
        session,
        Event.event_type,
        model=Event,
        time_column=Event.occurred_at,
    )

    hourly = await activity(session, start=day_start, end=day_end, hourly=True)
    # ``days`` is the window the client asked for (the dashboard range selector).
    daily = await activity(
        session, start=day_start - timedelta(days=max(1, days) - 1), end=day_end, hourly=False
    )

    today_tracks = await _count(
        session,
        select(func.count())
        .select_from(TrackRecord)
        .where(TrackRecord.first_seen >= day_start, TrackRecord.first_seen < day_end),
    )
    today_events = await _count(
        session,
        select(func.count())
        .select_from(Event)
        .where(Event.occurred_at >= day_start, Event.occurred_at < day_end),
    )
    today_images = await _count(
        session,
        select(func.count())
        .select_from(ObjectImage)
        .where(ObjectImage.captured_at >= day_start, ObjectImage.captured_at < day_end),
    )

    online = sum(1 for state in states.values() if state == "online")
    offline = sum(1 for state in states.values() if state in {"offline", "error"})

    recent = (
        (
            await session.execute(
                select(Event).order_by(Event.occurred_at.desc()).limit(recent_events)
            )
        )
        .scalars()
        .all()
    )

    return OverviewStats(
        generated_at=now,
        total_cameras=total_cameras,
        online_cameras=online,
        offline_cameras=offline,
        total_tracks=await _count(session, select(func.count()).select_from(TrackRecord)),
        total_events=await _count(session, select(func.count()).select_from(Event)),
        total_alerts=await _count(session, select(func.count()).select_from(Alert)),
        unacknowledged_alerts=await _count(
            session,
            select(func.count()).select_from(Alert).where(Alert.acknowledged.is_(False)),
        ),
        tracks_today=today_tracks,
        events_today=today_events,
        objects_today=today_images,
        storage_bytes=storage_bytes,
        by_type=by_type,
        by_direction=by_direction,
        by_event_type=by_event_type,
        hourly=hourly,
        daily=daily,
        cameras=await camera_stats(session, states=states, fps=fps),
        recent_events=[EventRead.model_validate(event) for event in recent],
    )


async def top_zones(session: AsyncSession, limit: int = 10) -> list[CountByKey]:
    rows = (
        await session.execute(
            select(Event.zone_id, func.count())
            .where(Event.zone_id.is_not(None))
            .group_by(Event.zone_id)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return [CountByKey(key=str(zone_id), count=int(count)) for zone_id, count in rows]


async def busiest_hours(session: AsyncSession, days: int = 7) -> list[CountByKey]:
    """Histogram of detections by local hour-of-day (for 'peak activity' charts)."""
    start = utcnow() - timedelta(days=days)
    moments = [
        row[0]
        for row in (
            await session.execute(
                select(TrackRecord.first_seen).where(TrackRecord.first_seen >= start)
            )
        ).all()
    ]
    counter: Counter[int] = Counter()
    for moment in moments:
        local = to_local(moment, settings.timezone)
        if local is not None:
            counter[local.hour] += 1
    return [
        CountByKey(key=f"{hour:02d}", count=counter.get(hour, 0)) for hour in range(24)
    ]


def jalal_range_label(start: date, end: date) -> str:
    return f"{jalali_label(start)} - {jalali_label(end)}"

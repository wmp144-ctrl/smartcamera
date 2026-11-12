"""Search stored objects.

The spatial search answers: *"show me every object of this type, moving in this
direction, in this part of the frame, between these times"*. It pre-filters in
SQL on the denormalised object centre (cheap bounding-box test) and then applies
an exact point-in-polygon test in Python, so the expensive part only ever runs
over a small candidate set.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.detection import Event, ObjectImage, TrackRecord
from app.schemas.detection import ObjectImageRead, TrackDetail
from app.services.geometry import point_in_polygon

MAX_SCAN = 20000


def polygon_bounds(polygon: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _base_query(
    *,
    camera_ids: list[int] | None,
    start: datetime | None,
    end: datetime | None,
    object_types: list[str] | None,
    directions: list[str] | None,
    motion_state: str | None,
    min_confidence: float | None,
    polygon: list[list[float]] | None,
):
    stmt = select(TrackRecord)
    if camera_ids:
        stmt = stmt.where(TrackRecord.camera_id.in_(camera_ids))
    if start is not None:
        stmt = stmt.where(TrackRecord.last_seen >= start)
    if end is not None:
        stmt = stmt.where(TrackRecord.first_seen < end)
    if object_types:
        stmt = stmt.where(TrackRecord.object_type.in_(object_types))
    if directions:
        stmt = stmt.where(TrackRecord.direction.in_(directions))
    if motion_state:
        stmt = stmt.where(TrackRecord.motion_state == motion_state)
    if min_confidence is not None:
        stmt = stmt.where(TrackRecord.confidence >= min_confidence)
    if polygon:
        min_x, min_y, max_x, max_y = polygon_bounds(polygon)
        stmt = stmt.where(
            TrackRecord.center_x.is_not(None),
            TrackRecord.center_x >= min_x,
            TrackRecord.center_x <= max_x,
            TrackRecord.center_y >= min_y,
            TrackRecord.center_y <= max_y,
        )
    return stmt


async def query_tracks(
    session: AsyncSession,
    *,
    camera_ids: list[int] | None = None,
    polygon: list[list[float]] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    object_types: list[str] | None = None,
    directions: list[str] | None = None,
    motion_state: str | None = None,
    min_confidence: float | None = None,
    min_quality: float | None = None,
    limit: int = 200,
    offset: int = 0,
    order: str = "desc",
) -> tuple[int, list[TrackRecord]]:
    """Return ``(total, tracks)`` matching the filters."""
    stmt = _base_query(
        camera_ids=camera_ids,
        start=start,
        end=end,
        object_types=object_types,
        directions=directions,
        motion_state=motion_state,
        min_confidence=min_confidence,
        polygon=polygon,
    )
    if min_quality is not None:
        stmt = stmt.join(ObjectImage, ObjectImage.id == TrackRecord.best_image_id).where(
            ObjectImage.quality_score >= min_quality
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_candidates = int((await session.execute(count_stmt)).scalar() or 0)

    order_col = TrackRecord.first_seen.asc() if order == "asc" else TrackRecord.first_seen.desc()

    if polygon:
        # The bounding-box prefilter is approximate, so fetch candidates and
        # apply the exact polygon test before paginating.
        scan_limit = min(offset + limit * 4, MAX_SCAN)
        rows = (
            (await session.execute(stmt.order_by(order_col).limit(scan_limit))).scalars().all()
        )
        exact = [
            row
            for row in rows
            if row.center_x is not None
            and point_in_polygon((row.center_x, row.center_y or 0.0), polygon)
        ]
        # ``len(exact)`` is exact when the whole candidate set was scanned.
        return len(exact), exact[offset : offset + limit]

    rows = (
        (await session.execute(stmt.order_by(order_col).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return total_candidates, list(rows)


async def load_images(
    session: AsyncSession, track_ids: list[int], *, include_all: bool = True
) -> dict[int, list[ObjectImage]]:
    if not track_ids:
        return {}
    stmt = (
        select(ObjectImage)
        .where(ObjectImage.track_id.in_(track_ids))
        .order_by(ObjectImage.is_best.desc(), ObjectImage.captured_at.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    result: dict[int, list[ObjectImage]] = {}
    for row in rows:
        bucket = result.setdefault(row.track_id, [])
        if include_all or row.is_best:
            bucket.append(row)
    return result


async def build_details(
    session: AsyncSession, tracks: list[TrackRecord]
) -> list[TrackDetail]:
    """Attach images (full + cropped) to each track for the UI."""
    if not tracks:
        return []
    images = await load_images(session, [t.id for t in tracks])
    columns = [column.name for column in TrackRecord.__table__.columns]
    details: list[TrackDetail] = []
    for track in tracks:
        track_images = images.get(track.id, [])
        best = next((img for img in track_images if img.is_best), None)
        if best is None and track_images:
            best = track_images[0]
        base = {name: getattr(track, name) for name in columns}
        details.append(
            TrackDetail(
                **base,
                images=[ObjectImageRead.model_validate(img) for img in track_images],
                best_image=ObjectImageRead.model_validate(best) if best is not None else None,
            )
        )
    return details


async def nearby_events(
    session: AsyncSession,
    *,
    camera_id: int,
    start: datetime | None,
    end: datetime | None,
    limit: int = 50,
) -> list[Event]:
    stmt = select(Event).where(Event.camera_id == camera_id)
    if start is not None:
        stmt = stmt.where(Event.occurred_at >= start)
    if end is not None:
        stmt = stmt.where(Event.occurred_at < end)
    return list(
        (await session.execute(stmt.order_by(Event.occurred_at.desc()).limit(limit)))
        .scalars()
        .all()
    )

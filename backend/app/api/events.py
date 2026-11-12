"""Event log endpoints (the dedicated events section)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select, update

from app.api.deps import SessionDep, TimeWindowDep, clamp_page
from app.core.timeutil import utcnow
from app.models.detection import EVENT_TYPES, Event
from app.schemas.common import Message, Page
from app.schemas.detection import EventRead

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=Page[EventRead])
async def list_events(
    session: SessionDep,
    window: TimeWindowDep,
    camera_id: Annotated[list[int] | None, Query()] = None,
    event_type: Annotated[list[str] | None, Query()] = None,
    object_type: Annotated[list[str] | None, Query()] = None,
    severity: Annotated[list[str] | None, Query()] = None,
    direction: Annotated[list[str] | None, Query()] = None,
    acknowledged: bool | None = None,
    search: str | None = None,
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[EventRead]:
    page, page_size = clamp_page(page, page_size)
    stmt = select(Event)
    if camera_id:
        stmt = stmt.where(Event.camera_id.in_(camera_id))
    if event_type:
        stmt = stmt.where(Event.event_type.in_(event_type))
    if object_type:
        stmt = stmt.where(Event.object_type.in_(object_type))
    if severity:
        stmt = stmt.where(Event.severity.in_(severity))
    if direction:
        stmt = stmt.where(Event.direction.in_(direction))
    if acknowledged is not None:
        stmt = stmt.where(Event.acknowledged.is_(acknowledged))
    if window.start is not None:
        stmt = stmt.where(Event.occurred_at >= window.start)
    if window.end is not None:
        stmt = stmt.where(Event.occurred_at < window.end)
    if search:
        stmt = stmt.where(Event.message.ilike(f"%{search}%"))

    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    )
    order_col = Event.occurred_at.asc() if order == "asc" else Event.occurred_at.desc()
    rows = (
        (
            await session.execute(
                stmt.order_by(order_col).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return Page[EventRead](
        items=[EventRead.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/summary", response_model=dict)
async def event_summary(
    session: SessionDep,
    window: TimeWindowDep,
    camera_id: Annotated[list[int] | None, Query()] = None,
) -> dict:
    stmt = select(Event.event_type, func.count()).group_by(Event.event_type)
    if camera_id:
        stmt = stmt.where(Event.camera_id.in_(camera_id))
    if window.start is not None:
        stmt = stmt.where(Event.occurred_at >= window.start)
    if window.end is not None:
        stmt = stmt.where(Event.occurred_at < window.end)
    counts = {key: int(value) for key, value in (await session.execute(stmt)).all()}
    unack = int(
        (
            await session.execute(
                select(func.count()).select_from(Event).where(Event.acknowledged.is_(False))
            )
        ).scalar()
        or 0
    )
    return {
        "by_type": counts,
        "types": list(EVENT_TYPES),
        "unacknowledged": unack,
    }


@router.get("/{event_id}", response_model=EventRead)
async def get_event(event_id: int, session: SessionDep) -> EventRead:
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="رویداد یافت نشد")
    return EventRead.model_validate(event)


@router.post("/{event_id}/acknowledge", response_model=EventRead)
async def acknowledge_event(event_id: int, session: SessionDep) -> EventRead:
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="رویداد یافت نشد")
    event.acknowledged = True
    event.acknowledged_at = utcnow()
    await session.commit()
    await session.refresh(event)
    return EventRead.model_validate(event)


@router.post("/acknowledge-all", response_model=Message)
async def acknowledge_all(session: SessionDep) -> Message:
    result = await session.execute(
        update(Event).where(Event.acknowledged.is_(False)).values(acknowledged=True, acknowledged_at=utcnow())
    )
    await session.commit()
    return Message(detail=f"{result.rowcount or 0} رویداد تأیید شد")


@router.delete("/{event_id}", response_model=Message)
async def delete_event(event_id: int, session: SessionDep) -> Message:
    event = await session.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="رویداد یافت نشد")
    await session.delete(event)
    await session.commit()
    return Message(detail="رویداد حذف شد")

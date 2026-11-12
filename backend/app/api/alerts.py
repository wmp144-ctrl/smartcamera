"""Alert endpoints (sound + notification centre)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select, update

from app.api.deps import SessionDep, TimeWindowDep, clamp_page
from app.core.timeutil import utcnow
from app.models.system import Alert
from app.schemas.common import Message, Page
from app.schemas.system import AlertRead

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("", response_model=Page[AlertRead])
async def list_alerts(
    session: SessionDep,
    window: TimeWindowDep,
    camera_id: Annotated[list[int] | None, Query()] = None,
    severity: Annotated[list[str] | None, Query()] = None,
    acknowledged: bool | None = None,
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[AlertRead]:
    page, page_size = clamp_page(page, page_size)
    stmt = select(Alert)
    if camera_id:
        stmt = stmt.where(Alert.camera_id.in_(camera_id))
    if severity:
        stmt = stmt.where(Alert.severity.in_(severity))
    if acknowledged is not None:
        stmt = stmt.where(Alert.acknowledged.is_(acknowledged))
    if window.start is not None:
        stmt = stmt.where(Alert.triggered_at >= window.start)
    if window.end is not None:
        stmt = stmt.where(Alert.triggered_at < window.end)

    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    )
    order_col = Alert.triggered_at.asc() if order == "asc" else Alert.triggered_at.desc()
    rows = (
        (
            await session.execute(
                stmt.order_by(order_col).offset((page - 1) * page_size).limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return Page[AlertRead](
        items=[AlertRead.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/unread", response_model=dict)
async def unread_count(session: SessionDep) -> dict:
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(Alert).where(Alert.acknowledged.is_(False))
            )
        ).scalar()
        or 0
    )
    latest = (
        (
            await session.execute(
                select(Alert).where(Alert.acknowledged.is_(False)).order_by(Alert.triggered_at.desc()).limit(5)
            )
        )
        .scalars()
        .all()
    )
    return {
        "unread": total,
        "latest": [AlertRead.model_validate(alert).model_dump(mode="json") for alert in latest],
    }


@router.post("/{alert_id}/acknowledge", response_model=AlertRead)
async def acknowledge_alert(alert_id: int, session: SessionDep) -> AlertRead:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="هشدار یافت نشد")
    alert.acknowledged = True
    alert.acknowledged_at = utcnow()
    await session.commit()
    await session.refresh(alert)
    return AlertRead.model_validate(alert)


@router.post("/acknowledge-all", response_model=Message)
async def acknowledge_all(session: SessionDep) -> Message:
    result = await session.execute(
        update(Alert)
        .where(Alert.acknowledged.is_(False))
        .values(acknowledged=True, acknowledged_at=utcnow())
    )
    await session.commit()
    return Message(detail=f"{result.rowcount or 0} هشدار تأیید شد")

"""Shared API dependencies and helpers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_session
from app.core.timeutil import parse_dt
from app.models.camera import Camera
from app.services.camera_manager import manager

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_camera(camera_id: int, session: SessionDep) -> Camera:
    camera = (
        await session.execute(
            select(Camera).options(selectinload(Camera.zones)).where(Camera.id == camera_id)
        )
    ).scalar_one_or_none()
    if camera is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="دوربین یافت نشد")
    return camera


CameraDep = Annotated[Camera, Depends(get_camera)]


class TimeWindow:
    """Parsed ``start``/``end`` query parameters (UTC or local ISO strings)."""

    def __init__(
        self,
        start: Annotated[str | None, Query(description="ISO datetime (UTC or local)")] = None,
        end: Annotated[str | None, Query(description="ISO datetime (UTC or local)")] = None,
    ) -> None:
        self.start = parse_dt(start)
        self.end = parse_dt(end)
        if self.start and self.end and self.start > self.end:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="بازه زمانی نامعتبر است: start بعد از end است",
            )


TimeWindowDep = Annotated[TimeWindow, Depends(TimeWindow)]


def clamp_page(page: int, page_size: int) -> tuple[int, int]:
    page = max(1, page)
    page_size = min(max(1, page_size), 500)
    return page, page_size

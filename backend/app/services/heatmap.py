"""Object heatmap: where do objects appear on each camera?

Detections are accumulated in memory on a coarse grid and flushed to the
database periodically (the pipeline flushes every few seconds), so the hot path
never blocks on a write.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.timeutil import day_bounds_utc, to_local, utcnow
from app.models.detection import TrackRecord
from app.models.system import HeatmapCell
from app.services.geometry import grid_cell

logger = logging.getLogger(__name__)

CellKey = tuple[int, str, int, int, int, int, str]  # camera, day, gw, gh, x, y, type


class HeatmapBuffer:
    """In-memory accumulation buffer for heatmap increments."""

    def __init__(self, grid_w: int | None = None, grid_h: int | None = None) -> None:
        self.grid_w = grid_w or settings.heatmap_grid_w
        self.grid_h = grid_h or settings.heatmap_grid_h
        self._pending: dict[CellKey, int] = defaultdict(int)
        self._tz_offsets: dict[str, date] = {}

    def add(
        self,
        camera_id: int,
        point: tuple[float, float],
        *,
        object_type: str = "*",
        when: datetime | None = None,
    ) -> None:
        when = when or utcnow()
        local = to_local(when, settings.timezone) or when
        day = local.date().isoformat()
        cell_x, cell_y = grid_cell(point, self.grid_w, self.grid_h)
        key: CellKey = (
            camera_id,
            day,
            self.grid_w,
            self.grid_h,
            cell_x,
            cell_y,
            object_type or "*",
        )
        self._pending[key] += 1

    @property
    def pending_count(self) -> int:
        return sum(self._pending.values())

    def clear(self) -> None:
        self._pending.clear()

    def drain(self) -> dict[CellKey, int]:
        data = dict(self._pending)
        self._pending.clear()
        return data

    def snapshot(self) -> dict[CellKey, int]:
        """Copy of the pending increments **without** clearing them.

        ``flush_buffer`` acknowledges a snapshot only after the write succeeded,
        so a failed flush (a locked database, for example) cannot silently drop
        the counts it was about to store.
        """
        return dict(self._pending)

    def acknowledge(self, written: dict[CellKey, int]) -> None:
        """Subtract the successfully written increments from the buffer."""
        for key, count in written.items():
            remaining = self._pending.get(key, 0) - count
            if remaining > 0:
                self._pending[key] = remaining
            else:
                self._pending.pop(key, None)


CONFLICT_COLUMNS = (
    "camera_id",
    "day",
    "grid_w",
    "grid_h",
    "cell_x",
    "cell_y",
    "object_type",
)


def _sqlite_increment_stmt(key: CellKey, count: int, *, now):  # noqa: ANN001, ANN202
    """One atomic "add ``count`` to this cell" statement for SQLite.

    A real ``INSERT .. ON CONFLICT DO UPDATE`` means the write never follows a
    read inside the same transaction: under WAL, a read snapshot that is
    upgraded to a write *after* another writer committed fails immediately with
    ``SQLITE_BUSY_SNAPSHOT`` (surfacing as "database is locked").
    """
    camera_id, day_str, gw, gh, cell_x, cell_y, object_type = key
    statement = sqlite_insert(HeatmapCell).values(
        camera_id=camera_id,
        day=date.fromisoformat(day_str),
        grid_w=gw,
        grid_h=gh,
        cell_x=cell_x,
        cell_y=cell_y,
        object_type=object_type,
        count=count,
        updated_at=now,
    )
    return statement.on_conflict_do_update(
        index_elements=list(CONFLICT_COLUMNS),
        set_={"count": HeatmapCell.count + statement.excluded.count, "updated_at": now},
    )


async def _increment_fallback(
    session: AsyncSession, key: CellKey, count: int, *, now  # noqa: ANN001
) -> None:
    """Portable (non-SQLite) read-modify-write upsert."""
    camera_id, day_str, gw, gh, cell_x, cell_y, object_type = key
    day = date.fromisoformat(day_str)
    row = (
        await session.execute(
            select(HeatmapCell).where(
                HeatmapCell.camera_id == camera_id,
                HeatmapCell.day == day,
                HeatmapCell.grid_w == gw,
                HeatmapCell.grid_h == gh,
                HeatmapCell.cell_x == cell_x,
                HeatmapCell.cell_y == cell_y,
                HeatmapCell.object_type == object_type,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        session.add(
            HeatmapCell(
                camera_id=camera_id,
                day=day,
                grid_w=gw,
                grid_h=gh,
                cell_x=cell_x,
                cell_y=cell_y,
                object_type=object_type,
                count=count,
                updated_at=now,
            )
        )
    else:
        row.count += count
        row.updated_at = now


async def _write_increments(session: AsyncSession, data: dict[CellKey, int]) -> int:
    dialect = session.get_bind().dialect.name
    now = utcnow()
    for key, count in data.items():
        if dialect == "sqlite":
            await session.execute(_sqlite_increment_stmt(key, count, now=now))
        else:
            await _increment_fallback(session, key, count, now=now)
    return len(data)


async def flush_buffer(session: AsyncSession, buffer: HeatmapBuffer) -> int:
    """Persist buffered increments (atomic upsert per cell).

    The buffer is drained only *after* a successful write, so a locked database
    degrades into a retry instead of losing the counts.
    """
    data = buffer.snapshot()
    if not data:
        return 0

    try:
        written = await _write_increments(session, data)
        await session.flush()
    except OperationalError:
        # Another writer holds the database; the increments stay buffered and
        # the next flush interval tries again with them.
        await session.rollback()
        logger.warning("heatmap flush hit a locked database (%d cells kept)", len(data))
        return 0

    buffer.acknowledge(data)
    return written


#: The stored grid is fixed (``settings.heatmap_grid_*``); the UI may ask for a
#: coarser or finer one and the answer is resampled from it.
MIN_GRID = 4
MAX_GRID_W = 160
MAX_GRID_H = 120


def clamp_grid(grid_w: int | None, grid_h: int | None) -> tuple[int, int]:
    width = int(grid_w or settings.heatmap_grid_w)
    height = int(grid_h or settings.heatmap_grid_h)
    return (
        max(MIN_GRID, min(MAX_GRID_W, width)),
        max(MIN_GRID, min(MAX_GRID_H, height)),
    )


def rebin(
    cells: list[int], src_w: int, src_h: int, dst_w: int, dst_h: int
) -> list[int]:
    """Resample a grid to another resolution, weighting cells by overlap.

    Stored cells are one day (and one object type) each; a finer target grid is
    filled by splitting a source cell over the target cells it covers, so the
    total count stays (almost) unchanged.
    """
    if src_w <= 0 or src_h <= 0 or not cells:
        return [0] * (dst_w * dst_h)
    if (src_w, src_h) == (dst_w, dst_h):
        return list(cells)

    weights: dict[tuple[int, int], float] = defaultdict(float)
    for row in range(src_h):
        for column in range(src_w):
            index = row * src_w + column
            count = cells[index] if index < len(cells) else 0
            if not count:
                continue
            x0, x1 = column / src_w, (column + 1) / src_w
            y0, y1 = row / src_h, (row + 1) / src_h
            tx0, tx1 = int(x0 * dst_w), min(dst_w, int(x1 * dst_w) + 1)
            ty0, ty1 = int(y0 * dst_h), min(dst_h, int(y1 * dst_h) + 1)
            for ty in range(ty0, ty1):
                overlap_y = min(y1, (ty + 1) / dst_h) - max(y0, ty / dst_h)
                if overlap_y <= 0:
                    continue
                for tx in range(tx0, tx1):
                    overlap_x = min(x1, (tx + 1) / dst_w) - max(x0, tx / dst_w)
                    if overlap_x <= 0:
                        continue
                    weights[(tx, ty)] += count * overlap_x * overlap_y * src_w * src_h

    out = [0] * (dst_w * dst_h)
    for (tx, ty), value in weights.items():
        out[ty * dst_w + tx] = int(round(value))
    return out


async def _heatmap_for_hours(
    session: AsyncSession,
    camera_id: int,
    *,
    day: date,
    hour_from: int,
    hour_to: int,
    object_type: str | None,
    grid_w: int,
    grid_h: int,
) -> dict[str, object]:
    """Heatmap for an hour range of one local day.

    Stored cells are per *day*, so an hour range cannot be answered from them.
    Tracks carry ``first_seen`` and a normalised centre, which is exactly the
    data needed to bin the objects of a few hours.
    """
    day_start, _ = day_bounds_utc(day.year, day.month, day.day, settings.timezone)
    start = day_start + timedelta(hours=hour_from)
    end = day_start + timedelta(hours=hour_to + 1)
    if end <= start:  # a range that wraps past midnight, e.g. 22 -> 03
        end += timedelta(days=1)

    stmt = select(TrackRecord.center_x, TrackRecord.center_y).where(
        TrackRecord.camera_id == camera_id,
        TrackRecord.first_seen >= start,
        TrackRecord.first_seen < end,
        TrackRecord.center_x.is_not(None),
        TrackRecord.center_y.is_not(None),
    )
    if object_type and object_type != "*":
        stmt = stmt.where(TrackRecord.object_type == object_type)

    cells = [0] * (grid_w * grid_h)
    max_count = 0
    total = 0
    for center_x, center_y in (await session.execute(stmt)).all():
        column = min(grid_w - 1, max(0, int(float(center_x) * grid_w)))
        row = min(grid_h - 1, max(0, int(float(center_y) * grid_h)))
        index = row * grid_w + column
        cells[index] += 1
        max_count = max(max_count, cells[index])
        total += 1

    return {
        "camera_id": camera_id,
        "grid_w": grid_w,
        "grid_h": grid_h,
        "day": day,
        "max_count": max_count,
        "total": total,
        "cells": cells,
        "object_type": object_type or "*",
        "hour_from": hour_from,
        "hour_to": hour_to,
    }


async def get_heatmap(
    session: AsyncSession,
    camera_id: int,
    *,
    day: date | None = None,
    object_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    grid_w: int | None = None,
    grid_h: int | None = None,
    hour_from: int | None = None,
    hour_to: int | None = None,
) -> dict[str, object]:
    """Return a full grid of counts for a camera/day (optionally an hour range)."""
    target_w, target_h = clamp_grid(grid_w, grid_h)

    if hour_from is not None or hour_to is not None:
        first = 0 if hour_from is None else max(0, min(23, int(hour_from)))
        last = 23 if hour_to is None else max(0, min(23, int(hour_to)))
        target_day = day or (to_local(utcnow(), settings.timezone) or utcnow()).date()
        return await _heatmap_for_hours(
            session,
            camera_id,
            day=target_day,
            hour_from=first,
            hour_to=last,
            object_type=object_type,
            grid_w=target_w,
            grid_h=target_h,
        )

    grid_w = settings.heatmap_grid_w
    grid_h = settings.heatmap_grid_h

    stmt = select(HeatmapCell).where(
        HeatmapCell.camera_id == camera_id,
        HeatmapCell.grid_w == grid_w,
        HeatmapCell.grid_h == grid_h,
    )
    if day is not None:
        stmt = stmt.where(HeatmapCell.day == day)
    if object_type and object_type != "*":
        stmt = stmt.where(HeatmapCell.object_type.in_([object_type, "*"]))
    if start is not None:
        local_start = to_local(start, settings.timezone)
        if local_start is not None:
            stmt = stmt.where(HeatmapCell.day >= local_start.date())
    if end is not None:
        local_end = to_local(end, settings.timezone)
        if local_end is not None:
            stmt = stmt.where(HeatmapCell.day <= local_end.date())

    # Aggregate in SQL: without a day filter this used to load every stored
    # cell (up to grid_w x grid_h x types x days), which grew without bound.
    rows = (
        await session.execute(
            stmt.with_only_columns(
                HeatmapCell.cell_x,
                HeatmapCell.cell_y,
                func.sum(HeatmapCell.count).label("count"),
            ).group_by(HeatmapCell.cell_x, HeatmapCell.cell_y)
        )
    ).all()

    cells = [0] * (grid_w * grid_h)
    for cell_x, cell_y, count in rows:
        index = int(cell_y) * grid_w + int(cell_x)
        if 0 <= index < len(cells):
            cells[index] += int(count or 0)

    if (target_w, target_h) != (grid_w, grid_h):
        cells = rebin(cells, grid_w, grid_h, target_w, target_h)
        grid_w, grid_h = target_w, target_h

    return {
        "camera_id": camera_id,
        "grid_w": grid_w,
        "grid_h": grid_h,
        "day": day,
        "max_count": max(cells) if cells else 0,
        "total": sum(cells),
        "cells": cells,
        "object_type": object_type or "*",
    }


async def heatmap_total(session: AsyncSession, camera_id: int | None = None) -> int:
    stmt = select(func.coalesce(func.sum(HeatmapCell.count), 0))
    if camera_id is not None:
        stmt = stmt.where(HeatmapCell.camera_id == camera_id)
    return int((await session.execute(stmt)).scalar() or 0)

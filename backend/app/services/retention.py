"""Retention: drop old records and their image files."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.timeutil import to_local, utcnow
from app.models.detection import Event, ObjectImage, TrackRecord
from app.models.system import Alert, HealthMetric, HeatmapCell
from app.services.storage import delete_relative

logger = logging.getLogger(__name__)


@dataclass
class RetentionReport:
    days: int
    cutoff: str
    tracks_deleted: int = 0
    events_deleted: int = 0
    alerts_deleted: int = 0
    images_deleted: int = 0
    health_deleted: int = 0
    heatmap_deleted: int = 0
    files_deleted: int = 0
    dry_run: bool = False

    def as_dict(self) -> dict:
        return {
            "days": self.days,
            "cutoff": self.cutoff,
            "tracks_deleted": self.tracks_deleted,
            "events_deleted": self.events_deleted,
            "alerts_deleted": self.alerts_deleted,
            "images_deleted": self.images_deleted,
            "health_deleted": self.health_deleted,
            "heatmap_deleted": self.heatmap_deleted,
            "files_deleted": self.files_deleted,
            "dry_run": self.dry_run,
        }


async def apply_retention(
    session: AsyncSession, days: int | None = None, *, dry_run: bool = False
) -> RetentionReport:
    days = int(days if days is not None else settings.retention_days)
    cutoff = utcnow() - timedelta(days=max(1, days))
    report = RetentionReport(days=days, cutoff=cutoff.isoformat(), dry_run=dry_run)

    image_stmt = select(ObjectImage).where(ObjectImage.captured_at < cutoff)
    images = (await session.execute(image_stmt)).scalars().all()
    report.images_deleted = len(images)

    if not dry_run:
        for image in images:
            for path in (image.full_path, image.crop_path, image.thumb_path):
                if delete_relative(path):
                    report.files_deleted += 1
            await session.delete(image)
        await session.flush()

    events_stmt = select(Event).where(Event.occurred_at < cutoff)
    events = (await session.execute(events_stmt)).scalars().all()
    report.events_deleted = len(events)
    if not dry_run and events:
        for event in events:
            for path in (event.snapshot_full_path, event.snapshot_crop_path):
                if delete_relative(path):
                    report.files_deleted += 1
        await session.execute(
            delete(Event).where(Event.occurred_at < cutoff)
        )

    tracks_stmt = select(TrackRecord).where(TrackRecord.last_seen < cutoff)
    tracks = (await session.execute(tracks_stmt)).scalars().all()
    report.tracks_deleted = len(tracks)
    if not dry_run and tracks:
        await session.execute(delete(TrackRecord).where(TrackRecord.last_seen < cutoff))

    alerts_stmt = select(Alert).where(Alert.triggered_at < cutoff)
    report.alerts_deleted = len((await session.execute(alerts_stmt)).scalars().all())
    if not dry_run:
        await session.execute(delete(Alert).where(Alert.triggered_at < cutoff))

    health_stmt = select(HealthMetric).where(HealthMetric.recorded_at < cutoff)
    report.health_deleted = len((await session.execute(health_stmt)).scalars().all())
    if not dry_run:
        await session.execute(delete(HealthMetric).where(HealthMetric.recorded_at < cutoff))

    # Heatmap cells are bucketed by *local* day, so the cutoff is local too.
    # They used to be kept forever, which grew the table without bound.
    heatmap_cutoff = (to_local(cutoff, settings.timezone) or cutoff).date()
    report.heatmap_deleted = int(
        (
            await session.execute(
                select(func.count())
                .select_from(HeatmapCell)
                .where(HeatmapCell.day < heatmap_cutoff)
            )
        ).scalar()
        or 0
    )
    if not dry_run:
        await session.execute(delete(HeatmapCell).where(HeatmapCell.day < heatmap_cutoff))

    if not dry_run:
        await session.commit()
    logger.info("retention finished: %s", report.as_dict())
    return report

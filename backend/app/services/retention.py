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

#: Deleting in bounded batches keeps a first run on an old database from loading
#: every image/event row into memory before writing anything.
RETENTION_CHUNK = 500


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


async def _count(session: AsyncSession, model, condition) -> int:
    total = await session.execute(select(func.count()).select_from(model).where(condition))
    return int(total.scalar() or 0)


async def apply_retention(
    session: AsyncSession, days: int | None = None, *, dry_run: bool = False
) -> RetentionReport:
    days = int(days if days is not None else settings.retention_days)
    cutoff = utcnow() - timedelta(days=max(1, days))
    report = RetentionReport(days=days, cutoff=cutoff.isoformat(), dry_run=dry_run)

    image_filter = ObjectImage.captured_at < cutoff
    if dry_run:
        report.images_deleted = await _count(session, ObjectImage, image_filter)
    else:
        while True:
            images = (
                await session.execute(
                    select(ObjectImage).where(image_filter).limit(RETENTION_CHUNK)
                )
            ).scalars().all()
            if not images:
                break
            for image in images:
                for path in (image.full_path, image.crop_path, image.thumb_path):
                    if delete_relative(path):
                        report.files_deleted += 1
                await session.delete(image)
            await session.flush()
            report.images_deleted += len(images)

    event_filter = Event.occurred_at < cutoff
    if dry_run:
        report.events_deleted = await _count(session, Event, event_filter)
    else:
        while True:
            events = (
                await session.execute(select(Event).where(event_filter).limit(RETENTION_CHUNK))
            ).scalars().all()
            if not events:
                break
            for event in events:
                for path in (event.snapshot_full_path, event.snapshot_crop_path):
                    if delete_relative(path):
                        report.files_deleted += 1
                await session.delete(event)
            await session.flush()
            report.events_deleted += len(events)

    track_filter = TrackRecord.last_seen < cutoff
    report.tracks_deleted = await _count(session, TrackRecord, track_filter)
    if not dry_run:
        await session.execute(delete(TrackRecord).where(track_filter))

    alert_filter = Alert.triggered_at < cutoff
    report.alerts_deleted = await _count(session, Alert, alert_filter)
    if not dry_run:
        await session.execute(delete(Alert).where(alert_filter))

    health_filter = HealthMetric.recorded_at < cutoff
    report.health_deleted = await _count(session, HealthMetric, health_filter)
    if not dry_run:
        await session.execute(delete(HealthMetric).where(health_filter))

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

"""Detection domain: tracked objects, saved images and logged events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime

# --- enums kept as plain strings so migrations stay cheap -------------------
DIRECTIONS = (
    "stationary",
    "left",
    "right",
    "up",
    "down",
    "up-left",
    "up-right",
    "down-left",
    "down-right",
)

MOTION_STATES = ("moving", "stationary")

EVENT_TYPES = (
    "enter",  # object entered the frame / region
    "exit",  # object left the frame / region
    "stationary",  # object stopped moving
    "moving",  # stationary object started moving again
    "line_cross",  # tripwire crossed
    "motion",  # raw motion with no classified object
    "alert",  # alert rule fired
    "detection",  # generic detection log
)

IMAGE_KINDS = ("enter", "exit", "best", "stationary", "periodic", "alert", "motion")


class TrackRecord(TimestampMixin, Base):
    """One tracked object instance (a "track" in tracking terms)."""

    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), index=True, nullable=False
    )
    track_id: Mapped[int] = mapped_column(Integer, nullable=False)  # per-camera tracker id
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, index=True, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, index=True, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    motion_state: Mapped[str] = mapped_column(String(16), default="moving", nullable=False)
    direction: Mapped[str] = mapped_column(String(16), default="stationary", index=True, nullable=False)
    velocity_x: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # normalised /s
    velocity_y: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    distance_travelled: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # normalised (0..1) boxes at birth / death of the track
    bbox_first: Mapped[list[float]] = mapped_column(JSON, default=list)
    bbox_last: Mapped[list[float]] = mapped_column(JSON, default=list)

    # denormalised centre of ``bbox_last`` so spatial search can pre-filter in
    # SQL (a full polygon test is then applied in Python on a small candidate set)
    center_x: Mapped[float | None] = mapped_column(Float, index=True)
    center_y: Mapped[float | None] = mapped_column(Float, index=True)

    status: Mapped[str] = mapped_column(String(16), default="active", index=True, nullable=False)
    entered_frame: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    exited_frame: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zones.id", ondelete="SET NULL"), index=True
    )
    crossed_line_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"))
    line_direction: Mapped[str | None] = mapped_column(String(16))

    best_image_id: Mapped[int | None] = mapped_column(Integer)
    frame_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    images: Mapped[list["ObjectImage"]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        lazy="selectin",
        foreign_keys="ObjectImage.track_id",
    )

    __table_args__ = (
        Index("ix_tracks_camera_type_time", "camera_id", "object_type", "first_seen"),
        Index("ix_tracks_camera_direction_time", "camera_id", "direction", "first_seen"),
        Index("ix_tracks_camera_center", "camera_id", "center_x", "center_y"),
    )


class ObjectImage(TimestampMixin, Base):
    """A saved frame for a track: the full scene and the object crop."""

    __tablename__ = "object_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), default="best", nullable=False)

    full_path: Mapped[str] = mapped_column(Text, nullable=False)
    crop_path: Mapped[str] = mapped_column(Text, nullable=False)
    thumb_path: Mapped[str | None] = mapped_column(Text)

    bbox: Mapped[list[float]] = mapped_column(JSON, default=list)  # normalised
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, index=True, nullable=False)
    sharpness: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    brightness: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    frame_width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    frame_height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True, nullable=False)
    is_best: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)

    track: Mapped[TrackRecord] = relationship(back_populates="images", foreign_keys=[track_id])


class Event(TimestampMixin, Base):
    """A logged event (roیداد) shown in the events section and the timeline."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), index=True, nullable=False
    )
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"))
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"))

    event_type: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(12), default="info", nullable=False)
    object_type: Mapped[str | None] = mapped_column(String(64), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    direction: Mapped[str | None] = mapped_column(String(16))

    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    snapshot_full_path: Mapped[str | None] = mapped_column(Text)
    snapshot_crop_path: Mapped[str | None] = mapped_column(Text)
    bbox: Mapped[list[float]] = mapped_column(JSON, default=list)

    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_events_camera_type_time", "camera_id", "event_type", "occurred_at"),
    )

"""System-level models: alerts, health metrics, heatmap cells, settings."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UTCDateTime

CAMERA_STATES = ("online", "offline", "reconnecting", "error", "disabled", "unknown")


class Alert(TimestampMixin, Base):
    """A fired alert, surfaced in the UI with sound + browser notification."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), index=True, nullable=False
    )
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id", ondelete="SET NULL"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))

    rule_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    severity: Mapped[str] = mapped_column(String(12), default="warning", nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    object_type: Mapped[str | None] = mapped_column(String(64))

    triggered_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True, nullable=False)
    sound: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class HealthMetric(TimestampMixin, Base):
    """A health sample for a camera (FPS, bitrate, resolution, latency ...)."""

    __tablename__ = "health_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), index=True, nullable=False
    )

    state: Mapped[str] = mapped_column(String(16), default="unknown", index=True, nullable=False)
    fps: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    detection_fps: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    bitrate_kbps: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    jitter_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    dropped_frames: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reconnect_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True, nullable=False)
    last_frame_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (
        Index("ix_health_camera_time", "camera_id", "recorded_at"),
    )


class HeatmapCell(Base):
    """Aggregated detection-centre counts on a fixed grid, bucketed per local day."""

    __tablename__ = "heatmap_cells"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), index=True, nullable=False
    )
    day: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    grid_w: Mapped[int] = mapped_column(Integer, default=64, nullable=False)
    grid_h: Mapped[int] = mapped_column(Integer, default=36, nullable=False)
    cell_x: Mapped[int] = mapped_column(Integer, nullable=False)
    cell_y: Mapped[int] = mapped_column(Integer, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), default="*", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "camera_id", "day", "grid_w", "grid_h", "cell_x", "cell_y", "object_type",
            name="uq_heatmap_cell",
        ),
    )


class AdminCredential(TimestampMixin, Base):
    """The single administrator password (there is no user management).

    ``session_secret`` signs the session cookie; changing the password rotates
    it, which signs out every existing browser session.
    """

    __tablename__ = "admin_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    salt: Mapped[str] = mapped_column(String(128), nullable=False)
    #: ``scrypt:<n>:<r>:<p>`` so the cost can be raised without a migration.
    params: Mapped[str] = mapped_column(String(64), default="scrypt:16384:8:1", nullable=False)
    session_secret: Mapped[str] = mapped_column(String(128), nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Setting(TimestampMixin, Base):
    """Simple key/value application settings (JSON values)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(Text)

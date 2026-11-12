"""Camera and zone (region-of-interest / tripwire) models."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

# Default per-class detection configuration. The UI can override any entry.
DEFAULT_CLASS_CONFIG: dict[str, dict[str, Any]] = {
    "person": {"enabled": True, "threshold": 0.35, "min_width": 0.02, "min_height": 0.04},
    "car": {"enabled": True, "threshold": 0.35, "min_width": 0.04, "min_height": 0.04},
    "truck": {"enabled": True, "threshold": 0.35, "min_width": 0.05, "min_height": 0.05},
    "bus": {"enabled": True, "threshold": 0.35, "min_width": 0.05, "min_height": 0.05},
    "motorcycle": {"enabled": True, "threshold": 0.35, "min_width": 0.02, "min_height": 0.03},
    "bicycle": {"enabled": True, "threshold": 0.35, "min_width": 0.02, "min_height": 0.03},
}

DEFAULT_RECONNECT_POLICY: dict[str, Any] = {
    "max_retries": 0,  # 0 = retry forever
    "backoff_initial": 1.0,
    "backoff_max": 30.0,
    "backoff_factor": 2.0,
}


class Camera(TimestampMixin, Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200))
    rtsp_url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # stream
    transport: Mapped[str] = mapped_column(String(8), default="tcp", nullable=False)
    fps: Mapped[int] = mapped_column(Integer, default=20, nullable=False)  # display fps
    detection_fps: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)

    # detection
    model_name: Mapped[str] = mapped_column(String(64), default="rf-detr-base", nullable=False)
    model_weights: Mapped[str | None] = mapped_column(Text)
    detection_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    motion_sensitivity: Mapped[float] = mapped_column(Float, default=0.004, nullable=False)
    class_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=lambda: DEFAULT_CLASS_CONFIG)
    reconnect_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=lambda: DEFAULT_RECONNECT_POLICY
    )

    # storage
    record_objects: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    record_snapshots: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    notes: Mapped[str | None] = mapped_column(Text)

    zones: Mapped[list["Zone"]] = relationship(
        back_populates="camera", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def has_alert_zone(self) -> bool:
        return any(zone.alert_enabled for zone in self.zones)


class Zone(TimestampMixin, Base):
    """A polygon region or a tripwire line drawn by the user on the video.

    ``points`` are normalised (0..1) so they survive resolution changes.
    A ``kind`` of ``line`` uses ``points[0]`` and ``points[1]`` as the
    two endpoints of the tripwire.
    """

    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="region", nullable=False)
    points: Mapped[list[list[float]]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    color: Mapped[str] = mapped_column(String(16), default="#22d3ee", nullable=False)

    # alerting
    alert_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    alert_sound: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    alert_classes: Mapped[list[str]] = mapped_column(JSON, default=list)
    schedule: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    camera: Mapped[Camera] = relationship(back_populates="zones")

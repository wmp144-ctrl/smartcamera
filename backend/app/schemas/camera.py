"""Camera and zone schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.camera import DEFAULT_CLASS_CONFIG, DEFAULT_RECONNECT_POLICY
from app.schemas.common import ORMModel

ZoneKind = Literal["region", "ignore", "line", "alert"]


class ClassRule(BaseModel):
    enabled: bool = True
    threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    min_width: float = Field(default=0.0, ge=0.0, le=1.0)
    min_height: float = Field(default=0.0, ge=0.0, le=1.0)
    min_motion: float = Field(default=0.0, ge=0.0, le=1.0)


class ReconnectPolicy(BaseModel):
    max_retries: int = Field(default=0, ge=0)
    backoff_initial: float = Field(default=1.0, ge=0.1, le=60)
    backoff_max: float = Field(default=30.0, ge=1.0, le=600)
    backoff_factor: float = Field(default=2.0, ge=1.0, le=10)


class CameraBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    location: str | None = Field(default=None, max_length=200)
    rtsp_url: str = Field(min_length=1)
    enabled: bool = True
    transport: Literal["tcp", "udp"] = "tcp"
    fps: int = Field(default=20, ge=1, le=60)
    detection_fps: float = Field(default=5.0, gt=0, le=60)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    model_name: str = "rf-detr-base"
    model_weights: str | None = None
    detection_enabled: bool = False
    motion_sensitivity: float = Field(default=0.004, ge=0.0, le=1.0)
    record_objects: bool = True
    record_snapshots: bool = True
    notes: str | None = None

    @field_validator("rtsp_url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        value = value.strip()
        scheme = value.split("://", 1)[0].lower() if "://" in value else ""
        if scheme not in {"rtsp", "rtsps", "http", "https", "file", "rtmp"}:
            raise ValueError(
                "rtsp_url must start with rtsp://, rtsps://, http(s)://, rtmp:// or file://"
            )
        return value


def _default_class_rules() -> dict[str, ClassRule]:
    return {key: ClassRule(**value) for key, value in DEFAULT_CLASS_CONFIG.items()}


class CameraCreate(CameraBase):
    class_config: dict[str, ClassRule] = Field(
        default_factory=_default_class_rules, validate_default=True
    )
    reconnect_policy: ReconnectPolicy = Field(
        default_factory=ReconnectPolicy, validate_default=True
    )


def rule_as_dict(rule: object) -> dict:
    """Accept either a ``ClassRule`` model or a plain mapping."""
    if hasattr(rule, "model_dump"):
        return rule.model_dump()  # type: ignore[union-attr]
    if isinstance(rule, dict):
        return dict(rule)
    return {}


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    location: str | None = Field(default=None, max_length=200)
    rtsp_url: str | None = None
    enabled: bool | None = None
    transport: Literal["tcp", "udp"] | None = None
    fps: int | None = Field(default=None, ge=1, le=60)
    detection_fps: float | None = Field(default=None, gt=0, le=60)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    model_name: str | None = None
    model_weights: str | None = None
    detection_enabled: bool | None = None
    motion_sensitivity: float | None = Field(default=None, ge=0.0, le=1.0)
    record_objects: bool | None = None
    record_snapshots: bool | None = None
    notes: str | None = None
    class_config: dict[str, ClassRule] | None = None
    reconnect_policy: ReconnectPolicy | None = None


class CameraRead(ORMModel):
    id: int
    name: str
    location: str | None
    rtsp_url: str
    enabled: bool
    transport: str
    fps: int
    detection_fps: float
    width: int | None
    height: int | None
    model_name: str
    model_weights: str | None
    detection_enabled: bool
    motion_sensitivity: float
    record_objects: bool
    record_snapshots: bool
    notes: str | None
    class_config: dict[str, Any] = Field(default_factory=dict)
    reconnect_policy: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class CameraStatus(BaseModel):
    camera_id: int
    state: str = "unknown"
    fps: float = 0.0
    detection_fps: float = 0.0
    bitrate_kbps: float = 0.0
    width: int = 0
    height: int = 0
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    dropped_frames: int = 0
    reconnect_count: int = 0
    last_frame_at: datetime | None = None
    detection_enabled: bool = False
    message: str | None = None
    # Set when detection is enabled but the detector could not be loaded (for
    # example the optional RF-DETR / PyTorch stack is not installed) so the UI
    # can explain why nothing is being detected instead of silently showing 0.
    detector_error: str | None = None


class ZoneBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: ZoneKind = "region"
    points: list[list[float]] = Field(min_length=2)
    enabled: bool = True
    color: str = "#22d3ee"
    alert_enabled: bool = False
    alert_sound: bool = True
    alert_classes: list[str] = Field(default_factory=list)
    schedule: dict[str, Any] | None = None

    @field_validator("points")
    @classmethod
    def _normalise(cls, points: list[list[float]]) -> list[list[float]]:
        out: list[list[float]] = []
        for point in points:
            if len(point) != 2:
                raise ValueError("each point must be [x, y]")
            x, y = float(point[0]), float(point[1])
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError("points must be normalised between 0 and 1")
            out.append([x, y])
        return out

    @model_validator(mode="after")
    def _check_shape(self):
        if self.kind == "line" and len(self.points) != 2:
            raise ValueError("a line zone needs exactly 2 points")
        if self.kind in {"region", "ignore", "alert"} and len(self.points) < 3:
            raise ValueError("a polygon zone needs at least 3 points")
        return self


class ZoneCreate(ZoneBase):
    camera_id: int | None = None


class ZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: ZoneKind | None = None
    points: list[list[float]] | None = None
    enabled: bool | None = None
    color: str | None = None
    alert_enabled: bool | None = None
    alert_sound: bool | None = None
    alert_classes: list[str] | None = None
    schedule: dict[str, Any] | None = None


class ZoneRead(ORMModel):
    id: int
    camera_id: int
    name: str
    kind: str
    points: list[list[float]]
    enabled: bool
    color: str
    alert_enabled: bool
    alert_sound: bool
    alert_classes: list[str]
    schedule: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class CameraWithZones(CameraRead):
    zones: list[ZoneRead] = Field(default_factory=list)

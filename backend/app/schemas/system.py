"""System schemas: alerts, health, statistics, heatmap, GPU and system info."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, computed_field

from app.core.config import settings
from app.core.timeutil import jalali_datetime_label, jalali_label
from app.schemas.common import ORMModel
from app.schemas.detection import EventRead


class AlertRead(ORMModel):
    id: int
    camera_id: int
    zone_id: int | None
    event_id: int | None
    rule_name: str
    severity: str
    title: str
    message: str
    object_type: str | None
    triggered_at: datetime
    sound: bool
    delivered: bool
    acknowledged: bool
    acknowledged_at: datetime | None
    meta: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def triggered_at_jalali(self) -> str:
        return jalali_datetime_label(self.triggered_at, settings.timezone)


class HealthRead(ORMModel):
    id: int
    camera_id: int
    state: str
    fps: float
    detection_fps: float
    bitrate_kbps: float
    width: int
    height: int
    latency_ms: float
    jitter_ms: float
    dropped_frames: int
    reconnect_count: int
    recorded_at: datetime
    last_frame_at: datetime | None


class CountByKey(BaseModel):
    key: str
    count: int
    extra: dict[str, Any] = Field(default_factory=dict)


class TimeBucket(BaseModel):
    bucket: str  # ISO datetime (UTC) at the start of the bucket
    jalali: str
    count: int


class CameraStats(BaseModel):
    camera_id: int
    camera_name: str
    total_tracks: int
    total_events: int
    total_alerts: int
    by_type: list[CountByKey] = Field(default_factory=list)
    state: str = "unknown"
    fps: float = 0.0


class OverviewStats(BaseModel):
    generated_at: datetime
    timezone: str = settings.timezone
    total_cameras: int
    online_cameras: int
    offline_cameras: int
    total_tracks: int
    total_events: int
    total_alerts: int
    unacknowledged_alerts: int
    tracks_today: int
    events_today: int
    objects_today: int
    storage_bytes: int
    by_type: list[CountByKey] = Field(default_factory=list)
    by_direction: list[CountByKey] = Field(default_factory=list)
    by_event_type: list[CountByKey] = Field(default_factory=list)
    hourly: list[TimeBucket] = Field(default_factory=list)
    daily: list[TimeBucket] = Field(default_factory=list)
    cameras: list[CameraStats] = Field(default_factory=list)
    recent_events: list[EventRead] = Field(default_factory=list)


class HeatmapResponse(BaseModel):
    camera_id: int
    grid_w: int
    grid_h: int
    day: date | None = None
    jalali: str | None = None
    max_count: int
    total: int
    # row-major grid of counts, length grid_w * grid_h
    cells: list[int]
    object_type: str = "*"
    #: Set when the grid was built for an hour range instead of a whole day.
    hour_from: int | None = None
    hour_to: int | None = None


class GPUMetric(BaseModel):
    index: int = 0
    name: str = "unknown"
    utilization: float = 0.0
    memory_used_mb: float = 0.0
    memory_total_mb: float = 0.0
    memory_percent: float = 0.0
    temperature_c: float | None = None
    power_w: float | None = None
    active_cameras: int = 0
    detection_fps: float = 0.0


class GPUInfo(BaseModel):
    available: bool
    source: str = "none"
    driver_version: str | None = None
    cuda_version: str | None = None
    devices: list[GPUMetric] = Field(default_factory=list)


class ModelInfo(BaseModel):
    key: str
    name: str
    resolution: int
    weights_filename: str
    available: bool
    path: str | None = None
    description: str = ""


class SystemComponent(BaseModel):
    name: str
    version: str
    available: bool = True


class SystemInfo(BaseModel):
    app_name: str
    version: str
    python_version: str
    platform: str
    timezone: str
    utc_now: datetime
    local_now: datetime
    jalali_now: str
    database_url: str
    storage_root: str
    storage_bytes: int
    components: list[SystemComponent] = Field(default_factory=list)
    gpu: GPUInfo


class SettingRead(ORMModel):
    key: str
    value: Any
    description: str | None = None
    updated_at: datetime


class SettingUpdate(BaseModel):
    value: Any


class StorageUsage(BaseModel):
    root: str
    total_bytes: int
    by_camera: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)
    file_count: int


class DayBoundary(BaseModel):
    """Answers "which UTC window is this Jalali day?" for the UI."""

    jalali: str
    jalali_year: int
    jalali_month: int
    jalali_day: int
    start_utc: datetime
    end_utc: datetime
    start_local: datetime
    end_local: datetime
    timezone: str = settings.timezone

    @classmethod
    def build(cls, start_utc: datetime, end_utc: datetime) -> "DayBoundary":
        from app.core.timeutil import to_local  # noqa: PLC0415

        # The calendar label must be derived from the *local* date, because the
        # UTC instant at local midnight still belongs to the previous UTC day.
        start_local = to_local(start_utc, settings.timezone) or start_utc
        label = jalali_label(start_local)
        jy, jm, jd = (int(part) for part in label.split("/"))
        return cls(
            jalali=label,
            jalali_year=jy,
            jalali_month=jm,
            jalali_day=jd,
            start_utc=start_utc,
            end_utc=end_utc,
            start_local=start_local,
            end_local=to_local(end_utc, settings.timezone) or end_utc,
        )

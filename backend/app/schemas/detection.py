"""Detection / event / search schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator

from app.core.config import settings
from app.core.media import media_url
from app.core.timeutil import jalali_datetime_label
from app.schemas.common import ORMModel

Direction = Literal[
    "stationary",
    "left",
    "right",
    "up",
    "down",
    "up-left",
    "up-right",
    "down-left",
    "down-right",
]


class ObjectImageRead(ORMModel):
    id: int
    track_id: int
    camera_id: int
    kind: str
    full_path: str
    crop_path: str
    thumb_path: str | None = None
    bbox: list[float] = Field(default_factory=list)
    confidence: float
    quality_score: float
    sharpness: float
    brightness: float
    frame_width: int
    frame_height: int
    captured_at: datetime
    is_best: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def full_url(self) -> str | None:
        return media_url(self.full_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def crop_url(self) -> str | None:
        return media_url(self.crop_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def thumb_url(self) -> str | None:
        return media_url(self.thumb_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def captured_at_jalali(self) -> str:
        return jalali_datetime_label(self.captured_at, settings.timezone)


class TrackRead(ORMModel):
    id: int
    camera_id: int
    track_id: int
    object_type: str
    confidence: float
    first_seen: datetime
    last_seen: datetime
    duration_seconds: float
    motion_state: str
    direction: str
    velocity_x: float
    velocity_y: float
    distance_travelled: float
    bbox_first: list[float] = Field(default_factory=list)
    bbox_last: list[float] = Field(default_factory=list)
    status: str
    entered_frame: bool
    exited_frame: bool
    zone_id: int | None = None
    crossed_line_id: int | None = None
    line_direction: str | None = None
    best_image_id: int | None = None
    frame_count: int
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def first_seen_jalali(self) -> str:
        return jalali_datetime_label(self.first_seen, settings.timezone)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def last_seen_jalali(self) -> str:
        return jalali_datetime_label(self.last_seen, settings.timezone)


class TrackDetail(TrackRead):
    images: list[ObjectImageRead] = Field(default_factory=list)
    best_image: ObjectImageRead | None = None


class EventRead(ORMModel):
    id: int
    camera_id: int
    track_id: int | None
    zone_id: int | None
    event_type: str
    severity: str
    object_type: str | None
    confidence: float
    direction: str | None
    message: str
    occurred_at: datetime
    ended_at: datetime | None
    snapshot_full_path: str | None
    snapshot_crop_path: str | None
    bbox: list[float] = Field(default_factory=list)
    acknowledged: bool
    acknowledged_at: datetime | None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def snapshot_url(self) -> str | None:
        return media_url(self.snapshot_full_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def crop_url(self) -> str | None:
        return media_url(self.snapshot_crop_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def occurred_at_jalali(self) -> str:
        return jalali_datetime_label(self.occurred_at, settings.timezone)


class SpatialSearchRequest(BaseModel):
    """Search stored objects by image region + time + type + direction."""

    camera_ids: list[int] | None = None
    # normalised polygon the object centre must fall inside
    polygon: list[list[float]] | None = None
    start: datetime | None = None
    end: datetime | None = None
    object_types: list[str] | None = None
    directions: list[str] | None = None
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    min_quality: float | None = Field(default=None, ge=0, le=1)
    motion_state: Literal["moving", "stationary"] | None = None
    limit: int = Field(default=200, ge=1, le=2000)
    offset: int = Field(default=0, ge=0)
    order: Literal["asc", "desc"] = "desc"

    @field_validator("polygon")
    @classmethod
    def _check_polygon(cls, value: list[list[float]] | None) -> list[list[float]] | None:
        if value is None:
            return None
        if len(value) < 3:
            raise ValueError("polygon needs at least 3 points")
        for point in value:
            if len(point) != 2 or not (0 <= point[0] <= 1 and 0 <= point[1] <= 1):
                raise ValueError("polygon points must be normalised [x, y] pairs")
        return value


class SpatialSearchResult(BaseModel):
    total: int
    items: list[TrackDetail]
    polygon: list[list[float]] | None = None

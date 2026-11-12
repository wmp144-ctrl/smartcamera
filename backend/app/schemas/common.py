"""Shared Pydantic v2 schema helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.timeutil import jalali_datetime_label, to_local

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    page_size: int = 50

    @property
    def pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size if self.page_size else 0


class LocalizedTime(BaseModel):
    """A UTC instant rendered for a Persian UI."""

    utc: datetime
    local: datetime
    jalali: str
    jalali_datetime: str
    timezone: str = settings.timezone

    @classmethod
    def from_utc(cls, value: datetime) -> "LocalizedTime":
        local = to_local(value, settings.timezone)
        assert local is not None
        return cls(
            utc=value,
            local=local,
            jalali=jalali_datetime_label(value, settings.timezone).split(" ")[0],
            jalali_datetime=jalali_datetime_label(value, settings.timezone),
        )


class Message(BaseModel):
    detail: str
    ok: bool = True


class TimeRange(BaseModel):
    start: datetime | None = Field(default=None, description="ISO datetime (inclusive, UTC)")
    end: datetime | None = Field(default=None, description="ISO datetime (exclusive, UTC)")

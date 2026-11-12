"""Timezone helpers.

Design rule (enforced across the codebase):

    UTC in the database  ->  local time in the UI

Every timestamp written to the database is timezone-aware UTC. The UI layer
converts to the configured display timezone (Asia/Tehran by default) and the
Jalali calendar is derived from that local time.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo

try:  # pragma: no cover - optional dependency
    from zoneinfo import ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfoNotFoundError = KeyError  # type: ignore[assignment,misc]

UTC = timezone.utc


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    """Coerce a naive or aware datetime into timezone-aware UTC.

    Naive datetimes are assumed to already be UTC (this is how SQLite hands
    them back), aware datetimes are converted.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@lru_cache(maxsize=32)
def get_timezone(name: str) -> tzinfo:
    """Resolve an IANA timezone name, falling back to UTC."""
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - any tz failure should not crash the app
        return UTC


def to_local(value: datetime | None, tz_name: str = "Asia/Tehran") -> datetime | None:
    """Convert a stored (UTC) datetime to the display timezone."""
    value = ensure_utc(value)
    if value is None:
        return None
    return value.astimezone(get_timezone(tz_name))


def to_utc(value: datetime | None, tz_name: str = "Asia/Tehran") -> datetime | None:
    """Convert a local (naive or aware) datetime to UTC.

    Naive datetimes coming from the UI are interpreted in the display timezone.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=get_timezone(tz_name)).astimezone(UTC)
    return value.astimezone(UTC)


def parse_dt(value: str | datetime | None, tz_name: str = "Asia/Tehran") -> datetime | None:
    """Parse an ISO string (or pass through a datetime) into UTC-aware form."""
    if value is None or isinstance(value, datetime):
        return to_utc(value, tz_name) if isinstance(value, datetime) else None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return to_utc(parsed, tz_name)


# --------------------------------------------------------------------------
# Jalali (Solar Hijri) calendar
# --------------------------------------------------------------------------
# Inverse of the well known jalali algorithm (Borkowski / jalaali-js).
# Persian year starts at the March equinox; we operate on the local calendar
# date so "1403-01-01" means the same day a Persian user sees.


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """Convert a Gregorian date to Jalali (port of jalaali-js)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        355666
        + (365 * gy)
        + ((gy2 + 3) // 4)
        - ((gy2 + 99) // 100)
        + ((gy2 + 399) // 400)
        + gd
        + g_d_m[gm - 1]
    )
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    """Convert a Jalali date to Gregorian (port of jalaali-js)."""
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    days += (jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    sal_a = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while gm < 13 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


JALALI_MONTHS = (
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)

JALALI_WEEKDAYS = ("شنبه", "یک‌شنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه")


def to_jalali(value: datetime | date) -> tuple[int, int, int]:
    """Return the (year, month, day) of a gregorian date in the Jalali calendar."""
    if isinstance(value, datetime):
        value = value.date()
    return gregorian_to_jalali(value.year, value.month, value.day)


def jalali_label(value: datetime | date) -> str:
    """Human readable Jalali label, e.g. ``1404/07/13``."""
    jy, jm, jd = to_jalali(value)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def jalali_datetime_label(value: datetime, tz_name: str = "Asia/Tehran") -> str:
    local = to_local(value, tz_name)
    assert local is not None
    return f"{jalali_label(local)} {local.strftime('%H:%M:%S')}"


def day_bounds_utc(
    year: int, month: int, day: int, tz_name: str = "Asia/Tehran"
) -> tuple[datetime, datetime]:
    """UTC [start, end) bounds for a local (Jalali or Gregorian) calendar day.

    ``year`` is interpreted in the Jalali calendar when ``month`` is 1..12 and
    the year is below 1700, otherwise as Gregorian.
    """
    if not (1 <= month <= 12):
        raise ValueError(f"month out of range: {month}")
    if not (1 <= day <= 31):
        raise ValueError(f"day out of range: {day}")
    if year < 1700:
        gy, gm, gd = jalali_to_gregorian(year, month, day)
    else:
        gy, gm, gd = year, month, day
    tz = get_timezone(tz_name)
    try:
        start_local = datetime(gy, gm, gd, tzinfo=tz)
    except ValueError as exc:  # pragma: no cover - guarded by the checks above
        raise ValueError(str(exc)) from exc
    return start_local.astimezone(UTC), (start_local + timedelta(days=1)).astimezone(UTC)

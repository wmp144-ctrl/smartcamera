"""Timezone and Jalali calendar tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.timeutil import (
    day_bounds_utc,
    ensure_utc,
    gregorian_to_jalali,
    jalali_datetime_label,
    jalali_label,
    jalali_to_gregorian,
    parse_dt,
    to_jalali,
    to_local,
    to_utc,
    utcnow,
)


@pytest.mark.parametrize(
    ("gregorian", "jalali"),
    [
        ((2024, 3, 20), (1403, 1, 1)),   # Nowruz 1403
        ((2025, 3, 21), (1404, 1, 1)),   # Nowruz 1404
        ((2026, 3, 21), (1405, 1, 1)),   # Nowruz 1405
        ((2026, 10, 5), (1405, 7, 13)),
        ((2023, 12, 31), (1402, 10, 10)),
        ((2000, 1, 1), (1378, 10, 11)),
    ],
)
def test_gregorian_to_jalali_known_dates(gregorian, jalali) -> None:
    assert gregorian_to_jalali(*gregorian) == jalali


def test_roundtrip_is_stable_over_a_decade() -> None:
    day = date(2016, 1, 1)
    while day < date(2026, 1, 1):
        jy, jm, jd = gregorian_to_jalali(day.year, day.month, day.day)
        assert jalali_to_gregorian(jy, jm, jd) == (day.year, day.month, day.day)
        day += timedelta(days=13)  # prime step -> hits different weekdays/month ends


def test_jalali_label_format() -> None:
    assert jalali_label(date(2026, 10, 5)) == "1405/07/13"


def test_to_jalali_accepts_datetime_and_date() -> None:
    assert to_jalali(datetime(2026, 10, 5, 12, 0)) == (1405, 7, 13)
    assert to_jalali(date(2026, 10, 5)) == (1405, 7, 13)


def test_ensure_utc_treats_naive_as_utc() -> None:
    naive = datetime(2026, 10, 5, 10, 0, 0)
    result = ensure_utc(naive)
    assert result is not None
    assert result.tzinfo == timezone.utc
    assert result.hour == 10


def test_to_local_tehran_offset() -> None:
    # Tehran is UTC+3:30 (Iran abolished DST in 2022)
    utc = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
    local = to_local(utc, "Asia/Tehran")
    assert local is not None
    assert local.hour == 3
    assert local.minute == 30


def test_to_utc_interprets_naive_as_local() -> None:
    local = datetime(2026, 7, 15, 3, 30)
    result = to_utc(local, "Asia/Tehran")
    assert result is not None
    assert result.hour == 0
    assert result.tzinfo == timezone.utc


def test_parse_dt_variants() -> None:
    assert parse_dt(None) is None
    assert parse_dt("") is None
    assert parse_dt("not-a-date") is None
    parsed = parse_dt("2026-10-05T10:00:00Z")
    assert parsed is not None and parsed.hour == 10
    local = parse_dt("2026-10-05T10:00:00", "Asia/Tehran")
    assert local is not None and local.hour == 6 and local.minute == 30


def test_day_bounds_utc_for_jalali_day() -> None:
    start, end = day_bounds_utc(1405, 7, 13, "Asia/Tehran")
    # 1405/07/13 == 2026-10-05 local -> starts 2026-10-04T20:30Z
    assert start.isoformat() == "2026-10-04T20:30:00+00:00"
    assert (end - start) == timedelta(days=1)


def test_day_bounds_utc_for_gregorian_day() -> None:
    start, end = day_bounds_utc(2026, 10, 5, "Asia/Tehran")
    assert start.isoformat() == "2026-10-04T20:30:00+00:00"
    assert (end - start).total_seconds() == 86400


def test_jalali_datetime_label_uses_display_timezone() -> None:
    utc = datetime(2026, 10, 5, 20, 45, tzinfo=timezone.utc)
    # +3:30 -> 2026-10-06 00:15 local, still 1405/07/14
    assert jalali_datetime_label(utc, "Asia/Tehran").startswith("1405/07/14")


def test_utcnow_is_aware() -> None:
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_unknown_timezone_falls_back_to_utc() -> None:
    value = datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc)
    local = to_local(value, "Not/AZone")
    assert local is not None and local.hour == 5

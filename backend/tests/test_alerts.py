"""Alert rule scheduling and cooldown tests."""

from __future__ import annotations

from datetime import datetime, time

import pytest

from app.services.alerts import (
    AlertEngine,
    AlertRule,
    build_rules,
    in_schedule,
    jalali_weekday_index,
    parse_schedule,
)


class _Zone:
    def __init__(self, **kwargs) -> None:
        self.id = kwargs.get("id", 1)
        self.name = kwargs.get("name", "ناحیه")
        self.enabled = kwargs.get("enabled", True)
        self.alert_enabled = kwargs.get("alert_enabled", True)
        self.alert_sound = kwargs.get("alert_sound", True)
        self.alert_classes = kwargs.get("alert_classes", [])
        self.schedule = kwargs.get("schedule", None)


def test_parse_schedule_disabled() -> None:
    assert parse_schedule(None) == (None, [])
    assert parse_schedule({"enabled": False, "start": "08:00", "end": "18:00"}) == (None, [])


def test_parse_schedule_shorthand_and_ranges() -> None:
    days, ranges = parse_schedule(
        {"enabled": True, "weekdays": [0, 1, 2], "start": "08:00", "end": "18:00"}
    )
    assert days == [0, 1, 2]
    assert ranges == [(time(8, 0), time(18, 0))]

    days, ranges = parse_schedule(
        {
            "enabled": True,
            "ranges": [{"start": "08:00", "end": "12:00"}, {"start": "13:00", "end": "17:00"}],
        }
    )
    assert days is None
    assert len(ranges) == 2


def test_parse_schedule_ignores_garbage() -> None:
    days, ranges = parse_schedule({"enabled": True, "start": "oops", "end": "18:00"})
    assert days is None and ranges == []
    days, ranges = parse_schedule({"enabled": True, "start": "25:00", "end": "18:00"})
    assert ranges == []


def test_jalali_weekday_index() -> None:
    # 2026-10-03 is a Saturday -> index 0 in the Persian week
    assert jalali_weekday_index(datetime(2026, 10, 3, 10, 0)) == 0
    assert jalali_weekday_index(datetime(2026, 10, 4, 10, 0)) == 1
    assert jalali_weekday_index(datetime(2026, 10, 9, 10, 0)) == 6  # Friday


def test_in_schedule_weekdays_and_ranges() -> None:
    assert in_schedule(datetime(2026, 10, 3, 10, 0), [0], [(time(8), time(18))])
    assert not in_schedule(datetime(2026, 10, 3, 19, 0), [0], [(time(8), time(18))])
    assert not in_schedule(datetime(2026, 10, 4, 10, 0), [0], [(time(8), time(18))])
    assert in_schedule(datetime(2026, 10, 4, 10, 0), None, [])


def test_in_schedule_overnight_window() -> None:
    window = [(time(22, 0), time(6, 0))]
    assert in_schedule(datetime(2026, 10, 3, 23, 30), None, window)
    assert in_schedule(datetime(2026, 10, 3, 5, 0), None, window)
    assert not in_schedule(datetime(2026, 10, 3, 12, 0), None, window)


def test_build_rules_skips_disabled_zones() -> None:
    rules = build_rules(
        [
            _Zone(id=1, alert_enabled=True, alert_classes=["person"]),
            _Zone(id=2, alert_enabled=False),
            _Zone(id=3, alert_enabled=True, enabled=False),
        ]
    )
    assert [rule.zone_id for rule in rules] == [1]
    assert rules[0].object_types == ["person"]


def _rule(**kwargs) -> AlertRule:
    defaults = {"zone_id": 1, "name": "ناحیه تست", "object_types": ["person"], "sound": True}
    defaults.update(kwargs)
    return AlertRule(**defaults)


def test_engine_fires_for_matching_object_type() -> None:
    engine = AlertEngine(cooldown_seconds=0)
    now = datetime(2026, 10, 3, 12, 0)
    decisions = engine.evaluate(
        [_rule()],
        camera_id=1,
        camera_name="دوربین ۱",
        local_now=now,
        now_ts=100.0,
        zone_id=1,
        object_type="person",
        confidence=0.9,
    )
    assert len(decisions) == 1
    assert decisions[0].severity == "warning"
    assert decisions[0].sound is True
    assert "person" not in decisions[0].title  # title uses the rule name


def test_engine_ignores_other_classes_and_zones() -> None:
    engine = AlertEngine(cooldown_seconds=0)
    now = datetime(2026, 10, 3, 12, 0)
    assert not engine.evaluate(
        [_rule()], camera_id=1, camera_name="c", local_now=now, now_ts=1.0,
        zone_id=1, object_type="car", confidence=0.9,
    )
    assert not engine.evaluate(
        [_rule()], camera_id=1, camera_name="c", local_now=now, now_ts=1.0,
        zone_id=2, object_type="person", confidence=0.9,
    )


def test_engine_respects_cooldown() -> None:
    engine = AlertEngine(cooldown_seconds=30)
    now = datetime(2026, 10, 3, 12, 0)
    first = engine.evaluate(
        [_rule()], camera_id=1, camera_name="c", local_now=now, now_ts=100.0,
        zone_id=1, object_type="person", confidence=0.9,
    )
    second = engine.evaluate(
        [_rule()], camera_id=1, camera_name="c", local_now=now, now_ts=110.0,
        zone_id=1, object_type="person", confidence=0.9,
    )
    third = engine.evaluate(
        [_rule()], camera_id=1, camera_name="c", local_now=now, now_ts=140.0,
        zone_id=1, object_type="person", confidence=0.9,
    )
    assert len(first) == 1
    assert second == []
    assert len(third) == 1


def test_engine_respects_schedule() -> None:
    engine = AlertEngine(cooldown_seconds=0)
    rule = _rule(weekdays=[0], time_ranges=[(time(8), time(12))])
    # Saturday 13:00 -> outside the window
    outside = datetime(2026, 10, 3, 13, 0)
    assert not engine.evaluate(
        [rule], camera_id=1, camera_name="c", local_now=outside, now_ts=1.0,
        zone_id=1, object_type="person", confidence=0.9,
    )
    inside = datetime(2026, 10, 3, 10, 0)
    assert engine.evaluate(
        [rule], camera_id=1, camera_name="c", local_now=inside, now_ts=2.0,
        zone_id=1, object_type="person", confidence=0.9,
    )


def test_engine_requires_min_confidence() -> None:
    engine = AlertEngine(cooldown_seconds=0)
    rule = _rule(min_confidence=0.8)
    now = datetime(2026, 10, 3, 12, 0)
    assert not engine.evaluate(
        [rule], camera_id=1, camera_name="c", local_now=now, now_ts=1.0,
        zone_id=1, object_type="person", confidence=0.5,
    )


def test_engine_reset_clears_cooldown() -> None:
    engine = AlertEngine(cooldown_seconds=100)
    now = datetime(2026, 10, 3, 12, 0)
    engine.evaluate(
        [_rule()], camera_id=1, camera_name="c", local_now=now, now_ts=1.0,
        zone_id=1, object_type="person", confidence=0.9,
    )
    engine.reset()
    assert engine.evaluate(
        [_rule()], camera_id=1, camera_name="c", local_now=now, now_ts=2.0,
        zone_id=1, object_type="person", confidence=0.9,
    )


def test_wildcard_rule_matches_any_class() -> None:
    engine = AlertEngine(cooldown_seconds=0)
    now = datetime(2026, 10, 3, 12, 0)
    decisions = engine.evaluate(
        [_rule(object_types=[])],
        camera_id=1,
        camera_name="c",
        local_now=now,
        now_ts=1.0,
        zone_id=1,
        object_type="truck",
        confidence=0.9,
    )
    assert len(decisions) == 1


@pytest.mark.parametrize("bad", ["", "abc", "12", "99:99"])
def test_parse_time_rejects_bad_values(bad) -> None:
    days, ranges = parse_schedule({"enabled": True, "start": bad, "end": "18:00"})
    assert ranges == []

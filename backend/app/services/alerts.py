"""Alert rule evaluation.

A rule lives on a zone: "alert me for these classes, inside this region, during
this schedule, with sound". This module decides *whether* an alert fires; the
persistence and the WebSocket delivery happen in the pipeline/bus.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, time


logger = logging.getLogger(__name__)

# Persian week starts on Saturday. 0 = شنبه ... 6 = جمعه
EVENT_SEVERITY = {
    "person": "warning",
    "car": "info",
    "truck": "info",
    "bus": "info",
    "motorcycle": "info",
    "bicycle": "info",
}


@dataclass(slots=True)
class AlertRule:
    """A compiled alert rule (from a zone)."""

    zone_id: int
    name: str
    object_types: list[str] = field(default_factory=list)  # empty = all
    min_confidence: float = 0.0
    sound: bool = True
    weekdays: list[int] | None = None  # None = every day
    time_ranges: list[tuple[time, time]] = field(default_factory=list)  # empty = all day
    severity: str = "warning"


@dataclass(slots=True)
class AlertDecision:
    rule: AlertRule
    object_type: str | None
    title: str
    message: str
    severity: str
    sound: bool


def parse_schedule(schedule: dict | None) -> tuple[list[int] | None, list[tuple[time, time]]]:
    """Parse a schedule dict into ``(weekdays, time_ranges)``.

    Accepted shape::

        {"enabled": true,
         "weekdays": [0, 1, 2],
         "ranges": [{"start": "08:00", "end": "18:00"}],
         "start": "08:00", "end": "18:00"}   # single-range shorthand
    """
    if not schedule or not schedule.get("enabled", True):
        return None, []

    weekdays = schedule.get("weekdays")
    days: list[int] | None = None
    if isinstance(weekdays, list) and weekdays:
        days = sorted({int(d) % 7 for d in weekdays})

    ranges: list[tuple[time, time]] = []
    raw_ranges = schedule.get("ranges")
    if isinstance(raw_ranges, list):
        candidates = raw_ranges
    elif schedule.get("start") and schedule.get("end"):
        candidates = [{"start": schedule["start"], "end": schedule["end"]}]
    else:
        candidates = []

    for item in candidates:
        start = _parse_time(item.get("start"))
        end = _parse_time(item.get("end"))
        if start is not None and end is not None:
            ranges.append((start, end))
    return days, ranges


def _parse_time(value: object) -> time | None:
    if isinstance(value, time):
        return value
    if not isinstance(value, str) or ":" not in value:
        return None
    parts = value.strip().split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def jalali_weekday_index(local_dt: datetime) -> int:
    """Map a local datetime to the Persian weekday index (0 = شنبه/Saturday)."""
    # Python: Monday=0 ... Sunday=6. Persian: Saturday=0 ... Friday=6.
    return (local_dt.weekday() + 2) % 7


def in_schedule(
    local_dt: datetime,
    weekdays: list[int] | None,
    ranges: list[tuple[time, time]],
) -> bool:
    if weekdays is not None and jalali_weekday_index(local_dt) not in weekdays:
        return False
    if not ranges:
        return True
    now = local_dt.time()
    for start, end in ranges:
        if start <= end:
            if start <= now <= end:
                return True
        elif now >= start or now <= end:  # overnight window, e.g. 22:00 -> 06:00
            return True
    return False


def build_rules(zones, *, default_min_confidence: float = 0.0) -> list[AlertRule]:
    """Turn ORM zones into alert rules."""
    rules: list[AlertRule] = []
    for zone in zones:
        if not getattr(zone, "alert_enabled", False) or not getattr(zone, "enabled", True):
            continue
        days, ranges = parse_schedule(getattr(zone, "schedule", None))
        classes = list(getattr(zone, "alert_classes", None) or [])
        rules.append(
            AlertRule(
                zone_id=zone.id,
                name=zone.name,
                object_types=classes,
                min_confidence=default_min_confidence,
                sound=bool(getattr(zone, "alert_sound", True)),
                weekdays=days,
                time_ranges=ranges,
            )
        )
    return rules


class AlertEngine:
    """Stateful evaluator that applies a cooldown per (camera, zone, class)."""

    def __init__(self, cooldown_seconds: float = 30.0) -> None:
        self.cooldown_seconds = float(cooldown_seconds)
        self._last_fired: dict[tuple[int, int, str], float] = {}

    def reset(self) -> None:
        self._last_fired.clear()

    def evaluate(
        self,
        rules: list[AlertRule],
        *,
        camera_id: int,
        camera_name: str,
        local_now: datetime,
        now_ts: float,
        zone_id: int | None = None,
        zone_ids: Iterable[int] | None = None,
        object_type: str | None = None,
        confidence: float = 0.0,
        event_type: str = "detection",
    ) -> list[AlertDecision]:
        """Evaluate every rule for the zones the object is currently inside.

        ``zone_ids`` is the complete set of containing zones. Matching on a
        single ``zone_id`` (the first containing polygon) made an alert zone
        nested inside a larger process region impossible to trigger: the object
        was reported as being in the outer region only.
        """
        accepted_zones = set(zone_ids) if zone_ids is not None else None
        decisions: list[AlertDecision] = []
        for rule in rules:
            if accepted_zones is not None:
                if rule.zone_id not in accepted_zones:
                    continue
            elif zone_id is not None and rule.zone_id != zone_id:
                continue
            if rule.object_types and (object_type or "") not in rule.object_types:
                continue
            if confidence < rule.min_confidence:
                continue
            if not in_schedule(local_now, rule.weekdays, rule.time_ranges):
                continue

            key = (camera_id, rule.zone_id, object_type or "*")
            last = self._last_fired.get(key)
            if last is not None and now_ts - last < self.cooldown_seconds:
                continue

            self._last_fired[key] = now_ts
            subject = object_type or "حرکت"
            title = f"هشدار {rule.name} - {camera_name}"
            message = (
                f"رویداد «{event_type}» برای «{subject}» در ناحیه «{rule.name}» "
                f"دوربین «{camera_name}» ثبت شد."
            )
            decisions.append(
                AlertDecision(
                    rule=rule,
                    object_type=object_type,
                    title=title,
                    message=message,
                    severity=rule.severity
                    or EVENT_SEVERITY.get(object_type or "", "warning"),
                    sound=rule.sound,
                )
            )
        return decisions

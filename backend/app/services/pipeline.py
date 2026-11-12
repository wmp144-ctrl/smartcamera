"""Per-camera detection pipeline.

``process(frame)`` performs motion detection, object detection, tracking,
quality scoring, zone/region filtering, tripwire crossing and alert evaluation,
and returns a :class:`PipelineResult` describing everything that happened.

The pipeline never touches the database or the filesystem: it emits *requests*
(events to log, snapshots to save, alerts to raise). That separation is what
makes the interesting logic unit-testable with synthetic frames and a
``FakeDetector``, and it keeps all DB I/O off the video thread.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from app.core.config import settings
from app.core.timeutil import to_local, utcnow
from app.services.alerts import AlertDecision, AlertEngine, AlertRule
from app.services.detector import Detector, filter_detections
from app.services.geometry import (
    box_center,
    box_center_in_polygon,
    box_size_norm,
    crossed_line,
    crossing_direction,
    direction_label_fa,
)
from app.services.motion import MotionDetector, MotionResult
from app.services.models_registry import class_label
from app.services.quality import QualityReport, evaluate_frame
from app.services.tracker import Detection, ObjectTracker, Track

logger = logging.getLogger(__name__)

PROCESS_KINDS = {"region", "alert"}
LINE_KIND = "line"


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------
@dataclass(slots=True)
class ZoneSpec:
    id: int
    name: str = ""
    kind: str = "region"
    points: list[list[float]] = field(default_factory=list)
    enabled: bool = True
    alert_enabled: bool = False
    alert_sound: bool = True
    alert_classes: list[str] = field(default_factory=list)
    schedule: dict | None = None

    @property
    def is_polygon(self) -> bool:
        return self.kind != LINE_KIND and len(self.points) >= 3

    @property
    def is_line(self) -> bool:
        return self.kind == LINE_KIND and len(self.points) >= 2


@dataclass
class PipelineConfig:
    camera_id: int
    camera_name: str = ""
    class_config: dict = field(default_factory=dict)
    zones: list[ZoneSpec] = field(default_factory=list)
    motion_sensitivity: float = 0.004
    require_motion: bool = True
    stationary_after_seconds: float = 8.0
    track_max_age_seconds: float = 2.0
    min_track_hits: int = 2
    best_image_improvement: float = 0.08
    grid_w: int = 64
    grid_h: int = 36
    record_objects: bool = True
    record_snapshots: bool = True
    alert_cooldown_seconds: float = 30.0
    direction_min_displacement: float = 0.01
    direction_window_seconds: float = 2.0
    line_min_displacement: float = 0.002


# --------------------------------------------------------------------------
# outputs
# --------------------------------------------------------------------------
@dataclass
class SnapshotRequest:
    runtime_track_id: int
    object_type: str
    kind: str
    bbox: list[float]
    confidence: float
    captured_at: datetime
    quality: QualityReport
    frame: np.ndarray | None = field(default=None, repr=False)
    best: bool = True
    replace_previous: bool = False
    # filled in by ``materialize_snapshots`` on the video thread so the frame
    # buffer can be released immediately (see services.persistence)
    saved: object | None = None


@dataclass
class EventDraft:
    event_type: str
    occurred_at: datetime
    object_type: str | None = None
    confidence: float = 0.0
    direction: str | None = None
    severity: str = "info"
    message: str = ""
    zone_id: int | None = None
    runtime_track_id: int | None = None
    bbox: list[float] = field(default_factory=list)
    ended_at: datetime | None = None
    attach_image_kind: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class TrackSnapshot:
    runtime_track_id: int
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
    bbox_first: list[float]
    bbox_last: list[float]
    status: str
    entered_frame: bool
    exited_frame: bool
    zone_id: int | None
    crossed_line_id: int | None
    line_direction: str | None
    frame_count: int
    meta: dict


@dataclass
class PipelineResult:
    timestamp: datetime
    camera_id: int
    frame_width: int = 0
    frame_height: int = 0
    ran_detection: bool = False
    motion: MotionResult | None = None
    detections: list[Detection] = field(default_factory=list)
    active_tracks: list[TrackSnapshot] = field(default_factory=list)
    started_tracks: list[TrackSnapshot] = field(default_factory=list)
    ended_tracks: list[TrackSnapshot] = field(default_factory=list)
    events: list[EventDraft] = field(default_factory=list)
    snapshots: list[SnapshotRequest] = field(default_factory=list)
    alerts: list[AlertDecision] = field(default_factory=list)
    prune_tracks: list[int] = field(default_factory=list)
    heatmap_points: list[tuple[tuple[float, float], str]] = field(default_factory=list)


@dataclass
class _TrackRuntime:
    first_seen: datetime
    bbox_first: list[float]
    last_seen: datetime | None = None
    has_best: bool = False
    best_quality: float = 0.0
    enter_event_sent: bool = False
    stationary_event_sent: bool = False
    moving_event_sent: bool = False
    exit_image_captured: bool = False
    zone_id: int | None = None
    crossed_lines: set[int] = field(default_factory=set)
    line_direction: str | None = None


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------
class DetectionPipeline:
    def __init__(
        self,
        config: PipelineConfig,
        detector: Detector | None = None,
        *,
        tracker: ObjectTracker | None = None,
        motion: MotionDetector | None = None,
        alert_engine: AlertEngine | None = None,
    ) -> None:
        self.config = config
        self.detector = detector
        self.tracker = tracker or ObjectTracker(
            max_age_seconds=config.track_max_age_seconds,
            min_hits=config.min_track_hits,
            direction_min_displacement=config.direction_min_displacement,
            stationary_after_seconds=config.stationary_after_seconds,
            history_seconds=config.direction_window_seconds,
        )
        self.motion = motion or MotionDetector(sensitivity=config.motion_sensitivity)
        self.alert_engine = alert_engine or AlertEngine(config.alert_cooldown_seconds)
        self._runtime: dict[int, _TrackRuntime] = {}
        self._alert_rules: list[AlertRule] = []
        self.rebuild_rules()

    # -- configuration -----------------------------------------------------
    def rebuild_rules(self) -> None:
        if not settings.alerts_enabled:
            # Master switch in Settings: zone rules stay configured but produce
            # no alerts (sirens/notifications off during e.g. maintenance).
            self._alert_rules = []
            return
        self._alert_rules = [
            AlertRule(
                zone_id=zone.id,
                name=zone.name or f"ناحیه {zone.id}",
                object_types=list(zone.alert_classes),
                sound=zone.alert_sound,
                weekdays=_schedule_days(zone.schedule),
                time_ranges=_schedule_ranges(zone.schedule),
            )
            for zone in self.config.zones
            if zone.alert_enabled and zone.enabled
        ]

    def set_detector(self, detector: Detector | None) -> None:
        self.detector = detector

    def reset(self) -> None:
        self.tracker.reset()
        self.motion.reset()
        self.alert_engine.reset()
        self._runtime.clear()

    # -- helpers -----------------------------------------------------------
    @property
    def polygon_zones(self) -> list[ZoneSpec]:
        return [z for z in self.config.zones if z.enabled and z.is_polygon]

    @property
    def line_zones(self) -> list[ZoneSpec]:
        return [z for z in self.config.zones if z.enabled and z.is_line]

    @property
    def process_zones(self) -> list[ZoneSpec]:
        return [z for z in self.polygon_zones if z.kind in PROCESS_KINDS]

    @property
    def ignore_zones(self) -> list[ZoneSpec]:
        return [z for z in self.polygon_zones if z.kind == "ignore"]

    def _min_motion(self, object_type: str) -> float:
        rule = self.config.class_config.get(object_type)
        if isinstance(rule, dict):
            return float(rule.get("min_motion", 0.0) or 0.0)
        if rule is not None:
            return float(getattr(rule, "min_motion", 0.0) or 0.0)
        return 0.0

    def _in_process_region(self, detection: Detection) -> bool:
        center = box_center(detection.bbox)
        for zone in self.ignore_zones:
            if box_center_in_polygon(detection.bbox, zone.points):
                return False
        process = self.process_zones
        if not process:
            return True
        # detections must be *fully* inside a process region to be handled
        for zone in process:
            if _box_inside_polygon(detection.bbox, zone.points):
                return True
        return False

    def _zone_for(self, track: Track) -> int | None:
        zones = self._zones_for(track)
        return zones[0] if zones else None

    def _zones_for(self, track: Track) -> list[int]:
        """Every process zone the object is currently inside (zone order).

        Returning *all* of them is what lets an alert zone nested inside a
        larger process region fire: with only the first match the event was
        always attributed to the outer region.
        """
        return [
            zone.id
            for zone in self.process_zones
            if box_center_in_polygon(track.bbox, zone.points)
        ]

    # -- main --------------------------------------------------------------
    def process(
        self,
        frame: np.ndarray,
        *,
        ts: datetime | None = None,
        now: float | None = None,
    ) -> PipelineResult:
        ts = ts or utcnow()
        now = now if now is not None else time.monotonic()
        array = np.asarray(frame)
        height, width = array.shape[:2]

        result = PipelineResult(
            timestamp=ts,
            camera_id=self.config.camera_id,
            frame_width=int(width),
            frame_height=int(height),
        )

        poly = [(z.id, z.points) for z in self.polygon_zones]
        motion_result = self.motion.update(array, poly)
        result.motion = motion_result

        active_runtime = self.tracker.tracks
        run_detection = bool(self.detector is not None)
        if run_detection and self.config.require_motion and not motion_result.motion:
            # keep watching while objects are (or may become) present
            run_detection = bool(active_runtime)

        detections: list[Detection] = []
        if run_detection and self.detector is not None:
            raw = self.detector.detect(array)
            filtered = filter_detections(raw, self.config.class_config)
            detections = [d for d in filtered if self._in_process_region(d)]
        result.ran_detection = run_detection
        result.detections = detections

        update = self.tracker.update(detections, now)

        for track in update.born:
            self._runtime[track.track_id] = _TrackRuntime(
                first_seen=ts, bbox_first=list(track.bbox), last_seen=ts
            )

        for track in update.active:
            self._handle_active(track, array, ts, now, result)

        for track in update.lost:
            self._handle_lost(track, array, ts, result)

        result.active_tracks = [
            self._snapshot(track, status="active") for track in update.active
        ]
        result.started_tracks = [
            self._snapshot(track, status="active") for track in update.born if track.confirmed
        ]
        result.ended_tracks = [self._snapshot(track, status="ended") for track in update.lost]

        # clean up runtime state for reaped tracks
        for track in update.lost:
            self._runtime.pop(track.track_id, None)
        return result

    # -- per-track handling -------------------------------------------------
    def _handle_active(
        self,
        track: Track,
        frame: np.ndarray,
        ts: datetime,
        now: float,
        result: PipelineResult,
    ) -> None:
        runtime = self._runtime.get(track.track_id)
        if runtime is None:
            runtime = _TrackRuntime(first_seen=ts, bbox_first=list(track.bbox), last_seen=ts)
            self._runtime[track.track_id] = runtime
        runtime.last_seen = ts
        if not track.confirmed:
            return

        zone_ids = self._zones_for(track)
        zone_id = zone_ids[0] if zone_ids else None
        runtime.zone_id = zone_id
        min_motion = self._min_motion(track.object_type)
        eligible = track.distance_travelled >= min_motion

        # Score the frame once and reuse it for every capture decision, so a
        # single frame never produces more than one stored image.
        report: QualityReport | None = None
        if self.config.record_snapshots:
            report = evaluate_frame(
                frame,
                _box_px(track.bbox, frame),
                track.confidence,
                frame_area=float(result.frame_width * result.frame_height),
            )

        # 1) entry -----------------------------------------------------------
        if eligible and not runtime.enter_event_sent:
            runtime.enter_event_sent = True
            result.events.append(
                EventDraft(
                    event_type="enter",
                    occurred_at=ts,
                    object_type=track.object_type,
                    confidence=round(track.confidence, 4),
                    direction=track.direction,
                    severity="info",
                    zone_id=zone_id,
                    runtime_track_id=track.track_id,
                    bbox=list(track.bbox),
                    message=(
                        f"ورود «{class_label(track.object_type)}» به کادر "
                        f"«{self.config.camera_name}»"
                    ),
                    attach_image_kind="enter",
                    meta={"frame_number": track.hits},
                )
            )
            if report is not None:
                result.snapshots.append(
                    self._snapshot_request(
                        track, frame, ts, kind="enter", best=True, quality=report
                    )
                )
                runtime.has_best = True
                runtime.best_quality = report.score

        if not eligible:
            return

        # 2) rolling best image (kept sharp, replaces the previous best) ------
        if report is not None:
            improves = report.score > runtime.best_quality + self.config.best_image_improvement
            if not runtime.has_best:
                result.snapshots.append(
                    self._snapshot_request(
                        track, frame, ts, kind="best", best=True, quality=report
                    )
                )
                runtime.has_best = True
                runtime.best_quality = report.score
            elif improves and runtime.best_quality < 1.0:
                result.snapshots.append(
                    self._snapshot_request(
                        track, frame, ts, kind="best", best=True,
                        quality=report, replace_previous=True,
                    )
                )
                runtime.best_quality = report.score

        # 3) motion state transitions ----------------------------------------
        if track.motion_state == "stationary" and not runtime.stationary_event_sent:
            runtime.stationary_event_sent = True
            runtime.moving_event_sent = False
            result.events.append(
                EventDraft(
                    event_type="stationary",
                    occurred_at=ts,
                    object_type=track.object_type,
                    confidence=round(track.confidence, 4),
                    direction="stationary",
                    severity="info",
                    zone_id=zone_id,
                    runtime_track_id=track.track_id,
                    bbox=list(track.bbox),
                    message=(
                        f"«{class_label(track.object_type)}» در "
                        f"«{self.config.camera_name}» به مدت "
                        f"{int(self.config.stationary_after_seconds)} ثانیه ثابت مانده است"
                    ),
                    meta={"motion_state": "stationary", "duration": round(track.duration, 2)},
                )
            )
            # A static object keeps exactly ONE image: the best one.
            if self.config.record_snapshots:
                result.prune_tracks.append(track.track_id)
        elif track.motion_state == "moving" and runtime.stationary_event_sent and not runtime.moving_event_sent:
            runtime.moving_event_sent = True
            runtime.stationary_event_sent = False
            result.events.append(
                EventDraft(
                    event_type="moving",
                    occurred_at=ts,
                    object_type=track.object_type,
                    confidence=round(track.confidence, 4),
                    direction=track.direction,
                    severity="info",
                    zone_id=zone_id,
                    runtime_track_id=track.track_id,
                    bbox=list(track.bbox),
                    message=f"«{class_label(track.object_type)}» مجدداً حرکت کرد",
                    meta={"motion_state": "moving"},
                )
            )

        # 4) tripwire crossings ----------------------------------------------
        prev_center = track.previous_center()
        if prev_center is not None:
            cur_center = track.center()
            disp = (
                (cur_center[0] - prev_center[0]) ** 2 + (cur_center[1] - prev_center[1]) ** 2
            ) ** 0.5
            if disp >= self.config.line_min_displacement:
                for line in self.line_zones:
                    if line.id in runtime.crossed_lines:
                        continue
                    if not crossed_line(prev_center, cur_center, line.points):
                        continue
                    runtime.crossed_lines.add(line.id)
                    runtime.line_direction = crossing_direction(prev_center, cur_center, line.points)
                    result.events.append(
                        EventDraft(
                            event_type="line_cross",
                            occurred_at=ts,
                            object_type=track.object_type,
                            confidence=round(track.confidence, 4),
                            direction=track.direction,
                            severity="warning",
                            zone_id=line.id,
                            runtime_track_id=track.track_id,
                            bbox=list(track.bbox),
                            message=(
                                f"عبور «{class_label(track.object_type)}» از خط "
                                f"«{line.name or line.id}» "
                                f"({direction_label_fa(track.direction)})"
                            ),
                            attach_image_kind="best",
                            meta={
                                "line_id": line.id,
                                "line_side": runtime.line_direction,
                                "movement": track.direction,
                            },
                        )
                    )
                    if self.config.record_snapshots:
                        result.snapshots.append(
                            self._snapshot_request(
                                track, frame, ts, kind="alert", best=False,
                                replace_previous=False,
                            )
                        )

        # 5) exit image while the object is still visible near the border -----
        if (
            self.config.record_snapshots
            and not runtime.exit_image_captured
            and track.touches_border(margin=0.02)
        ):
            runtime.exit_image_captured = True
            result.snapshots.append(
                self._snapshot_request(track, frame, ts, kind="exit", best=False)
            )

        # 6) alerts ------------------------------------------------------------
        if self._alert_rules:
            # Alert schedules are wall-clock windows, so they are evaluated in
            # the *configured* display timezone (never a hard-coded one).
            local_now = to_local(ts, settings.timezone) or ts
            decisions = self.alert_engine.evaluate(
                self._alert_rules,
                camera_id=self.config.camera_id,
                camera_name=self.config.camera_name,
                local_now=local_now,
                now_ts=now,
                zone_id=zone_id,
                zone_ids=zone_ids,
                object_type=track.object_type,
                confidence=track.confidence,
                event_type="detection",
            )
            for decision in decisions:
                result.alerts.append(decision)
                result.events.append(
                    EventDraft(
                        event_type="alert",
                        occurred_at=ts,
                        object_type=track.object_type,
                        confidence=round(track.confidence, 4),
                        direction=track.direction,
                        severity=decision.severity,
                        zone_id=decision.rule.zone_id,
                        runtime_track_id=track.track_id,
                        bbox=list(track.bbox),
                        message=decision.message,
                        attach_image_kind="best",
                        meta={"rule": decision.rule.name, "sound": decision.sound},
                    )
                )

        # 7) heatmap -----------------------------------------------------------
        result.heatmap_points.append((box_center(track.bbox), track.object_type))

    def _handle_lost(
        self, track: Track, frame: np.ndarray, ts: datetime, result: PipelineResult
    ) -> None:
        runtime = self._runtime.get(track.track_id)
        if runtime is None or not track.confirmed:
            return
        if not runtime.enter_event_sent:
            return  # never recorded (below the min-motion rule)

        result.events.append(
            EventDraft(
                event_type="exit",
                occurred_at=ts,
                object_type=track.object_type,
                confidence=round(track.confidence, 4),
                direction=track.direction,
                severity="info",
                zone_id=runtime.zone_id,
                runtime_track_id=track.track_id,
                bbox=list(track.bbox),
                message=(
                    f"خروج «{class_label(track.object_type)}» از کادر "
                    f"«{self.config.camera_name}»"
                ),
                attach_image_kind="exit" if runtime.exit_image_captured else "best",
                meta={
                    "duration": round(track.duration, 2),
                    "distance": round(track.distance_travelled, 4),
                    "exited_frame": True,
                },
            )
        )

    # -- snapshot helpers ---------------------------------------------------
    def _snapshot_request(
        self,
        track: Track,
        frame: np.ndarray,
        ts: datetime,
        *,
        kind: str,
        best: bool,
        quality: QualityReport | None = None,
        replace_previous: bool = False,
    ) -> SnapshotRequest:
        report = quality or evaluate_frame(
            frame, _box_px(track.bbox, frame), track.confidence
        )
        return SnapshotRequest(
            runtime_track_id=track.track_id,
            object_type=track.object_type,
            kind=kind,
            bbox=list(track.bbox),
            confidence=float(track.confidence),
            captured_at=ts,
            quality=report,
            frame=frame,
            best=best,
            replace_previous=replace_previous,
        )

    def _snapshot(self, track: Track, *, status: str) -> TrackSnapshot:
        runtime = self._runtime.get(track.track_id)
        first_seen = runtime.first_seen if runtime else utcnow()
        last_seen = (runtime.last_seen if runtime and runtime.last_seen else None) or utcnow()
        bbox_first = runtime.bbox_first if runtime else list(track.bbox)
        return TrackSnapshot(
            runtime_track_id=track.track_id,
            object_type=track.object_type,
            confidence=round(track.confidence, 4),
            first_seen=first_seen,
            last_seen=last_seen,
            duration_seconds=round(max(0.0, (last_seen - first_seen).total_seconds()), 3),
            motion_state=track.motion_state,
            direction=track.direction,
            velocity_x=round(track.velocity[0], 6),
            velocity_y=round(track.velocity[1], 6),
            distance_travelled=round(track.distance_travelled, 6),
            bbox_first=list(bbox_first),
            bbox_last=list(track.bbox),
            status=status,
            entered_frame=True,
            exited_frame=status == "ended",
            zone_id=runtime.zone_id if runtime else None,
            crossed_line_id=next(iter(runtime.crossed_lines)) if runtime and runtime.crossed_lines else None,
            line_direction=runtime.line_direction if runtime else None,
            frame_count=track.hits,
            meta={
                "hits": track.hits,
                "confirmed": track.confirmed,
                "touches_border": track.touches_border(),
                "size": list(box_size_norm(track.bbox)),
            },
        )


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _box_px(bbox_norm: list[float], frame: np.ndarray) -> list[float]:
    height, width = np.asarray(frame).shape[:2]
    return [
        bbox_norm[0] * width,
        bbox_norm[1] * height,
        bbox_norm[2] * width,
        bbox_norm[3] * height,
    ]


def _box_inside_polygon(box: list[float], polygon: list[list[float]]) -> bool:
    """True when the box *centre* is inside (kept lenient for partially visible
    objects at the region edge)."""
    return box_center_in_polygon(box, polygon)


def _schedule_days(schedule: dict | None) -> list[int] | None:
    from app.services.alerts import parse_schedule  # noqa: PLC0415

    days, _ = parse_schedule(schedule)
    return days


def _schedule_ranges(schedule: dict | None):
    from app.services.alerts import parse_schedule  # noqa: PLC0415

    _, ranges = parse_schedule(schedule)
    return ranges

"""A dependency-light multi-object tracker.

Implements the essentials of the SORT / ByteTrack family:

* per-track constant-velocity Kalman filter (numpy)
* class-aware greedy IoU association (no Hungarian/scipy dependency)
* track birth / death with ``min_hits`` confirmation and a time-based ``max_age``
* direction, speed, travelled distance and stationary detection from a
  sliding centroid history

Everything works in **normalised** coordinates (0..1) so tracking is
independent of camera resolution. The module has no heavy imports, which keeps
the whole thing unit-testable without PyTorch or a GPU.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

from app.services.geometry import (
    STATIONARY,
    box_center,
    box_size_norm,
    classify_direction,
    iou,
)

__all__ = ["Detection", "Track", "UpdateResult", "ObjectTracker"]


@dataclass(slots=True)
class Detection:
    """A single detector output in normalised coordinates."""

    bbox: list[float]  # [x1, y1, x2, y2] in 0..1
    confidence: float
    object_type: str

    def as_center(self) -> tuple[float, float]:
        return box_center(self.bbox)


@dataclass
class Track:
    """A live track."""

    track_id: int
    object_type: str
    bbox: list[float]
    confidence: float
    first_seen_ts: float
    last_seen_ts: float
    hits: int = 1
    age: int = 0
    confirmed: bool = False

    previous_bbox: list[float] | None = None
    velocity: tuple[float, float] = (0.0, 0.0)
    direction: str = STATIONARY
    motion_state: str = "moving"
    distance_travelled: float = 0.0
    stationary_since_ts: float | None = None
    motion_state_changed: bool = False
    previous_motion_state: str = "moving"

    # collected by the consumer (pipeline)
    zone_id: int | None = None
    meta: dict = field(default_factory=dict)
    _history: deque[tuple[float, float, float]] = field(default_factory=deque, repr=False)

    @property
    def duration(self) -> float:
        return max(0.0, self.last_seen_ts - self.first_seen_ts)

    def center(self) -> tuple[float, float]:
        return box_center(self.bbox)

    def previous_center(self) -> tuple[float, float] | None:
        if self.previous_bbox is None:
            return None
        return box_center(self.previous_bbox)

    def touches_border(self, margin: float = 0.01) -> bool:
        x1, y1, x2, y2 = self.bbox
        return bool(x1 <= margin or y1 <= margin or x2 >= 1.0 - margin or y2 >= 1.0 - margin)


class _KalmanBoxFilter:
    """Constant-velocity Kalman filter over ``[cx, cy, w, h, vx, vy, vw, vh]``."""

    _MEAS_NOISE = 1e-4
    _PROC_NOISE = 1e-6

    def __init__(self, bbox_norm: Sequence[float]) -> None:
        self.dim_x = 8
        self.dim_z = 4
        self.F = np.eye(self.dim_x)
        for i in range(self.dim_z):
            self.F[i, i + self.dim_z] = 1.0
        self.H = np.eye(self.dim_z, self.dim_x)
        self.R = np.eye(self.dim_z) * self._MEAS_NOISE
        self.Q = np.eye(self.dim_x) * self._PROC_NOISE
        self.P = np.eye(self.dim_x)
        self.P[: self.dim_z, : self.dim_z] *= 1e-2
        self.P[self.dim_z :, self.dim_z :] *= 1e-1
        self.x = np.zeros((self.dim_x, 1))
        self.x[: self.dim_z] = self._to_measurement(bbox_norm)

    @staticmethod
    def _to_measurement(bbox_norm: Sequence[float]) -> np.ndarray:
        x1, y1, x2, y2 = bbox_norm
        return np.array(
            [[(x1 + x2) / 2.0], [(y1 + y2) / 2.0], [x2 - x1], [y2 - y1]],
            dtype=np.float64,
        )

    def predict(self, dt: float = 1.0) -> None:
        dt = max(dt, 1e-3)
        for i in range(self.dim_z):
            self.F[i, i + self.dim_z] = dt
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q

    def update(self, bbox_norm: Sequence[float]) -> None:
        z = self._to_measurement(bbox_norm)
        y = z - self.H @ self.x
        S = self.H @ self.P @ self.H.T + self.R
        # Solve instead of inverting — numerically much more stable.
        K = np.linalg.solve(S, (self.P @ self.H.T).T).T
        self.x = self.x + K @ y
        self.P = (np.eye(self.dim_x) - K @ self.H) @ self.P

    def bbox(self) -> list[float]:
        cx, cy, w, h = (float(v) for v in self.x[: self.dim_z].flatten())
        w = max(w, 1e-4)
        h = max(h, 1e-4)
        return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]

    def velocity(self) -> tuple[float, float]:
        return float(self.x[4, 0]), float(self.x[5, 0])


@dataclass(slots=True)
class UpdateResult:
    active: list[Track] = field(default_factory=list)
    born: list[Track] = field(default_factory=list)
    lost: list[Track] = field(default_factory=list)


class ObjectTracker:
    """Per-camera tracker.

    Args:
        iou_threshold: minimum IoU to associate a detection with a track.
        max_age_seconds: how long a track survives without a matching detection.
        min_hits: detections required before a track is ``confirmed``.
        history_seconds: centroid history window used for direction/motion.
        direction_min_displacement: normalised displacement below which the
            object is considered not to have moved within the window.
        stationary_after_seconds: how long without movement before the track is
            marked ``stationary``.
    """

    def __init__(
        self,
        *,
        iou_threshold: float = 0.3,
        max_age_seconds: float = 2.0,
        min_hits: int = 2,
        history_seconds: float = 2.0,
        direction_min_displacement: float = 0.01,
        stationary_after_seconds: float = 8.0,
        center_gate: float = 0.5,
    ) -> None:
        self.iou_threshold = iou_threshold
        self.max_age_seconds = max_age_seconds
        self.min_hits = max(1, min_hits)
        self.history_seconds = history_seconds
        self.direction_min_displacement = direction_min_displacement
        self.stationary_after_seconds = stationary_after_seconds
        # IoU alone is too strict at low detection rates (5 fps), where an
        # object can jump more than its own box between frames. A second,
        # centre-proximity gate keeps identities stable without letting two
        # close objects swap places.
        self.center_gate = center_gate

        self._next_id = 1
        self._tracks: dict[int, Track] = {}
        self._filters: dict[int, _KalmanBoxFilter] = {}
        self._predicted: dict[int, list[float]] = {}
        self._last_ts: float | None = None

    # -- introspection -----------------------------------------------------
    @property
    def tracks(self) -> list[Track]:
        return list(self._tracks.values())

    def confirmed_tracks(self) -> list[Track]:
        return [t for t in self._tracks.values() if t.confirmed]

    def get(self, track_id: int) -> Track | None:
        return self._tracks.get(track_id)

    def reset(self) -> None:
        self._tracks.clear()
        self._filters.clear()
        self._predicted.clear()
        self._next_id = 1
        self._last_ts = None

    # -- main loop ---------------------------------------------------------
    def update(self, detections: Iterable[Detection], now: float) -> UpdateResult:
        detections = list(detections)
        dt = 0.0 if self._last_ts is None else max(0.0, now - self._last_ts)
        self._last_ts = now

        # 1) predict every track forward
        self._predicted.clear()
        for track_id, kf in self._filters.items():
            kf.predict(dt if dt > 0 else 1.0 / 30.0)
            self._predicted[track_id] = kf.bbox()

        # 2) greedy class-aware IoU association
        matches, unmatched_dets = self._associate(detections)

        # 3) update matched tracks
        result = UpdateResult()
        for track_id, det_index in matches.items():
            self._apply_detection(self._tracks[track_id], detections[det_index], now)

        # 4) birth
        for det_index in unmatched_dets:
            track = self._create_track(detections[det_index], now)
            result.born.append(track)

        # 5) death
        result.lost = self._reap(now)

        result.active = [t for t in self._tracks.values() if t.confirmed]
        return result

    # -- internals ---------------------------------------------------------
    def _pair_score(self, predicted: Sequence[float], det_bbox: Sequence[float]) -> float:
        """Association score: IoU first, centre proximity as a fallback.

        Matches with sufficient IoU score above 1.0 so they always win over
        proximity-only matches.
        """
        overlap = iou(predicted, det_bbox)
        if overlap >= self.iou_threshold:
            return 1.0 + overlap
        pw, ph = box_size_norm(predicted)
        dw, dh = box_size_norm(det_bbox)
        diagonal = max(math.hypot(pw, ph), math.hypot(dw, dh), 1e-6)
        pcx, pcy = box_center(predicted)
        dcx, dcy = box_center(det_bbox)
        distance = math.hypot(dcx - pcx, dcy - pcy)
        proximity = max(0.0, 1.0 - distance / diagonal)
        return proximity if proximity >= self.center_gate else 0.0

    def _associate(self, detections: list[Detection]) -> tuple[dict[int, int], list[int]]:
        matches: dict[int, int] = {}
        unmatched_dets = list(range(len(detections)))
        if not detections or not self._tracks:
            return matches, unmatched_dets

        pairs: list[tuple[float, int, int]] = []
        for track_id, track in self._tracks.items():
            predicted = self._predicted.get(track_id, track.bbox)
            for det_index, det in enumerate(detections):
                if det.object_type != track.object_type:
                    continue
                score = self._pair_score(predicted, det.bbox)
                if score > 0:
                    pairs.append((score, track_id, det_index))

        pairs.sort(key=lambda item: item[0], reverse=True)
        used_tracks: set[int] = set()
        used_dets: set[int] = set()
        for _score, track_id, det_index in pairs:
            if track_id in used_tracks or det_index in used_dets:
                continue
            matches[track_id] = det_index
            used_tracks.add(track_id)
            used_dets.add(det_index)

        unmatched_dets = [i for i in range(len(detections)) if i not in used_dets]
        return matches, unmatched_dets

    def _apply_detection(self, track: Track, det: Detection, now: float) -> None:
        prev_center = track.center()
        track.previous_bbox = list(track.bbox)
        track.bbox = list(det.bbox)
        track.confidence = float(det.confidence)
        track.hits += 1
        track.age += 1
        track.last_seen_ts = now
        if not track.confirmed and track.hits >= self.min_hits:
            track.confirmed = True
        self._filters[track.track_id].update(det.bbox)

        center = track.center()
        moved = ((center[0] - prev_center[0]) ** 2 + (center[1] - prev_center[1]) ** 2) ** 0.5
        track.distance_travelled += moved
        self._update_kinematics(track, now)

    def _create_track(self, det: Detection, now: float) -> Track:
        track_id = self._next_id
        self._next_id += 1
        track = Track(
            track_id=track_id,
            object_type=det.object_type,
            bbox=list(det.bbox),
            confidence=float(det.confidence),
            first_seen_ts=now,
            last_seen_ts=now,
            hits=1,
            age=1,
            confirmed=self.min_hits <= 1,
        )
        self._tracks[track_id] = track
        self._filters[track_id] = _KalmanBoxFilter(det.bbox)
        self._update_kinematics(track, now)
        return track

    def _update_kinematics(self, track: Track, now: float) -> None:
        cx, cy = track.center()
        track._history.append((now, cx, cy))
        cutoff = now - self.history_seconds
        while len(track._history) > 1 and track._history[0][0] < cutoff:
            track._history.popleft()

        old_ts, old_cx, old_cy = track._history[0]
        span = now - old_ts
        dx = cx - old_cx
        dy = cy - old_cy

        if span > 1e-3:
            track.velocity = (dx / span, dy / span)
        else:
            track.velocity = self._filters[track.track_id].velocity()

        track.direction = classify_direction(dx, dy, self.direction_min_displacement)

        previous = track.motion_state
        if track.direction == STATIONARY:
            if track.stationary_since_ts is None:
                track.stationary_since_ts = now
            if now - track.stationary_since_ts >= self.stationary_after_seconds:
                track.motion_state = "stationary"
        else:
            track.stationary_since_ts = None
            track.motion_state = "moving"

        track.previous_motion_state = previous
        track.motion_state_changed = previous != track.motion_state

    def _reap(self, now: float) -> list[Track]:
        lost: list[Track] = []
        for track_id in list(self._tracks.keys()):
            track = self._tracks[track_id]
            if now - track.last_seen_ts <= self.max_age_seconds:
                continue
            lost.append(track)
            del self._tracks[track_id]
            self._filters.pop(track_id, None)
            self._predicted.pop(track_id, None)
        return lost

    # -- diagnostics -------------------------------------------------------
    def summary(self) -> dict[str, int]:
        return {
            "tracks": len(self._tracks),
            "confirmed": len(self.confirmed_tracks()),
            "next_id": self._next_id,
        }

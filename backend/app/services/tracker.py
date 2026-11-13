"""A dependency-light multi-object tracker.

The design follows the ingredients that make today's strongest online trackers
work — ByteTrack's two-stage association, BoT-SORT's appearance fusion and
camera-motion compensation, and OC-SORT's observation-centric recovery — while
staying testable without a GPU and without a deep Re-ID network:

* per-track constant-velocity Kalman filter (numpy)
* **two-stage association**: high-confidence detections first, then the
  low-confidence boxes (a partially occluded object still keeps its identity)
* **Hungarian assignment** (scipy) with a greedy fallback when scipy is absent
* **appearance descriptor**: a compact colour histogram (EMA per track) fused
  with IoU when geometry alone is ambiguous — this is what stops two objects
  that cross paths from swapping identities
* **camera-motion compensation**: the global frame translation (phase
  correlation) is added to every prediction, so a panning camera does not look
  like every object moving/teleporting
* **observation-centric recovery**: a track that reappears after a short
  occlusion is matched against its last observation as well as its prediction
* track birth / death with ``min_hits`` confirmation and a time-based ``max_age``
* direction, speed, travelled distance and stationary detection from a
  sliding centroid history, plus a sampled path for the object replay view

Everything works in **normalised** coordinates (0..1) so tracking is
independent of camera resolution.
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

try:  # scipy is a project dependency, but the tracker must import without it
    from scipy.optimize import linear_sum_assignment as _linear_sum_assignment
except Exception:  # noqa: BLE001 - optional acceleration
    _linear_sum_assignment = None

__all__ = ["Detection", "Track", "UpdateResult", "ObjectTracker"]

# A pair that cannot be associated gets this cost in the assignment matrix.
_FORBIDDEN = 1e6
_DESCRIPTOR_SIZE = 32


@dataclass(slots=True)
class Detection:
    """A single detector output in normalised coordinates."""

    bbox: list[float]  # [x1, y1, x2, y2] in 0..1
    confidence: float
    object_type: str
    # Filled in by the tracker from the frame; kept flat so the dataclass stays
    # cheap to create for tests and for the motion-only path.
    descriptor: np.ndarray | None = None

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

    # -- association state -------------------------------------------------
    #: box the tracker last *observed* (not predicted); used by the recovery
    #: pass when an object reappears after an occlusion.
    last_observed_bbox: list[float] | None = None
    #: L2-normalised colour histogram of the object crop (EMA over the track).
    descriptor: np.ndarray | None = None
    #: frames since the last matched detection (0 = seen in this frame).
    time_since_update: int = 0

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
        iou_threshold: minimum IoU/proximity score for a motion-only match.
        max_age_seconds: how long a track survives without a matching detection.
        min_hits: detections required before a track is ``confirmed``.
        history_seconds: centroid history window used for direction/motion.
        direction_min_displacement: normalised displacement below which the
            object is considered not to have moved within the window.
        stationary_after_seconds: how long without movement before the track is
            marked ``stationary``.
        high_threshold: detection confidence at/above which the first association
            stage runs (ByteTrack's ``track_thresh``).
        low_threshold: minimum confidence for the second stage (ByteTrack's
            ``low_thresh``); lower boxes never spawn a new track.
        appearance_weight: 0 disables appearance fusion, 1 trusts only the
            colour descriptor.
        camera_motion: estimate and compensate the global frame translation.
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
        high_threshold: float = 0.5,
        low_threshold: float = 0.1,
        appearance_weight: float = 0.45,
        camera_motion: bool = True,
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
        self.high_threshold = high_threshold
        self.low_threshold = max(0.0, min(low_threshold, high_threshold))
        self.appearance_weight = max(0.0, min(1.0, appearance_weight))
        self.camera_motion = camera_motion

        self._next_id = 1
        self._tracks: dict[int, Track] = {}
        self._filters: dict[int, _KalmanBoxFilter] = {}
        self._predicted: dict[int, list[float]] = {}
        self._last_ts: float | None = None
        self._prev_gray: np.ndarray | None = None
        self._frame_shape: tuple[int, int] | None = None

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
        self._prev_gray = None
        self._frame_shape = None

    # -- main loop ---------------------------------------------------------
    def update(
        self,
        detections: Iterable[Detection],
        now: float,
        frame: np.ndarray | None = None,
    ) -> UpdateResult:
        """Associate ``detections`` with the live tracks.

        ``frame`` is optional: when given, appearance descriptors are extracted
        for every detection (and compared against the track's descriptor) and the
        global camera translation is estimated for motion compensation.
        """
        detections = list(detections)
        dt = 0.0 if self._last_ts is None else max(0.0, now - self._last_ts)
        self._last_ts = now

        shift = (0.0, 0.0)
        if frame is not None:
            shift = self._camera_shift(frame)
            self._describe(detections, frame)

        # 1) predict every track forward (plus the camera's own motion)
        self._predicted.clear()
        for track_id, kf in self._filters.items():
            kf.predict(dt if dt > 0 else 1.0 / 30.0)
            self._predicted[track_id] = _shift_box(kf.bbox(), shift)

        # 2) ByteTrack-style two-stage association
        high = [d for d in detections if d.confidence >= self.high_threshold]
        low = [
            d
            for d in detections
            if self.low_threshold <= d.confidence < self.high_threshold
        ]
        matches_high, unmatched_high = self._match(list(self._tracks.keys()), high)
        remaining_tracks = [tid for tid in self._tracks if tid not in matches_high]
        matches_low, _unmatched_low = self._match(remaining_tracks, low)

        # 3) update matched tracks (confidence never matters for the update, a
        #    partial/occluded box still keeps the Kalman and the kinematics fed)
        result = UpdateResult()
        for track_id, det_index in matches_high.items():
            self._apply_detection(self._tracks[track_id], high[det_index], now)
        for track_id, det_index in matches_low.items():
            self._apply_detection(self._tracks[track_id], low[det_index], now)

        # 4) birth — only confident boxes may spawn a track (ByteTrack rule)
        for det_index in unmatched_high:
            det = high[det_index]
            if self._is_duplicate(det):
                continue
            track = self._create_track(det, now)
            result.born.append(track)

        # 5) death / lost
        result.lost = self._reap(now)
        for track in self._tracks.values():
            track.age += 1

        result.active = [t for t in self._tracks.values() if t.confirmed]
        return result

    # -- association -------------------------------------------------------
    def _match(
        self, track_ids: list[int], detections: list[Detection]
    ) -> tuple[dict[int, int], list[int]]:
        """Hungarian association returning ``{track_id: detection_index}``."""
        if not track_ids or not detections:
            return {}, list(range(len(detections)))

        cost = np.full((len(track_ids), len(detections)), _FORBIDDEN, dtype=np.float64)
        for row, track_id in enumerate(track_ids):
            track = self._tracks[track_id]
            for column, det in enumerate(detections):
                if det.object_type != track.object_type:
                    continue
                value = self._pair_cost(track, det)
                if value is not None:
                    cost[row, column] = value

        matches: dict[int, int] = {}
        used: set[int] = set()
        if _linear_sum_assignment is not None and min(cost.shape) <= 64:
            rows, columns = _linear_sum_assignment(cost)
            for row, column in zip(rows, columns):
                if cost[row, column] >= _FORBIDDEN / 2:
                    continue
                matches[track_ids[row]] = int(column)
                used.add(int(column))
        else:
            # Greedy fallback: cheapest pair first, exactly what SORT does.
            pairs = sorted(
                (
                    (float(cost[row, column]), track_ids[row], column)
                    for row in range(cost.shape[0])
                    for column in range(cost.shape[1])
                    if cost[row, column] < _FORBIDDEN / 2
                ),
                key=lambda item: item[0],
            )
            used_tracks: set[int] = set()
            for _value, track_id, column in pairs:
                if track_id in used_tracks or column in used:
                    continue
                matches[track_id] = int(column)
                used_tracks.add(track_id)
                used.add(int(column))

        unmatched = [index for index in range(len(detections)) if index not in used]
        return matches, unmatched

    def _pair_cost(self, track: Track, det: Detection) -> float | None:
        """Association cost in ``[0, 1]`` (lower is better), or ``None``.

        Combines the motion score (IoU with the prediction, with the last
        observation as a recovery cue) and the appearance similarity. Appearance
        is only trusted when at least one of the two boxes is a plausible
        geometric neighbour, so two objects of the same class on opposite sides
        of the picture can never be swapped just because they look alike.
        """
        predicted = self._predicted.get(track.track_id, track.bbox)
        predicted_iou = iou(predicted, det.bbox)
        observed_iou = 0.0
        if track.last_observed_bbox is not None and track.time_since_update > 0:
            observed_iou = iou(track.last_observed_bbox, det.bbox)
        overlap = max(predicted_iou, observed_iou * 0.85)

        proximity = self._proximity(predicted, det.bbox)
        # ``iou_threshold`` is the gate for the motion cue; the recovery cue is
        # allowed a little more slack because the prediction has drifted.
        motion = max(
            overlap if overlap >= self.iou_threshold else 0.0,
            proximity if proximity >= self.center_gate else 0.0,
        )
        if motion <= 0.0:
            return None

        cost = 1.0 - motion
        if (
            self.appearance_weight > 0.0
            and track.descriptor is not None
            and det.descriptor is not None
        ):
            similarity = float(np.dot(track.descriptor, det.descriptor))
            similarity = max(0.0, min(1.0, (similarity + 1.0) / 2.0))
            weight = self.appearance_weight
            cost = (1.0 - weight) * cost + weight * (1.0 - similarity)
        return cost

    @staticmethod
    def _proximity(predicted: Sequence[float], det_bbox: Sequence[float]) -> float:
        pw, ph = box_size_norm(predicted)
        dw, dh = box_size_norm(det_bbox)
        diagonal = max(math.hypot(pw, ph), math.hypot(dw, dh), 1e-6)
        pcx, pcy = box_center(predicted)
        dcx, dcy = box_center(det_bbox)
        distance = math.hypot(dcx - pcx, dcy - pcy)
        return max(0.0, 1.0 - distance / diagonal)

    def _is_duplicate(self, det: Detection) -> bool:
        """True when a trusted track already covers this detection."""
        for track in self._tracks.values():
            if not track.confirmed or track.object_type != det.object_type:
                continue
            if iou(track.bbox, det.bbox) >= 0.7:
                return True
        return False

    # -- appearance & camera motion ---------------------------------------
    def _describe(self, detections: list[Detection], frame: np.ndarray) -> None:
        if self.appearance_weight <= 0.0:
            return
        for det in detections:
            if det.descriptor is None:
                det.descriptor = _crop_descriptor(frame, det.bbox)

    def _camera_shift(self, frame: np.ndarray) -> tuple[float, float]:
        """Global translation (normalised) between the previous and this frame.

        Uses phase correlation on a downscaled grey frame, the cheap stand-in for
        BoT-SORT's GMC: a panning camera moves every pixel by the same amount, and
        adding that shift to the predictions keeps IoU association meaningful.
        """
        try:
            import cv2  # noqa: PLC0415
        except Exception:  # noqa: BLE001 - OpenCV is optional here
            return (0.0, 0.0)
        if not self.camera_motion:
            return (0.0, 0.0)
        array = np.asarray(frame)
        if array.ndim != 3 or array.shape[0] < 16 or array.shape[1] < 16:
            return (0.0, 0.0)
        height, width = array.shape[:2]
        scale = min(1.0, 320.0 / max(width, 1))
        try:
            gray = cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)
            if scale < 1.0:
                gray = cv2.resize(
                    gray, (max(16, int(width * scale)), max(16, int(height * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            gray = gray.astype(np.float32)
        except Exception:  # noqa: BLE001
            return (0.0, 0.0)

        previous = self._prev_gray
        self._prev_gray = gray
        self._frame_shape = (height, width)
        if previous is None or previous.shape != gray.shape:
            return (0.0, 0.0)
        try:
            (dx, dy), response = cv2.phaseCorrelate(previous, gray)
        except Exception:  # noqa: BLE001
            return (0.0, 0.0)
        if not math.isfinite(dx) or not math.isfinite(dy) or response < 0.15:
            return (0.0, 0.0)
        norm_x = dx / gray.shape[1]
        norm_y = dy / gray.shape[0]
        # A shift larger than a fifth of the frame is a scene cut, not motion.
        if abs(norm_x) > 0.2 or abs(norm_y) > 0.2:
            return (0.0, 0.0)
        return (norm_x, norm_y)

    # -- track lifecycle ---------------------------------------------------
    def _apply_detection(self, track: Track, det: Detection, now: float) -> None:
        prev_center = track.center()
        track.previous_bbox = list(track.bbox)
        track.bbox = list(det.bbox)
        track.last_observed_bbox = list(det.bbox)
        track.confidence = float(det.confidence)
        track.hits += 1
        track.last_seen_ts = now
        track.time_since_update = 0
        if not track.confirmed and track.hits >= self.min_hits:
            track.confirmed = True
        self._filters[track.track_id].update(det.bbox)
        if det.descriptor is not None:
            track.descriptor = _blend_descriptor(track.descriptor, det.descriptor)

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
            last_observed_bbox=list(det.bbox),
            descriptor=None if det.descriptor is None else det.descriptor.copy(),
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
                track.time_since_update += 1
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


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _shift_box(bbox: Sequence[float], shift: tuple[float, float]) -> list[float]:
    dx, dy = shift
    if dx == 0.0 and dy == 0.0:
        return list(bbox)
    return [bbox[0] + dx, bbox[1] + dy, bbox[2] + dx, bbox[3] + dy]


def _crop_descriptor(frame: np.ndarray, bbox: Sequence[float]) -> np.ndarray | None:
    """Compact colour histogram of an object crop (``_DESCRIPTOR_SIZE`` dims).

    A colour/tone signature is not as strong as a learned Re-ID embedding, but it
    costs microseconds, needs no extra model, and is enough to separate two
    objects of the same class that cross each other.
    """
    try:
        import cv2  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    array = np.asarray(frame)
    if array.ndim != 3:
        return None
    height, width = array.shape[:2]
    x1 = int(max(0, min(width - 1, bbox[0] * width)))
    y1 = int(max(0, min(height - 1, bbox[1] * height)))
    x2 = int(max(x1 + 2, min(width, bbox[2] * width)))
    y2 = int(max(y1 + 2, min(height, bbox[3] * height)))
    crop = array[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    try:
        crop = cv2.resize(crop, (24, 48), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        # 2x2 spatial grid x (H:4 bins, S:4 bins) = 32 values.
        cells: list[np.ndarray] = []
        for row in range(2):
            for column in range(2):
                patch = hsv[row * 24 : (row + 1) * 24, column * 12 : (column + 1) * 12]
                hue = np.histogram(patch[:, :, 0], bins=4, range=(0, 180))[0]
                sat = np.histogram(patch[:, :, 1], bins=4, range=(0, 256))[0]
                cells.append(np.concatenate([hue, sat]).astype(np.float32))
        vector = np.concatenate(cells)
    except Exception:  # noqa: BLE001
        return None
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        return None
    vector = vector / norm
    if vector.size != _DESCRIPTOR_SIZE:  # defensive: keep the dot product aligned
        return None
    return vector.astype(np.float32)


def _blend_descriptor(current: np.ndarray | None, new: np.ndarray) -> np.ndarray:
    if current is None or current.shape != new.shape:
        return new.copy()
    blended = current * 0.85 + new * 0.15
    norm = float(np.linalg.norm(blended))
    if norm <= 0.0:
        return new.copy()
    return (blended / norm).astype(np.float32)

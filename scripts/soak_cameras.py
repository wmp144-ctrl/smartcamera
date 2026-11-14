"""Multi-camera soak test for CameraSmart.

Measures a running server (CPU or GPU) with synthetic RTSP cameras and prints a
summary that can be quoted in a report:

* frames/s and detections/s per camera
* inference time and queue wait (the scheduler's own numbers)
* frames replaced in the inference slot and decode drops
* process CPU, RSS and VRAM growth over the run (leak check)
* persistence queue depth, drops and errors

Examples::

    # four cameras for three minutes, keeping the cameras in the database
    python scripts/soak_cameras.py --url http://127.0.0.1:8999 \\
        --password Soak@12345 --cameras 4 --seconds 180 --rtsp rtsp://127.0.0.1:8554/live

    # eight cameras, then remove them again
    python scripts/soak_cameras.py --cameras 8 --seconds 120 --cleanup

The server must already be running; this script never starts or stops it and
never touches a camera it did not create (names are prefixed ``soak-``).
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from typing import Any


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(self, method: str, path: str, payload: Any | None = None) -> Any:
        data = None
        headers = {"Origin": self.base}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=headers, method=method
        )
        try:
            # The first camera of a run may trigger a cold model load (weights +
            # trace + FP16 benchmark); give it room instead of failing.
            with self.opener.open(request, timeout=120) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise SystemExit(f"{method} {path} -> {exc.code}: {detail}") from exc
        if not body:
            return None
        return json.loads(body)

    def login(self, password: str) -> None:
        self.request("POST", "/api/auth/login", {"password": password})


def create_cameras(
    client: Client,
    count: int,
    rtsp: str,
    *,
    detection_fps: float,
    stream_fps: int,
    model: str,
) -> list[int]:
    ids: list[int] = []
    for index in range(1, count + 1):
        payload = {
            "name": f"soak-{index:02d}",
            "location": "soak test",
            "rtsp_url": rtsp,
            "transport": "tcp",
            "fps": stream_fps,
            "detection_fps": detection_fps,
            "detection_enabled": True,
            "model_name": model,
            "enabled": True,
            "record_snapshots": True,
            "record_objects": True,
        }
        camera = client.request("POST", "/api/cameras", payload)
        ids.append(int(camera["id"]))
    return ids


def sample(client: Client) -> dict[str, Any]:
    runtime = client.request("GET", "/api/system/runtime")
    manager = runtime.get("manager", {})
    cameras = {
        str(item["camera_id"]): item
        for item in runtime.get("cameras", [])
        if str(item.get("camera_name", "")).startswith("soak-")
    }
    return {"at": time.time(), "manager": manager, "cameras": cameras}


def summarise(samples: list[dict[str, Any]]) -> None:
    if len(samples) < 2:
        print("not enough samples")
        return
    first, last = samples[0], samples[-1]
    print("\n=== per camera (mean / min / max of the sampling window) ===")
    print(
        f"{'camera':<22}{'state':<12}{'fps':>14}{'det fps':>14}"
        f"{'infer ms':>13}{'wait ms':>12}{'drop':>8}{'reconn':>7}"
    )
    for camera_id in sorted(first["cameras"]):
        rows = [s["cameras"][camera_id] for s in samples if camera_id in s["cameras"]]
        if not rows:
            continue
        fps = [float(row.get("fps") or 0.0) for row in rows]
        det = [float(row.get("detection_fps") or 0.0) for row in rows]
        infer = [float(row.get("inference_ms") or 0.0) for row in rows]
        wait = [float(row.get("queue_wait_ms") or 0.0) for row in rows]
        print(
            f"{rows[-1].get('camera_name', camera_id):<22}"
            f"{rows[-1].get('state', '?'):<12}"
            f"{statistics.fmean(fps):>6.1f}/{min(fps):>5.1f}/{max(fps):>5.1f}"
            f"{statistics.fmean(det):>6.1f}/{min(det):>5.1f}/{max(det):>5.1f}"
            f"{statistics.fmean(infer):>13.0f}"
            f"{statistics.fmean(wait):>12.0f}"
            f"{rows[-1].get('detection_dropped', 0):>8}"
            f"{rows[-1].get('reconnect_count', 0):>7}"
        )

    def series(path: tuple[str, ...]) -> list[float]:
        values: list[float] = []
        for item in samples:
            node: Any = item
            for key in path:
                node = (node or {}).get(key) if isinstance(node, dict) else None
            if isinstance(node, (int, float)):
                values.append(float(node))
        return values

    def report(label: str, values: list[float], unit: str = "") -> None:
        if not values:
            print(f"{label:<34} —")
            return
        print(
            f"{label:<34} start {values[0]:>8.1f}{unit}  end {values[-1]:>8.1f}{unit}"
            f"  max {max(values):>8.1f}{unit}  growth {values[-1] - values[0]:>+8.1f}{unit}"
        )

    print("\n=== process / queues (first -> last sample) ===")
    report("RSS (MB)", series(("manager", "resources", "process_rss_mb")), " MB")
    report("process CPU (%)", series(("manager", "resources", "process_cpu_percent")), "%")
    report("threads", series(("manager", "resources", "threads")))
    report("VRAM reserved (MB)", series(("manager", "resources", "vram_used_mb")), " MB")
    report("inference ms", series(("manager", "inference", "inference_ms")), " ms")
    report("inference queue wait ms", series(("manager", "inference", "queue_wait_ms")), " ms")
    report("inference pending", series(("manager", "inference", "pending")))
    report("detection fps total", series(("manager", "detection_fps")), " fps")
    report("persist queue depth", series(("manager", "persistence", "queue_depth")))
    report("persist queue wait ms", series(("manager", "persistence", "queue_wait_ms")), " ms")

    dropped_det = series(("manager", "inference", "dropped"))
    if dropped_det:
        print(f"frames replaced in the inference slot (total): {dropped_det[-1]:.0f}")
    snap = last.get("manager", {}).get("snapshots", {})
    print(f"snapshots written/dropped/errors: {snap.get('written')}/{snap.get('dropped')}/{snap.get('errors')}")
    persist_errors = last.get("manager", {}).get("persistence", {}).get("errors")
    print(f"persistence errors: {persist_errors}")
    budget = last.get("manager", {}).get("detection_budget", {})
    print(f"detection budget overloaded: {budget.get('overloaded')} (suggested {budget.get('suggested_model')})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8999")
    parser.add_argument("--password", default="Soak@12345")
    parser.add_argument("--rtsp", default="rtsp://127.0.0.1:8554/live")
    parser.add_argument("--cameras", type=int, default=4)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--detection-fps", type=float, default=5.0)
    parser.add_argument("--stream-fps", type=int, default=20)
    parser.add_argument("--model", default="rf-detr-base")
    parser.add_argument("--cleanup", action="store_true", help="delete the soak cameras at the end")
    parser.add_argument("--out", default="", help="write raw samples to this JSON file")
    args = parser.parse_args()

    client = Client(args.url)
    client.login(args.password)
    ids = create_cameras(
        client,
        args.cameras,
        args.rtsp,
        detection_fps=args.detection_fps,
        stream_fps=args.stream_fps,
        model=args.model,
    )
    print(f"created cameras: {ids}; sampling for {args.seconds:.0f}s")

    samples: list[dict[str, Any]] = []
    deadline = time.time() + args.seconds
    try:
        while time.time() < deadline:
            time.sleep(args.interval)
            try:
                samples.append(sample(client))
            except SystemExit as exc:  # server went away: report what we have
                print(f"sampling stopped: {exc}")
                break
            last = samples[-1]
            states = {item.get("state") for item in last["cameras"].values()}
            print(
                f"t+{len(samples) * args.interval:5.0f}s "
                f"states={sorted(states)} "
                f"rss={last['manager'].get('resources', {}).get('process_rss_mb')}MB "
                f"cpu={last['manager'].get('resources', {}).get('process_cpu_percent')}% "
                f"det={last['manager'].get('detection_fps')}fps "
                f"infer={last['manager'].get('inference', {}).get('inference_ms')}ms "
                f"queue={last['manager'].get('persistence', {}).get('queue_depth')}"
            )
    finally:
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(samples, handle, ensure_ascii=False)
            print(f"raw samples -> {args.out}")
        summarise(samples)
        if args.cleanup:
            for camera_id in ids:
                try:
                    client.request("DELETE", f"/api/cameras/{camera_id}")
                except SystemExit as exc:
                    print(f"could not delete camera {camera_id}: {exc}")
            print(f"removed cameras: {ids}")


if __name__ == "__main__":
    sys.exit(main())

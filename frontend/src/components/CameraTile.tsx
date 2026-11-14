import { ExternalLink, Eye, EyeOff, ScanEye, TriangleAlert, Video, VideoOff } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Camera, type CameraStatus } from "../lib/api";
import {
  cameraStateLabel,
  classLabel,
  cn,
  faNumber,
  formatRelative,
  stateTone,
  toFaDigits,
} from "../lib/utils";
import { useLive } from "../store/live";
import { useUI } from "../store/ui";
import { StatusDot } from "./ui/badge";

export function CameraTile({
  camera,
  status,
  live = true,
  className,
}: {
  camera: Camera;
  status?: CameraStatus;
  live?: boolean;
  className?: string;
}) {
  const overlay = useUI((state) => state.isOverlay(camera.id));
  const setOverlay = useUI((state) => state.setOverlay);
  const detection = useLive((state) => state.detections[camera.id]);
  const gridLiveFps = useUI((state) => state.gridLiveFps);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // A tile that scrolled out of view (or a background tab) must not keep an
  // MJPEG connection open: the backend encodes and the browser decodes every
  // frame, which is pure CPU for pixels nobody sees.
  const [visible, setVisible] = useState(true);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = containerRef.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => setVisible(entries.some((entry) => entry.isIntersecting)),
      { rootMargin: "160px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // A dropped MJPEG connection is normal (camera restarted, backend busy);
  // retry with backoff instead of leaving a dead frame on screen forever.
  useEffect(() => {
    if (!failed) return;
    const delay = Math.min(15_000, 1500 * 2 ** Math.min(attempt, 4));
    const timer = window.setTimeout(() => {
      setFailed(false);
      setAttempt((value) => value + 1);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [failed, attempt]);

  const tone = stateTone(status?.state);
  const resolution =
    status && status.width > 0 && status.height > 0
      ? `${toFaDigits(status.width)}×${toFaDigits(status.height)}`
      : camera.width && camera.height
        ? `${toFaDigits(camera.width)}×${toFaDigits(camera.height)}`
        : "نامشخص";

  const liveUrl = useMemo(
    // Grid tiles do not need the camera's full frame rate: each MJPEG stream
    // costs CPU in the backend, so the tile asks for the user-configured rate
    // (default 10 fps) capped by the camera's own stream rate. `attempt` is a
    // cache-buster that re-opens the stream after a failure.
    () =>
      `${api.cameras.liveUrl(camera.id, overlay, Math.min(camera.fps || 20, gridLiveFps))}${
        attempt ? `&r=${attempt}` : ""
      }`,
    [camera.id, camera.fps, overlay, gridLiveFps, attempt],
  );

  return (
    <div ref={containerRef} className={cn("panel group overflow-hidden", className)}>
      <div className="relative aspect-video w-full overflow-hidden bg-[#050a14]">
        {live && !failed && visible ? (
          <img
            key={liveUrl}
            src={liveUrl}
            alt={`نمای زنده ${camera.name}`}
            className="h-full w-full object-contain"
            onError={() => setFailed(true)}
            loading="lazy"
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-muted">
            <VideoOff className="size-7" />
            <span className="text-[11px]">
              {camera.enabled ? "تصویری در دسترس نیست" : "دوربین غیرفعال است"}
            </span>
          </div>
        )}

        <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-2 bg-gradient-to-b from-[#020617]/85 to-transparent p-2.5">
          <span className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-[#020617]/70 px-2 py-0.5 text-[11px] backdrop-blur">
            <StatusDot tone={tone} pulse={status?.state === "online"} />
            {cameraStateLabel(status?.state ?? (camera.enabled ? "unknown" : "disabled"))}
          </span>
          <div className="pointer-events-auto flex items-center gap-1">
            {camera.detection_enabled && status?.detector_error ? (
              <span
                title={`موتور تشخیص بارگذاری نشده است: ${status.detector_error}`}
                className="inline-flex items-center gap-1 rounded-full border border-warn/50 bg-[#020617]/70 px-2 py-0.5 text-[11px] text-warn backdrop-blur"
              >
                <TriangleAlert className="size-3" />
                تشخیص غیرفعال
              </span>
            ) : null}
            {camera.detection_enabled ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-brand/40 bg-[#020617]/70 px-2 py-0.5 text-[11px] text-brand backdrop-blur">
                <ScanEye className="size-3" />
                <span className="num">{toFaDigits(detection?.tracks.length ?? 0)}</span>
              </span>
            ) : null}
            <button
              type="button"
              onClick={() => setOverlay(camera.id, !overlay)}
              title={overlay ? "نمایش تصویر خام" : "نمایش اشیای علامت‌گذاری‌شده"}
              className="grid size-7 place-items-center rounded-lg border border-border/70 bg-[#020617]/70 text-muted backdrop-blur transition-colors hover:text-brand"
            >
              {overlay ? <Eye className="size-3.5 text-brand" /> : <EyeOff className="size-3.5" />}
            </button>
          </div>
        </div>

        {/* Objects detected right now are clickable: each chip opens the replay
            page of that object (all of its frames plus its movement path). */}
        {detection && detection.tracks.some((track) => track.db_id) ? (
          <div className="absolute left-2 top-10 flex max-w-[75%] flex-wrap gap-1">
            {detection.tracks.slice(0, 4).map((track) =>
              track.db_id ? (
                <Link
                  key={track.track_id}
                  to={`/objects/${track.db_id}`}
                  title="نمایش همه فریم‌ها و مسیر حرکت این شیء"
                  className="inline-flex items-center gap-1 rounded-full border border-brand/50 bg-[#020617]/80 px-2 py-0.5 text-[10px] text-brand backdrop-blur transition-colors hover:bg-brand/20"
                >
                  <ScanEye className="size-3" />
                  {classLabel(track.object_type)}
                </Link>
              ) : null,
            )}
          </div>
        ) : null}

        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-2 bg-gradient-to-t from-[#020617] via-[#020617]/70 to-transparent px-2.5 pb-2.5 pt-10">
          <div className="min-w-0 [text-shadow:0_1px_4px_rgb(0_0_0/0.95)]">
            <Link
              to={`/cameras/${camera.id}`}
              className="pointer-events-auto block truncate text-xs font-semibold hover:text-brand"
            >
              {camera.name}
            </Link>
            <p className="truncate text-[11px] text-muted">
              {camera.location || "بدون موقعیت"} — {resolution}
            </p>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-[11px] text-muted">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="inline-flex items-center gap-1">
            <Video className="size-3" />
            <span className="num">{faNumber(status?.fps ?? 0, 1)}</span> فریم
          </span>
          <span className="inline-flex items-center gap-1">
            <ScanEye className="size-3" />
            <span className="num">{faNumber(status?.detection_fps ?? 0, 1)}</span> تشخیص
          </span>
          {/* Live RTSP sessions rarely advertise an encoded bitrate; 0 means
              "unknown" and must not be rendered as "0.0 Mbps". */}
          <span className="num">
            {status && status.bitrate_kbps > 0
              ? `${faNumber(status.bitrate_kbps / 1000, 1)} Mbps`
              : "—"}
          </span>
          {status?.reconnect_count ? (
            <span className="num text-warn">اتصال مجدد {toFaDigits(status.reconnect_count)}</span>
          ) : null}
          {status?.last_frame_at ? <span>{formatRelative(status.last_frame_at)}</span> : null}
        </div>

        <div className="flex items-center gap-1">
          {detection && detection.tracks.length > 0 ? (
            <span className="text-brand">{classLabel(detection.tracks[0].object_type)}</span>
          ) : null}
          <Link
            to={`/cameras/${camera.id}`}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] text-muted transition-colors hover:bg-surface-2 hover:text-fg"
          >
            <ExternalLink className="size-3" />
            جزئیات
          </Link>
        </div>
      </div>
    </div>
  );
}

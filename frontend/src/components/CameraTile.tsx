import { ExternalLink, Eye, EyeOff, Gauge, ScanEye, TriangleAlert, Video, VideoOff } from "lucide-react";
import { useMemo, useState } from "react";
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
import { Badge, StatusDot } from "./ui/badge";

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
  const [failed, setFailed] = useState(false);

  const tone = stateTone(status?.state);
  const resolution =
    status && status.width > 0 && status.height > 0
      ? `${toFaDigits(status.width)}×${toFaDigits(status.height)}`
      : camera.width && camera.height
        ? `${toFaDigits(camera.width)}×${toFaDigits(camera.height)}`
        : "نامشخص";

  const liveUrl = useMemo(
    () => api.cameras.liveUrl(camera.id, overlay, Math.min(camera.fps || 20, 30)),
    [camera.id, camera.fps, overlay],
  );

  return (
    <div className={cn("panel group overflow-hidden", className)}>
      <div className="relative aspect-video w-full overflow-hidden bg-[#050a14]">
        {live && !failed ? (
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
          <span className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-[#020617]/70 px-2 py-0.5 text-[10px] backdrop-blur">
            <StatusDot tone={tone} pulse={status?.state === "online"} />
            {cameraStateLabel(status?.state ?? (camera.enabled ? "unknown" : "disabled"))}
          </span>
          <div className="pointer-events-auto flex items-center gap-1">
            {camera.detection_enabled && status?.detector_error ? (
              <span
                title={`موتور تشخیص بارگذاری نشده است: ${status.detector_error}`}
                className="inline-flex items-center gap-1 rounded-full border border-warn/50 bg-[#020617]/70 px-2 py-0.5 text-[10px] text-warn backdrop-blur"
              >
                <TriangleAlert className="size-3" />
                تشخیص غیرفعال
              </span>
            ) : null}
            {camera.detection_enabled ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-brand/40 bg-[#020617]/70 px-2 py-0.5 text-[10px] text-brand backdrop-blur">
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

        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-2 bg-gradient-to-t from-[#020617]/90 to-transparent p-2.5">
          <div className="min-w-0">
            <Link
              to={`/cameras/${camera.id}`}
              className="pointer-events-auto block truncate text-xs font-semibold hover:text-brand"
            >
              {camera.name}
            </Link>
            <p className="truncate text-[10px] text-muted">
              {camera.location || "بدون موقعیت"} — {resolution}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 text-[10px] text-muted">
            <Badge tone={status?.state === "online" ? "ok" : "muted"}>
              <Gauge className="size-3" />
              <span className="num">{faNumber(status?.fps ?? 0, 1)}</span>
            </Badge>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-[10px] text-muted">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="inline-flex items-center gap-1">
            <Video className="size-3" />
            <span className="num">{faNumber(status?.fps ?? 0, 1)}</span> فریم
          </span>
          <span className="inline-flex items-center gap-1">
            <ScanEye className="size-3" />
            <span className="num">{faNumber(status?.detection_fps ?? 0, 1)}</span> تشخیص
          </span>
          <span className="num">{faNumber((status?.bitrate_kbps ?? 0) / 1000, 1)} Mbps</span>
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
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] text-muted transition-colors hover:bg-surface-2 hover:text-fg"
          >
            <ExternalLink className="size-3" />
            جزئیات
          </Link>
        </div>
      </div>
    </div>
  );
}

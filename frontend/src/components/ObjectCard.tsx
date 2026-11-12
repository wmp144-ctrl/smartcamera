import { ArrowUpRight, Clock, Move, ScanEye, Sparkles, Square } from "lucide-react";
import { Link } from "react-router-dom";
import type { TrackDetail } from "../lib/api";
import {
  DIRECTION_ARROWS,
  classLabel,
  cn,
  directionLabel,
  faNumber,
  formatRelative,
  toFaDigits,
} from "../lib/utils";
import { formatJalaliDateTime } from "../lib/jalali";
import { Badge } from "./ui/badge";

export function ObjectCard({
  track,
  cameraName,
  className,
}: {
  track: TrackDetail;
  cameraName?: string;
  className?: string;
}) {
  const best = track.best_image ?? track.images[0] ?? null;
  const stationary = track.motion_state === "stationary";

  return (
    <div className={cn("panel group overflow-hidden transition-all hover:border-brand/40", className)}>
      <div className="grid grid-cols-[1fr_auto] gap-2 p-2">
        <Link
          to={`/objects/${track.id}`}
          className="relative block aspect-video overflow-hidden rounded-lg border border-border bg-[#050a14]"
        >
          {best?.full_url ? (
            <img
              src={best.full_url}
              alt={`تصویر کامل ${classLabel(track.object_type)}`}
              className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
              loading="lazy"
            />
          ) : (
            <span className="grid h-full w-full place-items-center text-[10px] text-muted">تصویر کامل</span>
          )}
          <span className="absolute bottom-1 right-1 rounded bg-[#020617]/80 px-1.5 py-0.5 text-[9px] text-muted">
            کامل
          </span>
        </Link>

        <Link
          to={`/objects/${track.id}`}
          className="relative block h-full w-20 overflow-hidden rounded-lg border border-border bg-[#050a14] sm:w-24"
        >
          {best?.crop_url ? (
            <img
              src={best.crop_url}
              alt={`برش ${classLabel(track.object_type)}`}
              className="h-full w-full object-cover"
              loading="lazy"
            />
          ) : (
            <span className="grid h-full w-full place-items-center text-[10px] text-muted">برش</span>
          )}
          <span className="absolute bottom-1 right-1 rounded bg-[#020617]/80 px-1.5 py-0.5 text-[9px] text-muted">
            برش
          </span>
        </Link>
      </div>

      <div className="space-y-2 px-3 pb-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-xs font-semibold">{classLabel(track.object_type)}</span>
            <Badge tone={stationary ? "warn" : "brand"}>
              {stationary ? <Square className="size-3" /> : <Move className="size-3" />}
              {stationary ? "ثابت" : "متحرک"}
            </Badge>
          </div>
          <Link
            to={`/objects/${track.id}`}
            className="shrink-0 text-muted transition-colors hover:text-brand"
            title="صفحه اختصاصی شیء"
          >
            <ArrowUpRight className="size-4" />
          </Link>
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted">
          <span className="inline-flex items-center gap-1">
            <ScanEye className="size-3" /> اطمینان{" "}
            <span className="num">{faNumber(track.confidence * 100, 0)}٪</span>
          </span>
          <span className="inline-flex items-center gap-1">
            حرکت {DIRECTION_ARROWS[track.direction] ?? "•"} {directionLabel(track.direction)}
          </span>
          {best ? (
            <span className="inline-flex items-center gap-1">
              <Sparkles className="size-3" /> کیفیت{" "}
              <span className="num">{faNumber(best.quality_score * 100, 0)}٪</span>
            </span>
          ) : null}
          <span className="inline-flex items-center gap-1">
            <Clock className="size-3" /> {faNumber(track.duration_seconds, 1)} ثانیه
          </span>
          <span className="num">{toFaDigits(track.frame_count)} فریم</span>
        </div>

        <p className="truncate text-[10px] text-muted" title={track.first_seen_jalali}>
          {formatJalaliDateTime(track.first_seen)} — {formatRelative(track.first_seen)}
        </p>

        <div className="flex flex-wrap items-center gap-1.5">
          {cameraName ? <Badge tone="muted">{cameraName}</Badge> : null}
          {track.entered_frame ? <Badge tone="ok">ورود ثبت شد</Badge> : null}
          {track.exited_frame ? <Badge tone="info">خروج ثبت شد</Badge> : null}
          {track.crossed_line_id ? <Badge tone="warn">عبور از خط</Badge> : null}
          {track.zone_id ? <Badge tone="muted">داخل ناحیه</Badge> : null}
          {track.status !== "active" ? (
            <Badge tone="muted">ردیابی پایان‌یافته</Badge>
          ) : null}
        </div>
      </div>
    </div>
  );
}

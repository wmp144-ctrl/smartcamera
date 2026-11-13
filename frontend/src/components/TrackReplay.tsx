import {
  ChevronLeft,
  ChevronRight,
  Clock,
  Images,
  Pause,
  Play,
  Route,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { ObjectImage } from "../lib/api";
import { formatJalaliDateTime } from "../lib/jalali";
import { classLabel, eventTypeLabel, faNumber, toFaDigits } from "../lib/utils";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { EmptyState } from "./ui/misc";

/** Normalised box (0..1) drawn over the frame the object belongs to. */
function BoxOverlay({ bbox }: { bbox: number[] }) {
  if (bbox.length !== 4) return null;
  const [x1, y1, x2, y2] = bbox;
  return (
    <svg
      viewBox="0 0 1 1"
      preserveAspectRatio="none"
      className="pointer-events-none absolute inset-0 h-full w-full"
    >
      <rect
        x={x1}
        y={y1}
        width={Math.max(0.001, x2 - x1)}
        height={Math.max(0.001, y2 - y1)}
        fill="rgb(34 211 238 / 0.14)"
        stroke="#22d3ee"
        strokeWidth={2}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/**
 * Frame-by-frame replay of one tracked object.
 *
 * Every stored frame of the track is shown in order (entry, periodic samples,
 * exit) with the object's box drawn on top, so a moving object can be followed
 * end to end instead of only through its single "best" picture.
 */
export function TrackReplay({
  images,
  objectType,
  index,
  onIndexChange,
}: {
  images: ObjectImage[];
  objectType: string;
  /** Controlled frame index so the trajectory map can jump into the player. */
  index: number;
  onIndexChange: (index: number) => void;
}) {
  const frames = useMemo(
    () =>
      [...images].sort(
        (a, b) => new Date(a.captured_at).getTime() - new Date(b.captured_at).getTime(),
      ),
    [images],
  );
  const [playing, setPlaying] = useState(false);
  const setIndex = (value: number | ((current: number) => number)) => {
    const next = typeof value === "function" ? value(index) : value;
    onIndexChange(Math.max(0, Math.min(frames.length - 1, next)));
  };

  useEffect(() => {
    if (!playing || frames.length <= 1) return;
    const timer = window.setInterval(() => {
      if (index + 1 >= frames.length) {
        setPlaying(false);
        return;
      }
      onIndexChange(index + 1);
    }, 650);
    return () => window.clearInterval(timer);
  }, [playing, frames.length, index, onIndexChange]);

  if (frames.length === 0) {
    return <EmptyState icon={<Images />} title="فریمی برای این شیء ثبت نشده است" />;
  }

  const safeIndex = Math.max(0, Math.min(index, frames.length - 1));
  const frame = frames[safeIndex];
  const url = frame.full_url ?? frame.crop_url ?? "";

  return (
    <div className="space-y-3">
      <div className="relative overflow-hidden rounded-xl border border-border bg-[#050a14]">
        <img src={url} alt={`فریم ${safeIndex + 1}`} className="h-auto w-full object-contain" />
        <BoxOverlay bbox={frame.bbox} />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-2 bg-gradient-to-t from-[#020617]/90 to-transparent px-3 pb-2 pt-8 text-[11px]">
          <Badge tone={frame.is_best ? "brand" : "muted"}>
            {frame.is_best ? "بهترین تصویر" : eventTypeLabel(frame.kind)}
          </Badge>
          <span className="text-muted">
            {formatJalaliDateTime(frame.captured_at)} — کیفیت{" "}
            <span className="num">{faNumber(frame.quality_score * 100, 0)}٪</span>
          </span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant={playing ? "primary" : "outline"}
          onClick={() => setPlaying((value) => !value)}
          disabled={frames.length <= 1}
        >
          {playing ? <Pause /> : <Play />}
          {playing ? "توقف" : "پخش"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setIndex((current) => Math.max(0, current - 1))}
          disabled={safeIndex === 0}
        >
          <ChevronRight />
          قبلی
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setIndex((current) => Math.min(frames.length - 1, current + 1))}
          disabled={safeIndex >= frames.length - 1}
        >
          <ChevronLeft />
          بعدی
        </Button>
        <input
          type="range"
          min={0}
          max={Math.max(0, frames.length - 1)}
          step={1}
          value={safeIndex}
          onChange={(event) => {
            setPlaying(false);
            setIndex(Number(event.target.value));
          }}
          className="mx-1 h-1.5 min-w-24 flex-1 accent-[#22d3ee]"
          aria-label="انتخاب فریم"
        />
        <Badge tone="muted">
          <Clock className="size-3" />
          فریم <span className="num">{toFaDigits(safeIndex + 1)}</span> از{" "}
          <span className="num">{toFaDigits(frames.length)}</span>
        </Badge>
      </div>

      <div className="flex gap-2 overflow-x-auto pb-1">
        {frames.map((item, itemIndex) => (
          <button
            key={item.id}
            type="button"
            onClick={() => {
              setPlaying(false);
              setIndex(itemIndex);
            }}
            className={`relative h-16 w-28 shrink-0 overflow-hidden rounded-lg border transition-colors ${
              itemIndex === safeIndex ? "border-brand" : "border-border hover:border-brand/50"
            }`}
            title={`${classLabel(objectType)} — ${formatJalaliDateTime(item.captured_at)}`}
          >
            <img
              src={item.thumb_url ?? item.full_url ?? item.crop_url ?? ""}
              alt={`فریم ${itemIndex + 1}`}
              className="h-full w-full object-cover"
              loading="lazy"
            />
            <span className="num absolute bottom-0.5 left-1 rounded bg-[#020617]/80 px-1 text-[10px]">
              {toFaDigits(itemIndex + 1)}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * The object's trajectory drawn on the camera snapshot.
 *
 * ``path`` holds ``[offset_seconds, cx, cy]`` samples in normalised coordinates;
 * clicking a sample jumps the replay to the closest stored frame.
 */
export function TrackPathMap({
  path,
  backgroundUrl,
  images,
  objectType,
  onSelectFrame,
}: {
  path: number[][];
  backgroundUrl?: string | null;
  images: ObjectImage[];
  objectType: string;
  onSelectFrame?: (index: number) => void;
}) {
  if (path.length < 2) {
    return (
      <EmptyState
        icon={<Route />}
        title="مسیر حرکت ثبت نشده است"
        description="برای اشیای ثابت یا ردیابی‌های قدیمی، مسیر نقطه‌به‌نقطه ذخیره نشده است."
      />
    );
  }

  const span = Math.max(1e-6, path[path.length - 1][0]);
  const points = path.map(([, x, y]) => `${x},${y}`).join(" ");
  const start = path[0];
  const end = path[path.length - 1];

  const jump = (offset: number) => {
    if (!onSelectFrame || images.length <= 1) return;
    const first = new Date(images[0].captured_at).getTime();
    const last = new Date(images[images.length - 1].captured_at).getTime();
    const target = first + (offset / span) * (last - first);
    let best = 0;
    let bestDelta = Number.POSITIVE_INFINITY;
    images.forEach((image, index) => {
      const delta = Math.abs(new Date(image.captured_at).getTime() - target);
      if (delta < bestDelta) {
        bestDelta = delta;
        best = index;
      }
    });
    onSelectFrame(best);
  };

  return (
    <div className="space-y-2">
      <div className="relative overflow-hidden rounded-xl border border-border bg-[#050a14]">
        {backgroundUrl ? (
          <img src={backgroundUrl} alt="مسیر حرکت" className="h-auto w-full object-contain" />
        ) : (
          <div className="aspect-video w-full" />
        )}
        <svg
          viewBox="0 0 1 1"
          preserveAspectRatio="none"
          className="absolute inset-0 h-full w-full"
        >
          <polyline
            points={points}
            fill="none"
            stroke="#22d3ee"
            strokeWidth={2.5}
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
            opacity={0.9}
          />
        </svg>
        {path.map(([offset, x, y], index) => (
          <button
            key={`${offset}-${index}`}
            type="button"
            onClick={() => jump(offset)}
            title={`نقطه ${index + 1} — ${faNumber(offset, 1)} ثانیه`}
            className="absolute size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[#04202a] bg-[#22d3ee] transition-transform hover:scale-150"
            style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
          />
        ))}
        <span
          className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#22d3ee]/25 p-2 ring-1 ring-[#22d3ee]"
          style={{ left: `${start[1] * 100}%`, top: `${start[2] * 100}%` }}
          title="نقطه شروع"
        />
        <span
          className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#f43f5e]/30 p-2 ring-1 ring-[#f43f5e]"
          style={{ left: `${end[1] * 100}%`, top: `${end[2] * 100}%` }}
          title="نقطه پایان"
        />
      </div>
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
        <Badge tone="muted">
          <Route className="size-3" />
          <span className="num">{toFaDigits(path.length)}</span> نقطه مسیر
        </Badge>
        <span>شروع: {classLabel(objectType)}</span>
        <span className="text-muted">•</span>
        <span>
          مدت پیموده‌شده: <span className="num">{faNumber(span, 1)}</span> ثانیه
        </span>
      </div>
    </div>
  );
}

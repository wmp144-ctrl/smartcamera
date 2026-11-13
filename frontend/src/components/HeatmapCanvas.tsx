import { useEffect, useRef } from "react";
import { api } from "../lib/api";
import { useHeatmap } from "../lib/queries";
import { cn, faNumber, toFaDigits } from "../lib/utils";
import { Badge } from "./ui/badge";
import { LoadingBlock } from "./ui/misc";

/** Blue → cyan → yellow → red ramp for density. */
function densityColor(ratio: number): [number, number, number, number] {
  const stops: [number, [number, number, number, number]][] = [
    [0.0, [34, 211, 238, 0]],
    [0.15, [34, 211, 238, 60]],
    [0.4, [250, 204, 21, 120]],
    [0.7, [249, 115, 22, 170]],
    [1.0, [244, 63, 94, 215]],
  ];
  for (let index = 1; index < stops.length; index += 1) {
    const [upper, upperColor] = stops[index];
    const [lower, lowerColor] = stops[index - 1];
    if (ratio <= upper) {
      const span = upper - lower || 1;
      const t = (ratio - lower) / span;
      return [
        Math.round(lowerColor[0] + (upperColor[0] - lowerColor[0]) * t),
        Math.round(lowerColor[1] + (upperColor[1] - lowerColor[1]) * t),
        Math.round(lowerColor[2] + (upperColor[2] - lowerColor[2]) * t),
        Math.round(lowerColor[3] + (upperColor[3] - lowerColor[3]) * t),
      ];
    }
  }
  return stops[stops.length - 1][1];
}

/** Alpha of the hottest stop in :func:`densityColor`. */
const PEAK_ALPHA = 215;

export function HeatmapCanvas({
  cameraId,
  day,
  objectType,
  daysBack,
  gridW,
  gridH,
  opacity = 1,
  hourFrom,
  hourTo,
  aspectRatio = 16 / 9,
  className,
}: {
  cameraId: number;
  day?: string;
  objectType?: string;
  daysBack?: number;
  /** Requested grid resolution; the server resamples its stored grid. */
  gridW?: number;
  gridH?: number;
  /** 0..1 multiplier for the cell alpha. */
  opacity?: number;
  hourFrom?: number | null;
  hourTo?: number | null;
  aspectRatio?: number;
  className?: string;
}) {
  const query = useHeatmap(cameraId, {
    day,
    object_type: objectType,
    days_back: daysBack,
    grid_w: gridW,
    grid_h: gridH,
    hour_from: hourFrom ?? undefined,
    hour_to: hourTo ?? undefined,
  });
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const element = canvas.current;
    const data = query.data;
    if (!element || !data) return;

    const context = element.getContext("2d");
    if (!context) return;

    const width = element.width;
    const height = element.height;
    context.clearRect(0, 0, width, height);

    const cellW = width / data.grid_w;
    const cellH = height / data.grid_h;
    const max = data.max_count || 1;

    for (let row = 0; row < data.grid_h; row += 1) {
      for (let column = 0; column < data.grid_w; column += 1) {
        const count = data.cells[row * data.grid_w + column] ?? 0;
        if (count <= 0) continue;
        const [r, g, b, a] = densityColor(count / max);
        // ``opacity`` is the transparency dial: scaled so that at 1 the hottest
        // cells are painted at full alpha (the snapshot no longer shows through)
        // and at 0 the layer disappears.
        const alpha = Math.min(1, (a / PEAK_ALPHA) * opacity);
        if (alpha <= 0.002) continue;
        context.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
        context.fillRect(column * cellW, row * cellH, cellW + 0.6, cellH + 0.6);
      }
    }
  }, [query.data, opacity]);

  return (
    <div className={cn("space-y-2", className)}>
      <div
        className="relative w-full overflow-hidden rounded-xl border border-border bg-[#050a14]"
        style={{ aspectRatio }}
      >
        <img
          src={api.cameras.snapshotUrl(cameraId, false)}
          alt="نقشه حرارتی"
          className="absolute inset-0 h-full w-full"
        />
        <canvas ref={canvas} width={640} height={360} className="absolute inset-0 h-full w-full" />
      </div>

      {query.isLoading ? (
        <LoadingBlock label="در حال محاسبه نقشه حرارتی…" />
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted">
          <div className="flex items-center gap-2">
            <Badge tone="muted">
              مجموع <span className="num">{toFaDigits(query.data?.total ?? 0)}</span> شیء
            </Badge>
            <Badge tone="muted">
              شبکه {toFaDigits(query.data?.grid_w ?? 0)}×{toFaDigits(query.data?.grid_h ?? 0)}
            </Badge>
            {query.data?.jalali ? <Badge tone="brand">{query.data.jalali}</Badge> : null}
            {query.data?.hour_from != null && query.data?.hour_to != null ? (
              <Badge tone="muted">
                ساعت <span className="num">{toFaDigits(query.data.hour_from)}</span> تا
                <span className="num">{toFaDigits(query.data.hour_to)}</span>
              </Badge>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <span>کم</span>
            <span className="h-2 w-24 rounded-full bg-gradient-to-l from-[rgb(34,211,238)] via-[rgb(250,204,21)] to-[rgb(244,63,94)]" />
            <span>زیاد</span>
            <span className="num">بیشینه {faNumber(query.data?.max_count ?? 0)}</span>
          </div>
        </div>
      )}
    </div>
  );
}

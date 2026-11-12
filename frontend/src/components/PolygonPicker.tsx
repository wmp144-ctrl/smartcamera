import { Eraser, MousePointerClick, Undo2, VectorPolygon, X } from "lucide-react";
import { useCallback, useRef, useState, type MouseEvent } from "react";
import { api } from "../lib/api";
import { cn, toFaDigits } from "../lib/utils";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

export interface PolygonPickerProps {
  cameraId: number | null;
  aspectRatio?: number;
  points: [number, number][];
  onChange: (points: [number, number][]) => void;
  className?: string;
  maxPoints?: number;
}

/**
 * Click-to-draw polygon over the live snapshot of a camera.
 * Points are normalised (0..1) so they can be sent straight to the API.
 */
export function PolygonPicker({
  cameraId,
  aspectRatio = 16 / 9,
  points,
  onChange,
  className,
  maxPoints = 30,
}: PolygonPickerProps) {
  const [drawing, setDrawing] = useState(true);
  const [revision, setRevision] = useState(0);
  const surface = useRef<HTMLDivElement>(null);

  const addPoint = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      if (!drawing || cameraId === null) return;
      const host = surface.current;
      if (!host) return;
      const rect = host.getBoundingClientRect();
      const x = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      const y = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
      if (points.length >= maxPoints) return;
      onChange([...points, [Number(x.toFixed(4)), Number(y.toFixed(4))]]);
    },
    [cameraId, drawing, maxPoints, onChange, points],
  );

  const path = points.map((point) => `${point[0]},${point[1]}`).join(" ");

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant={drawing ? "primary" : "outline"} onClick={() => setDrawing((value) => !value)}>
          <MousePointerClick />
          {drawing ? "حالت رسم فعال" : "حالت رسم غیرفعال"}
        </Button>
        <Button size="sm" variant="outline" disabled={points.length === 0} onClick={() => onChange(points.slice(0, -1))}>
          <Undo2 />
          یک نقطه عقب
        </Button>
        <Button size="sm" variant="ghost" disabled={points.length === 0} onClick={() => onChange([])}>
          <Eraser />
          پاک کردن
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setRevision((value) => value + 1)}>
          تازه‌سازی تصویر
        </Button>
        <Badge tone={points.length >= 3 ? "ok" : "muted"}>
          <VectorPolygon className="size-3" />
          <span className="num">{toFaDigits(points.length)}</span> نقطه
          {points.length < 3 ? " — حداقل ۳ نقطه لازم است" : ""}
        </Badge>
        {points.length > 0 ? (
          <button
            type="button"
            onClick={() => onChange([])}
            className="inline-flex items-center gap-1 text-[11px] text-muted hover:text-danger"
          >
            <X className="size-3" />
            حذف ناحیه
          </button>
        ) : null}
      </div>

      <div
        ref={surface}
        onClick={addPoint}
        className={cn(
          "relative w-full overflow-hidden rounded-xl border border-border bg-[#050a14] select-none",
          drawing ? "cursor-crosshair" : "cursor-default",
        )}
        style={{ aspectRatio }}
      >
        {cameraId !== null ? (
          <img
            key={revision}
            src={api.cameras.snapshotUrl(cameraId, false)}
            alt="تصویر دوربین برای رسم ناحیه"
            className="pointer-events-none absolute inset-0 h-full w-full"
            draggable={false}
          />
        ) : (
          <div className="absolute inset-0 grid place-items-center text-[11px] text-muted">
            ابتدا یک دوربین را انتخاب کنید
          </div>
        )}

        <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 h-full w-full">
          {points.length >= 3 ? (
            <polygon
              points={path}
              fill="rgb(34 211 238 / 0.18)"
              stroke="#22d3ee"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          ) : points.length >= 2 ? (
            <polyline
              points={path}
              fill="none"
              stroke="#22d3ee"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          ) : null}

          {points.map((point, index) => (
            <g key={`${point[0]}-${point[1]}-${index}`}>
              <circle
                cx={point[0]}
                cy={point[1]}
                r={5}
                fill="#22d3ee"
                stroke="#04202a"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
              <text
                x={point[0]}
                y={point[1]}
                dx={6}
                dy={-6}
                fill="#e8eefb"
                fontSize={11}
                style={{ fontSize: 11 }}
              >
                {index + 1}
              </text>
            </g>
          ))}
        </svg>
      </div>

      <p className="text-[11px] leading-6 text-muted">
        روی تصویر کلیک کنید تا نقاط ناحیه مشخص شوند. جست‌وجو اشیایی را برمی‌گرداند که مرکز آن‌ها داخل این ناحیه
        قرار دارد.
      </p>
    </div>
  );
}

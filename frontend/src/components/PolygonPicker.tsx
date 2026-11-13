import { Eraser, MousePointerClick, Undo2, VectorPolygon, X } from "lucide-react";
import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
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
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const surface = useRef<HTMLDivElement>(null);
  const dragStart = useRef<{ x0: number; y0: number } | null>(null);

  /** Client coordinates -> normalised (0..1) point inside the image. */
  const toPoint = (clientX: number, clientY: number): [number, number] | null => {
    const host = surface.current;
    if (!host) return null;
    const rect = host.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    const x = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
    return [Number(x.toFixed(4)), Number(y.toFixed(4))];
  };

  // Dragging paints a rectangle, tapping adds one polygon vertex. ``preventDefault``
  // in pointerdown is essential: without it the browser starts a text selection on
  // the surrounding card, which paints the whole image blue and makes drawing
  // impossible.
  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drawing || cameraId === null) return;
    const point = toPoint(event.clientX, event.clientY);
    if (!point) return;
    event.preventDefault();
    dragStart.current = { x0: point[0], y0: point[1] };
    setDrag({ x0: point[0], y0: point[1], x1: point[0], y1: point[1] });
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      // Synthetic or already-released pointers cannot be captured; the drag
      // still works while the pointer stays over the image.
    }
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = dragStart.current;
    if (!start) return;
    const point = toPoint(event.clientX, event.clientY);
    if (!point) return;
    setDrag({ ...start, x1: point[0], y1: point[1] });
  };

  const endDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = dragStart.current;
    dragStart.current = null;
    setDrag(null);
    if (!start) return;
    const point = toPoint(event.clientX, event.clientY);
    if (!point) return;
    const [x1, y1] = point;
    const width = Math.abs(x1 - start.x0);
    const height = Math.abs(y1 - start.y0);
    if (width > 0.01 && height > 0.01) {
      // A real drag: the four corners of the rectangle, clockwise.
      const left = Math.min(start.x0, x1);
      const right = Math.max(start.x0, x1);
      const top = Math.min(start.y0, y1);
      const bottom = Math.max(start.y0, y1);
      onChange([
        [left, top],
        [right, top],
        [right, bottom],
        [left, bottom],
      ]);
      return;
    }
    if (points.length >= maxPoints) return;
    onChange([...points, point]);
  };

  const path = points.map((point) => `${point[0]},${point[1]}`).join(" ");
  const dragRect = drag
    ? {
        x: Math.min(drag.x0, drag.x1),
        y: Math.min(drag.y0, drag.y1),
        width: Math.abs(drag.x1 - drag.x0),
        height: Math.abs(drag.y1 - drag.y0),
      }
    : null;

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
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onDragStart={(event) => event.preventDefault()}
        className={cn(
          "relative w-full overflow-hidden rounded-xl border border-border bg-[#050a14] select-none",
          drawing ? "cursor-crosshair" : "cursor-default",
        )}
        style={{ aspectRatio, touchAction: "none" }}
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
          {dragRect && (dragRect.width > 0.002 || dragRect.height > 0.002) ? (
            <rect
              x={dragRect.x}
              y={dragRect.y}
              width={dragRect.width}
              height={dragRect.height}
              fill="rgb(34 211 238 / 0.12)"
              stroke="#22d3ee"
              strokeDasharray="6 4"
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
            />
          ) : null}

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
        روی تصویر بکشید تا یک محدودهٔ مستطیلی رسم شود؛ برای ناحیه دلخواه، نقطه‌به‌نقطه کلیک کنید (حداقل ۳ نقطه).
        جست‌وجو اشیایی را برمی‌گرداند که مرکز آن‌ها داخل این ناحیه قرار دارد.
      </p>
    </div>
  );
}

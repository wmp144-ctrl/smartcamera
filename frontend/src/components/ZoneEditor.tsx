import { Eraser, MousePointer2, Pencil, Route, Save, Trash, Undo2, VectorPolygon, X } from "lucide-react";
import { useCallback, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { api, type Camera, type Zone } from "../lib/api";
import { useDeleteZone, useSaveZone } from "../lib/queries";
import { CLASS_LABELS_FA, cn, classLabel, toFaDigits } from "../lib/utils";
import { toast } from "./ui/toast";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox, Field, Input, Select } from "./ui/field";
import { EmptyState } from "./ui/misc";

type DrawMode = "idle" | "polygon" | "line";

const KIND_LABELS: Record<Zone["kind"], string> = {
  region: "ناحیه پردازش",
  ignore: "ناحیه نادیده‌گرفتن",
  line: "خط عبور (تریپ‌وایر)",
  alert: "ناحیه هشدار",
};

const KIND_TONE: Record<Zone["kind"], "ok" | "muted" | "warn" | "danger"> = {
  region: "ok",
  ignore: "muted",
  line: "warn",
  alert: "danger",
};

const POINT_LIMIT = 40;

export interface ZoneEditorProps {
  camera: Camera;
  aspectRatio: number;
  className?: string;
}

export function ZoneEditor({ camera, aspectRatio, className }: ZoneEditorProps) {
  const [mode, setMode] = useState<DrawMode>("idle");
  const [draft, setDraft] = useState<[number, number][]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Zone["kind"]>("region");
  const [alertEnabled, setAlertEnabled] = useState(false);
  const [alertSound, setAlertSound] = useState(true);
  const [alertClasses, setAlertClasses] = useState<string[]>([]);
  const [visible, setVisible] = useState(true);
  const [snapshotKey, setSnapshotKey] = useState(0);
  const surface = useRef<HTMLDivElement>(null);

  const saveZone = useSaveZone();
  const deleteZone = useDeleteZone();
  const zones = camera.zones ?? [];
  const classKeys = useMemo(() => Object.keys(camera.class_config ?? {}), [camera.class_config]);

  const startDrawing = (next: DrawMode) => {
    setMode(next);
    setDraft([]);
    setEditingId(null);
    setKind(next === "line" ? "line" : "region");
    setAlertEnabled(false);
    setName(next === "line" ? "خط عبور" : "ناحیه پردازش");
    setSnapshotKey((value) => value + 1);
  };

  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const dragStart = useRef<{ x0: number; y0: number } | null>(null);

  const toPoint = (clientX: number, clientY: number): [number, number] | null => {
    const host = surface.current;
    if (!host) return null;
    const rect = host.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    const x = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
    return [Number(x.toFixed(4)), Number(y.toFixed(4))];
  };

  const appendPoint = useCallback(
    (point: [number, number]) => {
      setDraft((points) => {
        if (points.length >= POINT_LIMIT) return points;
        const next: [number, number][] = [...points, point];
        if (mode === "line" && next.length === 2) setMode("idle");
        return next;
      });
    },
    [mode],
  );

  // Drawing must not start a text selection on the card: the browser would paint
  // the whole image with the ::selection colour and nothing could be drawn.
  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (mode === "idle") return;
    const point = toPoint(event.clientX, event.clientY);
    if (!point) return;
    event.preventDefault();
    dragStart.current = { x0: point[0], y0: point[1] };
    setDrag({ x0: point[0], y0: point[1], x1: point[0], y1: point[1] });
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      // Synthetic or already-released pointers cannot be captured.
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
    const moved = Math.abs(x1 - start.x0) > 0.01 && Math.abs(y1 - start.y0) > 0.01;

    if (mode === "line") {
      // A drag is the whole tripwire; a tap starts it point by point.
      if (moved) {
        setDraft([[start.x0, start.y0], point]);
        setMode("idle");
        return;
      }
      appendPoint(point);
      return;
    }

    if (moved) {
      const left = Math.min(start.x0, x1);
      const right = Math.max(start.x0, x1);
      const top = Math.min(start.y0, y1);
      const bottom = Math.max(start.y0, y1);
      setDraft([
        [left, top],
        [right, top],
        [right, bottom],
        [left, bottom],
      ]);
      return;
    }
    appendPoint(point);
  };

  const canSave = useMemo(() => {
    if (draft.length === 0) return false;
    if (kind === "line") return draft.length === 2;
    return draft.length >= 3;
  }, [draft, kind]);

  const handleSave = async () => {
    if (!canSave) return;
    try {
      await saveZone.mutateAsync({
        cameraId: camera.id,
        zoneId: editingId ?? undefined,
        payload: {
          name: name.trim() || (kind === "line" ? "خط عبور" : "ناحیه"),
          kind,
          points: draft,
          color: kind === "alert" ? "#f43f5e" : kind === "ignore" ? "#8ea3c2" : kind === "line" ? "#f59e0b" : "#22d3ee",
          enabled: true,
          alert_enabled: kind === "line" ? alertEnabled || true : alertEnabled,
          alert_sound: alertSound,
          alert_classes: alertClasses,
        },
      });
      toast.success("ناحیه ذخیره شد", `${zones.length + (editingId ? 0 : 1)} ناحیه روی این دوربین ثبت شده است`);
      setDraft([]);
      setEditingId(null);
      setMode("idle");
    } catch (error) {
      toast.error("ذخیره ناحیه ناموفق بود", (error as Error).message);
    }
  };

  const handleDelete = async (zoneId: number) => {
    try {
      await deleteZone.mutateAsync({ cameraId: camera.id, zoneId });
      toast.success("ناحیه حذف شد");
    } catch (error) {
      toast.error("حذف ناحیه ناموفق بود", (error as Error).message);
    }
  };

  const loadZone = (zone: Zone) => {
    setEditingId(zone.id);
    setDraft(zone.points.map((point) => [point[0], point[1]] as [number, number]));
    setName(zone.name);
    setKind(zone.kind);
    setAlertEnabled(zone.alert_enabled);
    setAlertSound(zone.alert_sound);
    setAlertClasses(zone.alert_classes ?? []);
    setMode("idle");
  };

  const draftPath = draft.map((point) => `${point[0]},${point[1]}`).join(" ");

  return (
    <div className={cn("grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]", className)}>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant={mode === "polygon" ? "primary" : "outline"}
            onClick={() => startDrawing("polygon")}
          >
            <VectorPolygon />
            رسم ناحیه
          </Button>
          <Button size="sm" variant={mode === "line" ? "warn" : "outline"} onClick={() => startDrawing("line")}>
            <Route />
            رسم خط عبور
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setDraft((points) => points.slice(0, -1))}
            disabled={draft.length === 0}
          >
            <Undo2 />
            یک نقطه عقب
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setDraft([])} disabled={draft.length === 0}>
            <Eraser />
            پاک کردن رسم
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setVisible((value) => !value)}>
            <MousePointer2 />
            {visible ? "مخفی کردن نواحی" : "نمایش نواحی"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSnapshotKey((value) => value + 1)}>
            <Pencil />
            تازه‌سازی تصویر
          </Button>
          {mode !== "idle" ? (
            <Badge tone={mode === "line" ? "warn" : "brand"}>
              {mode === "line"
                ? "حالت رسم فعال — برای خط، روی تصویر بکشید یا دو نقطه کلیک کنید"
                : "حالت رسم فعال — بکشید تا مستطیل رسم شود، یا نقطه‌به‌نقطه کلیک کنید"}
            </Badge>
          ) : null}
        </div>

        <div
          className="relative w-full cursor-crosshair overflow-hidden rounded-xl border border-border bg-[#050a14] select-none"
          style={{ aspectRatio, touchAction: "none" }}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onDragStart={(event) => event.preventDefault()}
          ref={surface}
        >
          <img
            key={snapshotKey}
            src={api.cameras.snapshotUrl(camera.id, false)}
            alt={`تصویر دوربین ${camera.name}`}
            className="pointer-events-none absolute inset-0 h-full w-full"
            draggable={false}
          />

          <svg
            viewBox="0 0 1 1"
            preserveAspectRatio="none"
            className="pointer-events-none absolute inset-0 h-full w-full"
          >
            {visible
              ? zones.map((zone) => {
                  const points = zone.points.map((point) => `${point[0]},${point[1]}`).join(" ");
                  return zone.kind === "line" ? (
                    <line
                      key={zone.id}
                      x1={zone.points[0]?.[0] ?? 0}
                      y1={zone.points[0]?.[1] ?? 0}
                      x2={zone.points[1]?.[0] ?? 0}
                      y2={zone.points[1]?.[1] ?? 0}
                      stroke={zone.color}
                      strokeWidth={3}
                      vectorEffect="non-scaling-stroke"
                      strokeDasharray={zone.enabled ? undefined : "6 4"}
                    />
                  ) : (
                    <polygon
                      key={zone.id}
                      points={points}
                      fill={zone.color}
                      fillOpacity={zone.kind === "ignore" ? 0.08 : 0.16}
                      stroke={zone.color}
                      strokeWidth={2}
                      vectorEffect="non-scaling-stroke"
                      strokeDasharray={zone.enabled ? undefined : "6 4"}
                    />
                  );
                })
              : null}

            {drag && (Math.abs(drag.x1 - drag.x0) > 0.002 || Math.abs(drag.y1 - drag.y0) > 0.002) ? (
              mode === "line" ? (
                <line
                  x1={drag.x0}
                  y1={drag.y0}
                  x2={drag.x1}
                  y2={drag.y1}
                  stroke="#f59e0b"
                  strokeDasharray="6 4"
                  strokeWidth={3}
                  vectorEffect="non-scaling-stroke"
                />
              ) : (
                <rect
                  x={Math.min(drag.x0, drag.x1)}
                  y={Math.min(drag.y0, drag.y1)}
                  width={Math.abs(drag.x1 - drag.x0)}
                  height={Math.abs(drag.y1 - drag.y0)}
                  fill="rgb(34 211 238 / 0.12)"
                  stroke="#22d3ee"
                  strokeDasharray="6 4"
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                />
              )
            ) : null}

            {draft.length > 0 ? (
              kind === "line" && draft.length === 2 ? (
                <line
                  x1={draft[0][0]}
                  y1={draft[0][1]}
                  x2={draft[1][0]}
                  y2={draft[1][1]}
                  stroke="#f59e0b"
                  strokeWidth={3}
                  vectorEffect="non-scaling-stroke"
                />
              ) : (
                <polyline
                  points={draftPath}
                  fill="rgb(34 211 238 / 0.15)"
                  stroke="#22d3ee"
                  strokeWidth={2}
                  vectorEffect="non-scaling-stroke"
                />
              )
            ) : null}

          </svg>

          {/* Vertices are HTML, not SVG shapes: inside a 0..1 viewBox a radius is
              measured in image widths, which painted the picture solid cyan. */}
          {draft.map((point, index) => (
            <span
              key={`${point[0]}-${point[1]}-${index}`}
              className="pointer-events-none absolute"
              style={{ left: `${point[0] * 100}%`, top: `${point[1] * 100}%` }}
            >
              <span className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[#04202a] bg-[#22d3ee] size-2" />
              <span className="num absolute -translate-y-[140%] translate-x-1.5 text-[10px] leading-3 text-[#e8eefb] drop-shadow">
                {index + 1}
              </span>
            </span>
          ))}

          <div className="pointer-events-none absolute bottom-2 right-2 rounded-lg bg-[#020617]/75 px-2 py-1 text-[11px] text-muted backdrop-blur">
            {draft.length === 0
              ? "برای شروع، یک ابزار رسم را انتخاب کنید"
              : `${toFaDigits(draft.length)} نقطه ثبت شده — ${
                  kind === "line" ? "خط به دو نقطه نیاز دارد" : "ناحیه به حداقل سه نقطه نیاز دارد"
                }`}
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <div className="panel p-3">
          <p className="mb-2 text-xs font-semibold">تنظیمات ناحیه جاری</p>
          <div className="space-y-2.5">
            <Field label="نام ناحیه">
              <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="مثلاً ورودی پارکینگ" />
            </Field>
            <Field label="نوع">
              <Select value={kind} onChange={(event) => setKind(event.target.value as Zone["kind"])}>
                {(Object.keys(KIND_LABELS) as Zone["kind"][]).map((value) => (
                  <option key={value} value={value}>
                    {KIND_LABELS[value]}
                  </option>
                ))}
              </Select>
            </Field>

            <Checkbox
              checked={kind === "line" ? true : alertEnabled}
              disabled={kind === "line"}
              onChange={setAlertEnabled}
              label="فعال‌سازی هشدار"
              description={kind === "line" ? "عبور از خط همیشه هشدار ثبت می‌کند" : "با ورود شیء به این ناحیه هشدار ثبت شود"}
            />
            <Checkbox checked={alertSound} onChange={setAlertSound} label="پخش صدا هنگام هشدار" />

            {classKeys.length > 0 ? (
              <div>
                <p className="mb-1.5 text-xs text-muted">کلاس‌های مشمول هشدار (خالی = همه)</p>
                <div className="flex flex-wrap gap-1.5">
                  {classKeys.map((key) => {
                    const active = alertClasses.includes(key);
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() =>
                          setAlertClasses((list) =>
                            active ? list.filter((item) => item !== key) : [...list, key],
                          )
                        }
                        className={cn(
                          "rounded-full border px-2 py-0.5 text-[11px] transition-colors",
                          active
                            ? "border-brand/50 bg-brand/15 text-brand"
                            : "border-border bg-surface-2 text-muted hover:text-fg",
                        )}
                      >
                        {CLASS_LABELS_FA[key] ?? classLabel(key)}
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : null}

            <div className="flex items-center gap-2 pt-1">
              <Button size="sm" variant="primary" onClick={handleSave} disabled={!canSave} loading={saveZone.isPending}>
                <Save />
                {editingId ? "به‌روزرسانی ناحیه" : "ذخیره ناحیه"}
              </Button>
              {editingId || draft.length > 0 ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setDraft([]);
                    setEditingId(null);
                  }}
                >
                  <X />
                  انصراف
                </Button>
              ) : null}
            </div>
            {!canSave && draft.length > 0 ? (
              <p className="text-[11px] text-warn">
                {kind === "line" ? "خط عبور باید دقیقاً دو نقطه داشته باشد" : "ناحیه باید حداقل سه نقطه داشته باشد"}
              </p>
            ) : null}
          </div>
        </div>

        <div className="panel p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-semibold">نواحی این دوربین</p>
            <Badge tone="muted">{toFaDigits(zones.length)}</Badge>
          </div>

          {zones.length === 0 ? (
            <EmptyState
              icon={<VectorPolygon />}
              title="هنوز ناحیه‌ای ثبت نشده"
              description="با ابزار رسم، منطقه‌ای که باید پردازش شود یا خط عبور را مشخص کنید."
            />
          ) : (
            <div className="space-y-2">
              {zones.map((zone) => (
                <div key={zone.id} className="rounded-xl border border-border bg-surface-2/60 p-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-[11px] font-medium">{zone.name}</p>
                      <p className="mt-0.5 text-[11px] text-muted">
                        {KIND_LABELS[zone.kind]} — {toFaDigits(zone.points.length)} نقطه
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <Badge tone={KIND_TONE[zone.kind]}>{zone.enabled ? "فعال" : "غیرفعال"}</Badge>
                      <button
                        type="button"
                        title="ویرایش نقاط"
                        onClick={() => loadZone(zone)}
                        className="grid size-7 place-items-center rounded-lg text-muted hover:bg-surface-3 hover:text-fg"
                      >
                        <Pencil className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        title="حذف ناحیه"
                        onClick={() => handleDelete(zone.id)}
                        className="grid size-7 place-items-center rounded-lg text-muted hover:bg-danger/15 hover:text-danger"
                      >
                        <Trash className="size-3.5" />
                      </button>
                    </div>
                  </div>
                  {zone.alert_enabled ? (
                    <div className="mt-2 flex flex-wrap items-center gap-1">
                      <Badge tone="danger">هشدار فعال</Badge>
                      {zone.alert_sound ? <Badge tone="warn">صدا</Badge> : null}
                      {zone.alert_classes.length > 0 ? (
                        <span className="text-[11px] text-muted">
                          {zone.alert_classes.map((key) => classLabel(key)).join("، ")}
                        </span>
                      ) : (
                        <span className="text-[11px] text-muted">همه کلاس‌ها</span>
                      )}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

import { Plus, Save, ScanEye, Trash } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { Camera, ClassRule } from "../lib/api";
import { useUpdateCamera } from "../lib/queries";
import { CLASS_LABELS_FA, classLabel, cn, faNumber, toFaDigits } from "../lib/utils";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Field, Input, Select, Switch } from "./ui/field";
import { Slider } from "./ui/misc";
import { toast } from "./ui/toast";

/** Subset of the 80 COCO classes RF-DETR can emit — enough for CCTV setups. */
const PICKABLE_CLASSES: string[] = [
  "person",
  "bicycle",
  "car",
  "motorcycle",
  "bus",
  "truck",
  "train",
  "boat",
  "airplane",
  "traffic light",
  "stop sign",
  "bench",
  "dog",
  "cat",
  "bird",
  "horse",
  "sheep",
  "cow",
  "backpack",
  "umbrella",
  "handbag",
  "suitcase",
  "bottle",
  "chair",
  "tv",
  "laptop",
  "cell phone",
];

const DEFAULT_RULE: ClassRule = {
  enabled: true,
  threshold: 0.35,
  min_width: 0,
  min_height: 0,
  min_motion: 0,
};

function normaliseRule(raw: Partial<ClassRule> | undefined): ClassRule {
  return {
    enabled: raw?.enabled ?? DEFAULT_RULE.enabled,
    threshold: raw?.threshold ?? DEFAULT_RULE.threshold,
    min_width: raw?.min_width ?? DEFAULT_RULE.min_width,
    min_height: raw?.min_height ?? DEFAULT_RULE.min_height,
    min_motion: raw?.min_motion ?? DEFAULT_RULE.min_motion,
  };
}

export function DetectionSettings({ camera }: { camera: Camera }) {
  const update = useUpdateCamera();

  const [rules, setRules] = useState<Record<string, ClassRule>>({});
  const [detectionFps, setDetectionFps] = useState(camera.detection_fps);
  const [fps, setFps] = useState(camera.fps);
  const [motionSensitivity, setMotionSensitivity] = useState(camera.motion_sensitivity);
  const [detectionEnabled, setDetectionEnabled] = useState(camera.detection_enabled);
  const [recordObjects, setRecordObjects] = useState(camera.record_objects);
  const [modelName, setModelName] = useState(camera.model_name);
  const [transport, setTransport] = useState(camera.transport);
  const [newClass, setNewClass] = useState("");

  useEffect(() => {
    const next: Record<string, ClassRule> = {};
    for (const [key, value] of Object.entries(camera.class_config ?? {})) {
      next[key] = normaliseRule(value);
    }
    setRules(next);
    setDetectionFps(camera.detection_fps);
    setFps(camera.fps);
    setMotionSensitivity(camera.motion_sensitivity);
    setDetectionEnabled(camera.detection_enabled);
    setRecordObjects(camera.record_objects);
    setModelName(camera.model_name);
    setTransport(camera.transport);
  }, [camera]);

  const configuredKeys = useMemo(() => Object.keys(rules), [rules]);
  const addable = useMemo(
    () => PICKABLE_CLASSES.filter((key) => !configuredKeys.includes(key)),
    [configuredKeys],
  );

  const patchRule = (key: string, patch: Partial<ClassRule>) =>
    setRules((state) => ({ ...state, [key]: { ...normaliseRule(state[key]), ...patch } }));

  const handleSave = async () => {
    try {
      await update.mutateAsync({
        id: camera.id,
        payload: {
          class_config: rules,
          detection_fps: detectionFps,
          fps,
          motion_sensitivity: motionSensitivity,
          detection_enabled: detectionEnabled,
          record_objects: recordObjects,
          model_name: modelName,
          transport,
        },
      });
      toast.success("تنظیمات ذخیره شد", "حساسیت و قواعد کلاس‌ها به‌روزرسانی شد");
    } catch (error) {
      toast.error("ذخیره تنظیمات ناموفق بود", (error as Error).message);
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2">
        <Switch
          checked={detectionEnabled}
          onChange={setDetectionEnabled}
          label="تشخیص اشیا (RF-DETR)"
          description="در حالت خام تصویر پخش می‌شود؛ با فعال بودن این گزینه، اشیا شناسایی و ثبت می‌شوند."
        />
        <Switch
          checked={recordObjects}
          onChange={setRecordObjects}
          label="ذخیره تصویر اشیای شناسایی‌شده"
          description="تصویر کامل صحنه و نسخه برش‌خورده شیء روی دیسک ذخیره می‌شود."
        />
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Field label="نرخ فریم نمایش">
          <Input
            type="number"
            min={1}
            max={60}
            value={fps}
            onChange={(event) => setFps(Number(event.target.value))}
          />
        </Field>
        <Field label="نرخ تشخیص (فریم بر ثانیه)" hint="پیشنهادی: ۵">
          <Input
            type="number"
            min={0.5}
            max={30}
            step={0.5}
            value={detectionFps}
            onChange={(event) => setDetectionFps(Number(event.target.value))}
          />
        </Field>
        <Field label="مدل RF-DETR">
          <Select value={modelName} onChange={(event) => setModelName(event.target.value)}>
            {["rf-detr-nano", "rf-detr-small", "rf-detr-base", "rf-detr-medium", "rf-detr-large"].map(
              (key) => (
                <option key={key} value={key}>
                  {key}
                </option>
              ),
            )}
          </Select>
        </Field>
        <Field label="پروتکل انتقال">
          <Select
            value={transport}
            onChange={(event) => setTransport(event.target.value as "tcp" | "udp")}
          >
            <option value="tcp">TCP (پایدارتر)</option>
            <option value="udp">UDP (کم‌تأخیرتر)</option>
          </Select>
        </Field>
      </div>

      <Slider
        value={motionSensitivity}
        onChange={setMotionSensitivity}
        min={0.0005}
        max={0.05}
        step={0.0005}
        label="حداقل حرکت لازم برای بررسی (حساسیت حرکت)"
        format={(value) => `${faNumber(value * 100, 2)}٪`}
      />

      <div className="panel overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2.5">
          <div className="flex items-center gap-2">
            <ScanEye className="size-4 text-brand" />
            <span className="text-xs font-semibold">قواعد هر کلاس</span>
            <Badge tone="muted">{toFaDigits(configuredKeys.length)} کلاس</Badge>
          </div>
          <div className="flex items-center gap-2">
            <Select
              value={newClass}
              onChange={(event) => {
                const key = event.target.value;
                if (!key) return;
                setRules((state) => ({ ...state, [key]: { ...DEFAULT_RULE } }));
                setNewClass("");
              }}
              className="h-8 w-44 text-[11px]"
            >
              <option value="">افزودن کلاس…</option>
              {addable.map((key) => (
                <option key={key} value={key}>
                  {classLabel(key)}
                </option>
              ))}
            </Select>
          </div>
        </div>

        <div className="max-h-[420px] divide-y divide-border/60 overflow-y-auto">
          {configuredKeys.length === 0 ? (
            <p className="px-4 py-8 text-center text-[11px] text-muted">
              هیچ کلاسی فعال نیست؛ از فهرست بالا کلاس اضافه کنید.
            </p>
          ) : (
            configuredKeys.map((key) => {
              const rule = normaliseRule(rules[key]);
              return (
                <div key={key} className="px-3 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <label className="flex items-center gap-2 text-xs font-medium">
                      <input
                        type="checkbox"
                        checked={rule.enabled}
                        onChange={(event) => patchRule(key, { enabled: event.target.checked })}
                        className="size-4 accent-[var(--brand)]"
                      />
                      {CLASS_LABELS_FA[key] ?? classLabel(key)}
                      <span className="text-[10px] text-muted">({key})</span>
                    </label>
                    <div className="flex items-center gap-2">
                      {!rule.enabled ? <Badge tone="muted">غیرفعال</Badge> : null}
                      <button
                        type="button"
                        onClick={() =>
                          setRules((state) => {
                            const next = { ...state };
                            delete next[key];
                            return next;
                          })
                        }
                        className={cn(
                          "grid size-7 place-items-center rounded-lg text-muted hover:bg-danger/15 hover:text-danger",
                        )}
                        title="حذف کلاس"
                      >
                        <Trash className="size-3.5" />
                      </button>
                    </div>
                  </div>

                  <div className={cn("mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4", !rule.enabled && "opacity-50")}>
                    <Slider
                      value={rule.threshold}
                      onChange={(value) => patchRule(key, { threshold: value })}
                      min={0.05}
                      max={0.95}
                      step={0.01}
                      label="حد آستانه اطمینان"
                      format={(value) => `${faNumber(value * 100, 0)}٪`}
                    />
                    <Slider
                      value={rule.min_width}
                      onChange={(value) => patchRule(key, { min_width: value })}
                      max={0.5}
                      step={0.005}
                      label="حداقل عرض (نسبت به کادر)"
                      format={(value) => `${faNumber(value * 100, 1)}٪`}
                    />
                    <Slider
                      value={rule.min_height}
                      onChange={(value) => patchRule(key, { min_height: value })}
                      max={0.5}
                      step={0.005}
                      label="حداقل ارتفاع (نسبت به کادر)"
                      format={(value) => `${faNumber(value * 100, 1)}٪`}
                    />
                    <Slider
                      value={rule.min_motion}
                      onChange={(value) => patchRule(key, { min_motion: value })}
                      max={0.2}
                      step={0.002}
                      label="حداقل جابه‌جایی"
                      format={(value) => `${faNumber(value * 100, 2)}٪`}
                    />
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-border px-3 py-2.5">
          <span className="text-[10px] text-muted">
            <Plus className="inline size-3" /> قواعد روی پردازش زنده همین دوربین اعمال می‌شود.
          </span>
          <Button size="sm" variant="primary" onClick={handleSave} loading={update.isPending}>
            <Save />
            ذخیره تنظیمات
          </Button>
        </div>
      </div>
    </div>
  );
}

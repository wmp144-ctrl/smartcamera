import {
  Camera as CameraIcon,
  CheckCheck,
  Pencil,
  Plug,
  Plus,
  RefreshCw,
  ScanEye,
  Search,
  Trash,
  Video,
  VideoOff,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CameraTile } from "../components/CameraTile";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, PageHeader } from "../components/ui/card";
import { ConfirmDialog, Dialog } from "../components/ui/dialog";
import { Field, Input, Select, Textarea } from "../components/ui/field";
import { EmptyState, LoadingBlock, Slider } from "../components/ui/misc";
import { toast } from "../components/ui/toast";
import type { Camera, CameraCreatePayload } from "../lib/api";
import {
  useCameras,
  useCameraStatuses,
  useCreateCamera,
  useDeleteCamera,
  useReloadCamera,
  useTestCamera,
  useToggleDetection,
  useUpdateCamera,
} from "../lib/queries";
import { cameraStateLabel, faNumber, formatRelative, stateTone, toFaDigits } from "../lib/utils";

interface FormState {
  name: string;
  location: string;
  rtsp_url: string;
  transport: "tcp" | "udp";
  fps: number;
  detection_fps: number;
  model_name: string;
  detection_enabled: boolean;
  motion_sensitivity: number;
  record_objects: boolean;
  record_snapshots: boolean;
  notes: string;
  enabled: boolean;
}

const EMPTY_FORM: FormState = {
  name: "",
  location: "",
  rtsp_url: "",
  transport: "tcp",
  fps: 20,
  detection_fps: 5,
  model_name: "rf-detr-base",
  detection_enabled: false,
  motion_sensitivity: 0.004,
  record_objects: true,
  record_snapshots: true,
  notes: "",
  enabled: true,
};

function toForm(camera: Camera): FormState {
  return {
    name: camera.name,
    location: camera.location ?? "",
    rtsp_url: camera.rtsp_url,
    transport: camera.transport,
    fps: camera.fps,
    detection_fps: camera.detection_fps,
    model_name: camera.model_name,
    detection_enabled: camera.detection_enabled,
    motion_sensitivity: camera.motion_sensitivity,
    record_objects: camera.record_objects,
    record_snapshots: camera.record_snapshots,
    notes: camera.notes ?? "",
    enabled: camera.enabled,
  };
}

function CameraFormDialog({
  open,
  onClose,
  camera,
}: {
  open: boolean;
  onClose: () => void;
  camera: Camera | null;
}) {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);

  const create = useCreateCamera();
  const update = useUpdateCamera();
  const test = useTestCamera();

  useEffect(() => {
    if (!open) return;
    setForm(camera ? toForm(camera) : EMPTY_FORM);
    setError(null);
    setTestResult(null);
  }, [open, camera]);

  const patch = (values: Partial<FormState>) => setForm((state) => ({ ...state, ...values }));

  const handleSubmit = async () => {
    if (!form.name.trim() || !form.rtsp_url.trim()) {
      setError("نام و آدرس RTSP الزامی است");
      return;
    }
    const payload: CameraCreatePayload = {
      name: form.name.trim(),
      location: form.location.trim() || null,
      rtsp_url: form.rtsp_url.trim(),
      enabled: form.enabled,
      transport: form.transport,
      fps: form.fps,
      detection_fps: form.detection_fps,
      model_name: form.model_name,
      detection_enabled: form.detection_enabled,
      motion_sensitivity: form.motion_sensitivity,
      record_objects: form.record_objects,
      record_snapshots: form.record_snapshots,
      notes: form.notes.trim() || null,
    };
    try {
      if (camera) {
        await update.mutateAsync({ id: camera.id, payload });
        toast.success("دوربین به‌روزرسانی شد", form.name);
      } else {
        await create.mutateAsync(payload);
        toast.success("دوربین افزوده شد", form.name);
      }
      onClose();
    } catch (requestError) {
      const message = (requestError as Error).message;
      setError(message);
      toast.error("ذخیره دوربین ناموفق بود", message);
    }
  };

  const handleTest = async () => {
    if (camera) {
      const result = await test.mutateAsync(camera.id);
      setTestResult(
        result.ok
          ? `${result.message} — ${toFaDigits(result.width ?? 0)}×${toFaDigits(result.height ?? 0)} @ ${faNumber(
              result.fps ?? 0,
              1,
            )} fps`
          : result.message,
      );
      return;
    }
    setTestResult("برای آزمایش اتصال، ابتدا دوربین را ذخیره کنید");
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      title={camera ? `ویرایش دوربین «${camera.name}»` : "افزودن دوربین جدید"}
      description="آدرس RTSP و تنظیمات پایه دوربین را وارد کنید. تنظیمات پیشرفته در صفحه اختصاصی دوربین قابل تغییر است."
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            انصراف
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleTest}
            loading={test.isPending}
          >
            <Plug />
            آزمایش اتصال
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={handleSubmit}
            loading={create.isPending || update.isPending}
          >
            <CheckCheck />
            {camera ? "ذخیره تغییرات" : "افزودن دوربین"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error ? (
          <p className="rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-[11px] text-danger">{error}</p>
        ) : null}
        {testResult ? (
          <p className="rounded-xl border border-info/40 bg-info/10 px-3 py-2 text-[11px] text-info">{testResult}</p>
        ) : null}

        <div className="grid gap-3 md:grid-cols-2">
          <Field label="نام دوربین" required>
            <Input
              value={form.name}
              onChange={(event) => patch({ name: event.target.value })}
              placeholder="مثلاً دوربین ورودی اصلی"
            />
          </Field>
          <Field label="موقعیت مکانی">
            <Input
              value={form.location}
              onChange={(event) => patch({ location: event.target.value })}
              placeholder="مثلاً پارکینگ طبقه منفی یک"
            />
          </Field>
        </div>

        <Field label="آدرس RTSP" required hint="rtsp://, rtsps://, http(s):// یا file://">
          <Input
            dir="ltr"
            value={form.rtsp_url}
            onChange={(event) => patch({ rtsp_url: event.target.value })}
            placeholder="rtsp://user:pass@192.168.1.10:554/Streaming/Channels/101"
            className="text-left"
          />
        </Field>

        <div className="grid gap-3 md:grid-cols-3">
          <Field label="پروتکل انتقال">
            <Select
              value={form.transport}
              onChange={(event) => patch({ transport: event.target.value as "tcp" | "udp" })}
            >
              <option value="tcp">TCP</option>
              <option value="udp">UDP</option>
            </Select>
          </Field>
          <Field label="نرخ فریم نمایش">
            <Input
              type="number"
              min={1}
              max={60}
              value={form.fps}
              onChange={(event) => patch({ fps: Number(event.target.value) })}
            />
          </Field>
          <Field label="نرخ تشخیص">
            <Input
              type="number"
              min={0.5}
              max={30}
              step={0.5}
              value={form.detection_fps}
              onChange={(event) => patch({ detection_fps: Number(event.target.value) })}
            />
          </Field>
        </div>

        <Field label="مدل RF-DETR">
          <Select value={form.model_name} onChange={(event) => patch({ model_name: event.target.value })}>
            {["rf-detr-nano", "rf-detr-small", "rf-detr-base", "rf-detr-medium", "rf-detr-large"].map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </Select>
        </Field>

        <Slider
          value={form.motion_sensitivity}
          onChange={(value) => patch({ motion_sensitivity: value })}
          min={0.0005}
          max={0.05}
          step={0.0005}
          label="حداقل حرکت لازم برای پردازش"
          format={(value) => `${faNumber(value * 100, 2)}٪`}
        />

        <div className="grid gap-2 md:grid-cols-2">
          <label className="flex items-center gap-2 rounded-xl border border-border bg-surface-2/60 p-2.5 text-[11px]">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(event) => patch({ enabled: event.target.checked })}
              className="size-4 accent-[var(--brand)]"
            />
            دوربین فعال باشد (استریم دریافت شود)
          </label>
          <label className="flex items-center gap-2 rounded-xl border border-border bg-surface-2/60 p-2.5 text-[11px]">
            <input
              type="checkbox"
              checked={form.detection_enabled}
              onChange={(event) => patch({ detection_enabled: event.target.checked })}
              className="size-4 accent-[var(--brand)]"
            />
            تشخیص اشیا فعال باشد
          </label>
          <label className="flex items-center gap-2 rounded-xl border border-border bg-surface-2/60 p-2.5 text-[11px]">
            <input
              type="checkbox"
              checked={form.record_objects}
              onChange={(event) => patch({ record_objects: event.target.checked })}
              className="size-4 accent-[var(--brand)]"
            />
            تصاویر اشیا ذخیره شود
          </label>
          <label className="flex items-center gap-2 rounded-xl border border-border bg-surface-2/60 p-2.5 text-[11px]">
            <input
              type="checkbox"
              checked={form.record_snapshots}
              onChange={(event) => patch({ record_snapshots: event.target.checked })}
              className="size-4 accent-[var(--brand)]"
            />
            تصویر لحظه رویداد ذخیره شود
          </label>
        </div>

        <Field label="یادداشت">
          <Textarea
            value={form.notes}
            onChange={(event) => patch({ notes: event.target.value })}
            placeholder="توضیحات اختیاری درباره این دوربین"
          />
        </Field>
      </div>
    </Dialog>
  );
}

export function CamerasPage() {
  const cameras = useCameras(undefined, { refetchInterval: 15_000 });
  const statuses = useCameraStatuses(5000);
  const reload = useReloadCamera();
  const remove = useDeleteCamera();
  const toggleDetection = useToggleDetection();
  const [search, setSearch] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Camera | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Camera | null>(null);

  const statusMap = useMemo(
    () => new Map((statuses.data ?? []).map((status) => [status.camera_id, status])),
    [statuses.data],
  );

  const filtered = useMemo(() => {
    const list = cameras.data ?? [];
    if (!search.trim()) return list;
    const needle = search.trim().toLowerCase();
    return list.filter(
      (camera) =>
        camera.name.toLowerCase().includes(needle) ||
        (camera.location ?? "").toLowerCase().includes(needle) ||
        camera.rtsp_url.toLowerCase().includes(needle),
    );
  }, [cameras.data, search]);

  const handleDelete = async () => {
    if (!pendingDelete) return;
    try {
      await remove.mutateAsync(pendingDelete.id);
      toast.success("دوربین حذف شد", pendingDelete.name);
      setPendingDelete(null);
    } catch (error) {
      toast.error("حذف دوربین ناموفق بود", (error as Error).message);
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="دوربین‌ها"
        subtitle="افزودن، ویرایش و پایش وضعیت دوربین‌های مداربسته (RTSP)"
        icon={<CameraIcon />}
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void cameras.refetch();
                void statuses.refetch();
              }}
              loading={cameras.isFetching}
            >
              <RefreshCw />
              به‌روزرسانی
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                setEditing(null);
                setDialogOpen(true);
              }}
            >
              <Plus />
              افزودن دوربین
            </Button>
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full max-w-xs">
          <Search className="absolute right-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="جست‌وجوی نام، موقعیت یا آدرس…"
            className="pr-9"
          />
        </div>
        <Badge tone="muted">
          <span className="num">{toFaDigits(filtered.length)}</span> دوربین
        </Badge>
        <Badge tone="ok">
          <span className="num">
            {toFaDigits((statuses.data ?? []).filter((status) => status.state === "online").length)}
          </span>{" "}
          آنلاین
        </Badge>
        <Badge tone="danger">
          <span className="num">
            {toFaDigits(
              (statuses.data ?? []).filter((status) => status.state === "offline" || status.state === "error")
                .length,
            )}
          </span>{" "}
          آفلاین
        </Badge>
      </div>

      {cameras.isLoading ? (
        <LoadingBlock />
      ) : filtered.length === 0 ? (
        <Card>
          <EmptyState
            icon={<CameraIcon />}
            title={search ? "دوربینی مطابق جست‌وجو پیدا نشد" : "هنوز دوربینی ثبت نشده است"}
            description="با افزودن آدرس RTSP، دوربین به‌صورت خودکار به سامانه متصل و پایش می‌شود."
            action={
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  setEditing(null);
                  setDialogOpen(true);
                }}
              >
                <Plus />
                افزودن اولین دوربین
              </Button>
            }
          />
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          {filtered.map((camera) => {
            const status = statusMap.get(camera.id);
            return (
              <div key={camera.id} className="space-y-2">
                <CameraTile camera={camera} status={status} />
                <div className="flex flex-wrap items-center gap-2 px-1">
                  <Button
                    size="xs"
                    variant={camera.detection_enabled ? "primary" : "outline"}
                    onClick={() =>
                      toggleDetection.mutate({ id: camera.id, enabled: !camera.detection_enabled })
                    }
                    loading={toggleDetection.isPending}
                  >
                    <ScanEye />
                    {camera.detection_enabled ? "تشخیص فعال" : "تشخیص غیرفعال"}
                  </Button>
                  <Button size="xs" variant="outline" onClick={() => reload.mutate(camera.id)} loading={reload.isPending}>
                    <RefreshCw />
                    راه‌اندازی مجدد
                  </Button>
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={() => {
                      setEditing(camera);
                      setDialogOpen(true);
                    }}
                  >
                    <Pencil />
                    ویرایش
                  </Button>
                  <Button size="xs" variant="ghost" onClick={() => setPendingDelete(camera)}>
                    <Trash />
                    حذف
                  </Button>
                  <Badge tone={stateTone(status?.state)}>
                    {camera.enabled ? <Video className="size-3" /> : <VideoOff className="size-3" />}
                    {cameraStateLabel(status?.state ?? (camera.enabled ? "unknown" : "disabled"))}
                  </Badge>
                  {status?.last_frame_at ? (
                    <span className="text-[10px] text-muted">آخرین فریم {formatRelative(status.last_frame_at)}</span>
                  ) : null}
                  <Link to={`/cameras/${camera.id}`} className="text-[10px] text-brand hover:underline">
                    تنظیمات پیشرفته و نواحی
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <CameraFormDialog open={dialogOpen} onClose={() => setDialogOpen(false)} camera={editing} />

      <ConfirmDialog
        open={pendingDelete !== null}
        onClose={() => setPendingDelete(null)}
        onConfirm={handleDelete}
        loading={remove.isPending}
        title="حذف دوربین"
        message={
          <>
            آیا از حذف دوربین «{pendingDelete?.name}» مطمئن هستید؟ رویدادها و تصاویر ثبت‌شده این دوربین نیز حذف
            خواهند شد.
          </>
        }
        confirmLabel="حذف دوربین"
      />
    </div>
  );
}

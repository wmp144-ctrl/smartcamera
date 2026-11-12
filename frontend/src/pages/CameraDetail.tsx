import {
  Activity,
  ArrowRight,
  Camera as CameraIcon,
  Eye,
  EyeOff,
  Gauge,
  Plug,
  RefreshCw,
  Route,
  Save,
  ScanEye,
  Settings,
  Timer,
  TriangleAlert,
  Video,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { DetectionSettings } from "../components/DetectionSettings";
import { ZoneEditor } from "../components/ZoneEditor";
import { Badge, StatusDot } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "../components/ui/card";
import { ConfirmDialog } from "../components/ui/dialog";
import { Field, Input } from "../components/ui/field";
import { EmptyState, KeyValue, LoadingBlock, Metric } from "../components/ui/misc";
import { Tabs } from "../components/ui/tabs";
import { toast } from "../components/ui/toast";
import { api, type ReconnectPolicy } from "../lib/api";
import {
  useCamera,
  useCameraHealth,
  useCameraStatus,
  useDeleteCamera,
  useReloadCamera,
  useTestCamera,
  useToggleDetection,
  useUpdateCamera,
} from "../lib/queries";
import { formatClock } from "../lib/jalali";
import {
  cameraStateLabel,
  classLabel,
  DIRECTION_ARROWS,
  directionLabel,
  faNumber,
  formatBytes,
  formatRelative,
  stateTone,
  toFaDigits,
} from "../lib/utils";
import { useLive } from "../store/live";
import { useUI } from "../store/ui";

type TabKey = "live" | "zones" | "detection" | "health" | "connection";

const TABS: { value: TabKey; label: string; icon: ReactNode }[] = [
  { value: "live", label: "نمای زنده", icon: <Video /> },
  { value: "zones", label: "نواحی و خطوط", icon: <Route /> },
  { value: "detection", label: "حساسیت و مدل", icon: <ScanEye /> },
  { value: "health", label: "سلامت دوربین", icon: <Activity /> },
  { value: "connection", label: "اتصال و بازاتصال", icon: <Plug /> },
];

function LivePanel({ cameraId, fps }: { cameraId: number; fps: number }) {
  const overlay = useUI((state) => state.isOverlay(cameraId));
  const setOverlay = useUI((state) => state.setOverlay);
  const detection = useLive((state) => state.detections[cameraId]);
  const [mode, setMode] = useState<"stream" | "snapshot">("stream");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (mode !== "snapshot") return;
    const timer = setInterval(() => setTick((value) => value + 1), 1200);
    return () => clearInterval(timer);
  }, [mode]);

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant={overlay ? "primary" : "outline"}
            onClick={() => setOverlay(cameraId, !overlay)}
          >
            {overlay ? <Eye /> : <EyeOff />}
            {overlay ? "نمایش اشیای علامت‌گذاری‌شده" : "نمایش تصویر خام"}
          </Button>
          <Button size="sm" variant="outline" onClick={() => setMode(mode === "stream" ? "snapshot" : "stream")}>
            {mode === "stream" ? "حالت تصویر تکی" : "حالت جریان زنده"}
          </Button>
          <Badge tone={overlay ? "brand" : "muted"}>
            {overlay ? "لایه تشخیص فعال" : "پیش‌فرض: تصویر خام"}
          </Badge>
        </div>

        <div className="relative aspect-video w-full overflow-hidden rounded-xl border border-border bg-[#050a14]">
          {mode === "stream" ? (
            <img
              key={`${overlay}-${fps}`}
              src={api.cameras.liveUrl(cameraId, overlay, Math.min(fps || 20, 30))}
              alt="نمای زنده دوربین"
              className="h-full w-full object-contain"
            />
          ) : (
            <img
              key={`snap-${overlay}-${tick}`}
              src={`${api.cameras.snapshotUrl(cameraId, overlay)}&_=${tick}`}
              alt="تصویر لحظه‌ای دوربین"
              className="h-full w-full object-contain"
            />
          )}
        </div>
      </div>

      <div className="space-y-3">
        <Card>
          <CardHeader title="اشیای فعال در کادر" subtitle="به‌روزرسانی زنده از موتور تشخیص" icon={<ScanEye />} />
          <CardBody className="p-0">
            {!detection || detection.tracks.length === 0 ? (
              <EmptyState icon={<ScanEye />} title="شیئی در کادر نیست" />
            ) : (
              <ul className="divide-y divide-border/60">
                {detection.tracks.map((track) => (
                  <li key={track.track_id} className="flex items-center gap-3 px-3 py-2">
                    <Badge tone="brand">{classLabel(track.object_type)}</Badge>
                    <div className="min-w-0 flex-1 text-[11px] text-muted">
                      <p className="num">شناسه {toFaDigits(track.track_id)}</p>
                      <p>
                        {DIRECTION_ARROWS[track.direction] ?? "•"} {directionLabel(track.direction)}
                        {track.motion_state === "stationary" ? " — ثابت" : ""}
                      </p>
                    </div>
                    <span className="num text-[11px] text-brand">{faNumber(track.confidence * 100, 0)}٪</span>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="راهنما" icon={<Settings />} />
          <CardBody className="space-y-1.5 text-[11px] leading-6 text-muted">
            <p>• به‌صورت پیش‌فرض تصویر خام دوربین پخش می‌شود.</p>
            <p>• با فعال‌کردن لایه تشخیص، اشیای شناسایی‌شده روی تصویر زنده علامت‌گذاری می‌شوند.</p>
            <p>• اگر تصویر در دسترس نباشد، وضعیت دوربین و پیام خطا در تب سلامت نمایش داده می‌شود.</p>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function HealthPanel({ cameraId }: { cameraId: number }) {
  const status = useCameraStatus(cameraId, 4000);
  const health = useCameraHealth(cameraId, 6);

  const series = useMemo(
    () =>
      (health.data ?? [])
        .slice()
        .reverse()
        .map((metric) => ({
          label: formatClock(metric.recorded_at),
          fps: Number(metric.fps.toFixed(2)),
          latency: Number(metric.latency_ms.toFixed(1)),
          bitrate: Number((metric.bitrate_kbps / 1000).toFixed(2)),
          jitter: Number(metric.jitter_ms.toFixed(1)),
        })),
    [health.data],
  );

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <Metric label="نرخ فریم" value={faNumber(status.data?.fps ?? 0, 1)} unit="fps" icon={<Video />} tone="brand" />
        <Metric
          label="نرخ تشخیص"
          value={faNumber(status.data?.detection_fps ?? 0, 1)}
          unit="fps"
          icon={<ScanEye />}
        />
        <Metric
          label="بیت‌ریت"
          value={faNumber((status.data?.bitrate_kbps ?? 0) / 1000, 2)}
          unit="Mbps"
          icon={<Activity />}
        />
        <Metric
          label="تفکیک‌پذیری"
          value={
            status.data && status.data.width > 0
              ? `${toFaDigits(status.data.width)}×${toFaDigits(status.data.height)}`
              : "—"
          }
          icon={<CameraIcon />}
        />
        <Metric
          label="تأخیر"
          value={faNumber(status.data?.latency_ms ?? 0, 1)}
          unit="ms"
          icon={<Timer />}
          tone={(status.data?.latency_ms ?? 0) > 3000 ? "warn" : "default"}
        />
        <Metric
          label="تعداد اتصال مجدد"
          value={toFaDigits(status.data?.reconnect_count ?? 0)}
          icon={<RefreshCw />}
          tone={(status.data?.reconnect_count ?? 0) > 3 ? "warn" : "default"}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="نرخ فریم و بیت‌ریت" subtitle="۶ ساعت گذشته" icon={<Activity />} />
          <CardBody>
            {series.length === 0 ? (
              <EmptyState icon={<Activity />} title="داده سلامت ثبت نشده" description="پس از شروع استریم، معیارها هر چند ثانیه ثبت می‌شوند." />
            ) : (
              <div className="h-56 w-full" dir="ltr">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={series}>
                    <defs>
                      <linearGradient id="fpsGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.5} />
                        <stop offset="100%" stopColor="#22d3ee" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 10 }} stroke="var(--border-strong)" />
                    <YAxis tick={{ fontSize: 10 }} stroke="var(--border-strong)" />
                    <ReTooltip
                      contentStyle={{
                        background: "var(--surface-2)",
                        border: "1px solid var(--border)",
                        borderRadius: 12,
                        fontSize: 11,
                        direction: "rtl",
                      }}
                    />
                    <Area type="monotone" dataKey="fps" name="نرخ فریم" stroke="#22d3ee" fill="url(#fpsGrad)" />
                    <Line type="monotone" dataKey="bitrate" name="بیت‌ریت (Mbps)" stroke="#6366f1" dot={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="تأخیر و لرزش" subtitle="۶ ساعت گذشته" icon={<Timer />} />
          <CardBody>
            {series.length === 0 ? (
              <EmptyState icon={<Timer />} title="داده‌ای موجود نیست" />
            ) : (
              <div className="h-56 w-full" dir="ltr">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={series}>
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 10 }} stroke="var(--border-strong)" />
                    <YAxis tick={{ fontSize: 10 }} stroke="var(--border-strong)" />
                    <ReTooltip
                      contentStyle={{
                        background: "var(--surface-2)",
                        border: "1px solid var(--border)",
                        borderRadius: 12,
                        fontSize: 11,
                        direction: "rtl",
                      }}
                    />
                    <Line type="monotone" dataKey="latency" name="تأخیر (ms)" stroke="#f59e0b" dot={false} />
                    <Line type="monotone" dataKey="jitter" name="لرزش (ms)" stroke="#f43f5e" dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="جزئیات دقیق" icon={<Gauge />} />
        <CardBody>
          <div className="grid gap-x-6 md:grid-cols-2">
            <KeyValue label="وضعیت" value={cameraStateLabel(status.data?.state)} />
            <KeyValue label="آخرین فریم" value={status.data?.last_frame_at ? formatRelative(status.data.last_frame_at) : "—"} />
            <KeyValue label="فریم‌های ازدست‌رفته" value={toFaDigits(status.data?.dropped_frames ?? 0)} />
            <KeyValue label="اتصال مجدد" value={toFaDigits(status.data?.reconnect_count ?? 0)} />
            <KeyValue label="پیام" value={status.data?.message ?? "—"} />
            <KeyValue
              label="حجم تقریبی در ساعت"
              value={formatBytes(((status.data?.bitrate_kbps ?? 0) * 1000 * 3600) / 8)}
            />
          </div>
        </CardBody>
      </Card>
    </div>
  );
}

function ConnectionPanel({ cameraId, policy, rtspUrl }: { cameraId: number; policy: ReconnectPolicy; rtspUrl: string }) {
  const [form, setForm] = useState<ReconnectPolicy>(policy);
  const update = useUpdateCamera();
  const test = useTestCamera();
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => setForm(policy), [policy]);

  const patch = (values: Partial<ReconnectPolicy>) => setForm((state) => ({ ...state, ...values }));

  const handleSave = async () => {
    try {
      await update.mutateAsync({ id: cameraId, payload: { reconnect_policy: form } });
      toast.success("سیاست بازاتصال ذخیره شد");
    } catch (error) {
      toast.error("ذخیره ناموفق بود", (error as Error).message);
    }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader title="سیاست بازاتصال" subtitle="رفتار سامانه هنگام قطع ارتباط با دوربین" icon={<RefreshCw />} />
        <CardBody className="space-y-3">
          <Field label="حداکثر تعداد تلاش" hint="۰ = تلاش بی‌نهایت">
            <Input
              type="number"
              min={0}
              value={form.max_retries}
              onChange={(event) => patch({ max_retries: Number(event.target.value) })}
            />
          </Field>
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="تأخیر اولیه (ثانیه)">
              <Input
                type="number"
                min={0.1}
                step={0.1}
                value={form.backoff_initial}
                onChange={(event) => patch({ backoff_initial: Number(event.target.value) })}
              />
            </Field>
            <Field label="حداکثر تأخیر (ثانیه)">
              <Input
                type="number"
                min={1}
                step={1}
                value={form.backoff_max}
                onChange={(event) => patch({ backoff_max: Number(event.target.value) })}
              />
            </Field>
            <Field label="ضریب افزایش">
              <Input
                type="number"
                min={1}
                step={0.5}
                value={form.backoff_factor}
                onChange={(event) => patch({ backoff_factor: Number(event.target.value) })}
              />
            </Field>
          </div>
          <Button variant="primary" size="sm" onClick={handleSave} loading={update.isPending}>
            <Save />
            ذخیره سیاست
          </Button>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="آدرس استریم و آزمایش اتصال" icon={<Plug />} />
        <CardBody className="space-y-3">
          <div className="rounded-xl border border-border bg-surface-2/60 p-3">
            <p className="mb-1 text-[11px] text-muted">آدرس RTSP</p>
            <p dir="ltr" className="num break-all text-left text-[11px]">
              {rtspUrl}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              const response = await test.mutateAsync(cameraId);
              setResult(
                response.ok
                  ? `${response.message} — ${toFaDigits(response.width ?? 0)}×${toFaDigits(
                      response.height ?? 0,
                    )} @ ${faNumber(response.fps ?? 0, 1)} fps${response.codec ? ` — ${response.codec}` : ""}`
                  : response.message,
              );
            }}
            loading={test.isPending}
          >
            <Plug />
            آزمایش اتصال
          </Button>
          {result ? (
            <p className="rounded-xl border border-info/40 bg-info/10 px-3 py-2 text-[11px] text-info">{result}</p>
          ) : null}
          <p className="text-[11px] leading-6 text-muted">
            برای تغییر آدرس RTSP از دکمه ویرایش در همین صفحه استفاده کنید.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}

export function CameraDetailPage() {
  const { cameraId: rawId } = useParams();
  const cameraId = Number(rawId);
  const navigate = useNavigate();

  const camera = useCamera(Number.isFinite(cameraId) ? cameraId : null);
  const status = useCameraStatus(Number.isFinite(cameraId) ? cameraId : null, 4000);
  const toggleDetection = useToggleDetection();
  const reload = useReloadCamera();
  const remove = useDeleteCamera();
  const [tab, setTab] = useState<TabKey>("live");
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (!Number.isFinite(cameraId)) {
    return <EmptyState icon={<TriangleAlert />} title="شناسه دوربین نامعتبر است" />;
  }

  if (camera.isLoading) return <LoadingBlock />;
  if (camera.isError || !camera.data) {
    return (
      <EmptyState
        icon={<TriangleAlert />}
        title="دوربین یافت نشد"
        description={(camera.error as Error | null)?.message ?? "این دوربین حذف شده یا در دسترس نیست."}
        action={
          <Link to="/cameras" className="text-xs text-brand hover:underline">
            بازگشت به فهرست دوربین‌ها
          </Link>
        }
      />
    );
  }

  const item = camera.data;
  const aspectRatio =
    status.data && status.data.width > 0 && status.data.height > 0
      ? status.data.width / status.data.height
      : item.width && item.height
        ? item.width / item.height
        : 16 / 9;

  const policy: ReconnectPolicy = {
    max_retries: item.reconnect_policy?.max_retries ?? 0,
    backoff_initial: item.reconnect_policy?.backoff_initial ?? 1,
    backoff_max: item.reconnect_policy?.backoff_max ?? 30,
    backoff_factor: item.reconnect_policy?.backoff_factor ?? 2,
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title={item.name}
        subtitle={
          <span className="inline-flex flex-wrap items-center gap-2">
            <span>{item.location || "بدون موقعیت"}</span>
            <span className="text-muted">•</span>
            <span className="inline-flex items-center gap-1.5">
              <StatusDot tone={stateTone(status.data?.state)} pulse={status.data?.state === "online"} />
              {cameraStateLabel(status.data?.state)}
            </span>
            {item.detection_enabled ? <Badge tone="brand">تشخیص فعال</Badge> : <Badge tone="muted">تشخیص غیرفعال</Badge>}
          </span>
        }
        icon={<CameraIcon />}
        actions={
          <>
            <Link
              to="/cameras"
              className="inline-flex items-center gap-1 rounded-xl border border-border px-3 py-2 text-xs text-muted transition-colors hover:border-brand/50 hover:text-fg"
            >
              <ArrowRight className="size-3.5" />
              فهرست دوربین‌ها
            </Link>
            <Button
              variant={item.detection_enabled ? "primary" : "outline"}
              size="sm"
              loading={toggleDetection.isPending}
              onClick={() => toggleDetection.mutate({ id: item.id, enabled: !item.detection_enabled })}
            >
              <ScanEye />
              {item.detection_enabled ? "غیرفعال‌سازی تشخیص" : "فعال‌سازی تشخیص"}
            </Button>
            <Button variant="outline" size="sm" loading={reload.isPending} onClick={() => reload.mutate(item.id)}>
              <RefreshCw />
              راه‌اندازی مجدد
            </Button>
            <Button variant="danger" size="sm" onClick={() => setConfirmDelete(true)}>
              حذف
            </Button>
          </>
        }
      />

      {item.detection_enabled && status.data?.detector_error ? (
        <div className="flex items-start gap-3 rounded-2xl border border-warn/40 bg-warn/5 p-4">
          <TriangleAlert className="mt-0.5 size-5 shrink-0 text-warn" />
          <div className="space-y-1 text-xs leading-6">
            <p className="font-semibold text-warn">تشخیص اشیا برای این دوربین فعال نیست</p>
            <p className="text-muted">
              موتور تشخیص بارگذاری نشد، بنابراین تصویر به‌صورت خام پخش می‌شود و هیچ شیئی
              ثبت نمی‌شود. برای فعال شدن تشخیص، بسته‌های هوش مصنوعی را نصب کنید
              (فایل <span className="font-mono">requirements-ai.txt</span> یا اسکریپت نصب آفلاین).
            </p>
            <p className="font-mono text-[11px] text-muted">{status.data.detector_error}</p>
          </div>
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="نرخ فریم"
          value={faNumber(status.data?.fps ?? 0, 1)}
          unit="fps"
          icon={<Video />}
          tone="brand"
        />
        <Metric
          label="بیت‌ریت"
          value={faNumber((status.data?.bitrate_kbps ?? 0) / 1000, 2)}
          unit="Mbps"
          icon={<Activity />}
        />
        <Metric
          label="تأخیر"
          value={faNumber(status.data?.latency_ms ?? 0, 1)}
          unit="ms"
          icon={<Timer />}
        />
        <Metric
          label="فریم‌های ازدست‌رفته"
          value={toFaDigits(status.data?.dropped_frames ?? 0)}
          icon={<Gauge />}
          tone={(status.data?.dropped_frames ?? 0) > 0 ? "warn" : "ok"}
        />
      </div>

      {status.data?.state === "error" || status.data?.state === "offline" ? (
        <div className="flex items-start gap-3 rounded-xl border border-danger/40 bg-danger/10 px-4 py-3 text-[11px] text-danger">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <div>
            <p className="font-medium">ارتباط با این دوربین برقرار نیست</p>
            <p className="mt-1 leading-6">{status.data.message ?? "دلیل خطا از سوی سرور گزارش نشده است."}</p>
          </div>
        </div>
      ) : null}

      <Tabs items={TABS} value={tab} onChange={setTab} />

      {tab === "live" ? <LivePanel cameraId={item.id} fps={item.fps} /> : null}
      {tab === "zones" ? <ZoneEditor camera={item} aspectRatio={aspectRatio} /> : null}
      {tab === "detection" ? <DetectionSettings camera={item} /> : null}
      {tab === "health" ? <HealthPanel cameraId={item.id} /> : null}
      {tab === "connection" ? (
        <ConnectionPanel cameraId={item.id} policy={policy} rtspUrl={item.rtsp_url} />
      ) : null}

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="حذف دوربین"
        message={<>آیا از حذف «{item.name}» مطمئن هستید؟ این عمل قابل بازگشت نیست.</>}
        confirmLabel="حذف"
        loading={remove.isPending}
        onConfirm={async () => {
          try {
            await remove.mutateAsync(item.id);
            toast.success("دوربین حذف شد");
            navigate("/cameras");
          } catch (error) {
            toast.error("حذف ناموفق بود", (error as Error).message);
          }
        }}
      />

      <Card>
        <CardBody className="flex flex-wrap items-center justify-between gap-3 text-[11px] text-muted">
          <span>
            مدل: <span className="text-fg">{item.model_name}</span> — نرخ تشخیص:{" "}
            <span className="num text-fg">{faNumber(item.detection_fps, 1)}</span> fps — حساسیت حرکت:{" "}
            <span className="num text-fg">{faNumber(item.motion_sensitivity * 100, 2)}٪</span>
          </span>
          <span>
            ایجاد: {formatRelative(item.created_at)} — آخرین تغییر: {formatRelative(item.updated_at)}
          </span>
        </CardBody>
      </Card>
    </div>
  );
}

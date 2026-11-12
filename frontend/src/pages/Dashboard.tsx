import {
  Activity,
  Bell,
  Camera,
  ChartColumn,
  Cpu,
  HardDrive,
  Images,
  LayoutDashboard,
  RefreshCw,
  ScanEye,
  Thermometer,
  TrendingUp,
  TriangleAlert,
  Users,
  Zap,
} from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CameraTile } from "../components/CameraTile";
import { StatusDot } from "../components/ui/badge";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "../components/ui/card";
import { EmptyState, LoadingBlock, Metric, Progress } from "../components/ui/misc";
import { formatClock } from "../lib/jalali";
import { useCameras, useCameraStatuses, useGPU, useOverview, useRuntime } from "../lib/queries";
import {
  classLabel,
  directionLabel,
  eventTypeLabel,
  faNumber,
  formatBytes,
  formatRelative,
  severityTone,
  stateTone,
  toFaDigits,
} from "../lib/utils";
import { useLive } from "../store/live";

const CHART_COLORS = ["#22d3ee", "#6366f1", "#f59e0b", "#22c55e", "#f43f5e", "#38bdf8", "#a855f7"];

const MODEL_NAMES: Record<string, string> = {
  "rf-detr-nano": "Nano",
  "rf-detr-small": "Small",
  "rf-detr-base": "Base",
  "rf-detr-medium": "Medium",
  "rf-detr-large": "Large",
};

type DetectionBudget = {
  cameras: number;
  overloaded: boolean;
  suggested_model: string | null;
  models: { model: string; demand_fps: number; capacity_fps: number; overloaded: boolean }[];
};

export function DashboardPage() {
  const overview = useOverview(14, 20_000);
  const cameras = useCameras(undefined, { refetchInterval: 20_000 });
  const statuses = useCameraStatuses(5000);
  const gpu = useGPU(5000);
  const runtime = useRuntime(15_000);
  const budget = (
    runtime.data?.manager as { detection_budget?: DetectionBudget } | undefined
  )?.detection_budget;
  const demandFps = useMemo(
    () => (budget?.models ?? []).reduce((total, item) => total + item.demand_fps, 0),
    [budget],
  );
  const recentEvents = useLive((state) => state.recentEvents);
  const connection = useLive((state) => state.connection);

  const data = overview.data;
  const statusMap = useMemo(
    () => new Map((statuses.data ?? []).map((status) => [status.camera_id, status])),
    [statuses.data],
  );

  const hourly = useMemo(
    () =>
      (data?.hourly ?? []).map((bucket) => ({
        label: formatClock(bucket.bucket),
        count: bucket.count,
      })),
    [data?.hourly],
  );

  const byType = useMemo(
    () =>
      (data?.by_type ?? [])
        .slice(0, 7)
        .map((item, index) => ({
          name: classLabel(item.key),
          value: item.count,
          color: CHART_COLORS[index % CHART_COLORS.length],
        })),
    [data?.by_type],
  );

  const byDirection = useMemo(
    () =>
      (data?.by_direction ?? [])
        .slice(0, 6)
        .map((item) => ({ name: directionLabel(item.key), count: item.count })),
    [data?.by_direction],
  );

  const byEvent = useMemo(
    () =>
      (data?.by_event_type ?? [])
        .slice(0, 6)
        .map((item, index) => ({
          name: eventTypeLabel(item.key),
          value: item.count,
          color: CHART_COLORS[index % CHART_COLORS.length],
        })),
    [data?.by_event_type],
  );

  const device = gpu.data?.devices?.[0];

  return (
    <div className="space-y-5">
      <PageHeader
        title="داشبورد"
        subtitle={
          data
            ? `گزارش ${toFaDigits(data.timezone)} — آخرین به‌روزرسانی ${formatRelative(data.generated_at)}`
            : "نمای کلی سامانه پایش"
        }
        icon={<LayoutDashboard />}
        actions={
          <>
            <span className="inline-flex items-center gap-2 rounded-xl border border-border bg-surface-2/60 px-3 py-2 text-[11px] text-muted">
              <StatusDot tone={connection === "open" ? "ok" : "danger"} pulse={connection === "open"} />
              جریان زنده {connection === "open" ? "متصل" : "قطع"}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void overview.refetch();
                void statuses.refetch();
                void gpu.refetch();
              }}
              loading={overview.isFetching}
            >
              <RefreshCw />
              به‌روزرسانی
            </Button>
          </>
        }
      />

      {budget?.overloaded ? (
        <div className="panel flex flex-wrap items-center gap-x-3 gap-y-1 border-warn/40 bg-warn/10 px-4 py-3 text-xs">
          <TriangleAlert className="size-4 shrink-0 text-warn" />
          <span className="font-semibold text-warn">
            بار پردازش از ظرفیت کارت گرافیک بیشتر است
          </span>
          <span className="text-muted">
            {toFaDigits(budget.cameras)} دوربین در مجموع {faNumber(demandFps, 1)} تشخیص در ثانیه
            می‌خواهند؛ فریم‌ها عقب می‌افتند.
            {budget.suggested_model
              ? ` مدل پیشنهادی: ${MODEL_NAMES[budget.suggested_model] ?? budget.suggested_model}.`
              : ""}
          </span>
          <Link to="/settings" className="font-medium text-brand hover:underline">
            تغییر تنظیمات
          </Link>
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="دوربین‌های آنلاین"
          value={`${toFaDigits(data?.online_cameras ?? 0)} / ${toFaDigits(data?.total_cameras ?? 0)}`}
          icon={<Camera />}
          tone={(data?.online_cameras ?? 0) > 0 ? "ok" : "danger"}
          hint={`${toFaDigits(data?.offline_cameras ?? 0)} دوربین آفلاین`}
        />
        <Metric
          label="اشیای ثبت‌شده امروز"
          value={toFaDigits(data?.tracks_today ?? 0)}
          icon={<ScanEye />}
          tone="brand"
          hint={`مجموع کل: ${toFaDigits(data?.total_tracks ?? 0)}`}
        />
        <Metric
          label="رویدادهای امروز"
          value={toFaDigits(data?.events_today ?? 0)}
          icon={<Activity />}
          hint={`مجموع کل: ${toFaDigits(data?.total_events ?? 0)}`}
        />
        <Metric
          label="هشدارهای تأییدنشده"
          value={toFaDigits(data?.unacknowledged_alerts ?? 0)}
          icon={<Bell />}
          tone={(data?.unacknowledged_alerts ?? 0) > 0 ? "warn" : "ok"}
          hint={`مجموع هشدارها: ${toFaDigits(data?.total_alerts ?? 0)}`}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="فعالیت سامانه در ۲۴ ساعت گذشته"
            subtitle="تعداد اشیای ثبت‌شده در هر ساعت (به وقت محلی)"
            icon={<TrendingUp />}
          />
          <CardBody>
            {overview.isLoading ? (
              <LoadingBlock />
            ) : hourly.length === 0 ? (
              <EmptyState icon={<ChartColumn />} title="داده‌ای برای نمایش نیست" description="پس از فعال‌سازی تشخیص، نمودار فعالیت اینجا نمایش داده می‌شود." />
            ) : (
              <div className="h-64 w-full" dir="ltr">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={hourly}>
                    <defs>
                      <linearGradient id="activity" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.55} />
                        <stop offset="100%" stopColor="#22d3ee" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 10 }} stroke="var(--border-strong)" />
                    <YAxis tick={{ fontSize: 10 }} stroke="var(--border-strong)" allowDecimals={false} />
                    <ReTooltip
                      contentStyle={{
                        background: "var(--surface-2)",
                        border: "1px solid var(--border)",
                        borderRadius: 12,
                        fontSize: 11,
                        direction: "rtl",
                      }}
                      labelStyle={{ color: "var(--muted)" }}
                    />
                    <Area
                      type="monotone"
                      dataKey="count"
                      name="تعداد اشیا"
                      stroke="#22d3ee"
                      strokeWidth={2}
                      fill="url(#activity)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="مدیریت GPU"
            subtitle={gpu.data?.available ? `${gpu.data.source}` : "GPU در دسترس نیست"}
            icon={<Cpu />}
            actions={
              gpu.data?.available ? (
                <Badge tone="ok">فعال</Badge>
              ) : (
                <Badge tone="warn">حالت CPU</Badge>
              )
            }
          />
          <CardBody className="space-y-3">
            {device ? (
              <>
                <p className="truncate text-[11px] text-muted" title={device.name}>
                  {device.name}
                </p>
                <div>
                  <div className="mb-1 flex items-center justify-between text-[11px]">
                    <span className="inline-flex items-center gap-1 text-muted">
                      <Zap className="size-3" /> مصرف پردازنده
                    </span>
                    <span className="num">{faNumber(device.utilization)}٪</span>
                  </div>
                  <Progress value={device.utilization} showLabel={false} />
                </div>
                <div>
                  <div className="mb-1 flex items-center justify-between text-[11px]">
                    <span className="inline-flex items-center gap-1 text-muted">
                      <HardDrive className="size-3" /> حافظه گرافیکی
                    </span>
                    <span className="num">
                      {faNumber(device.memory_used_mb / 1024, 1)} / {faNumber(device.memory_total_mb / 1024, 1)} GB
                    </span>
                  </div>
                  <Progress
                    value={device.memory_percent}
                    tone={device.memory_percent > 90 ? "danger" : device.memory_percent > 75 ? "warn" : "brand"}
                  />
                </div>
                <div className="grid grid-cols-2 gap-2 text-[11px]">
                  <div className="rounded-xl border border-border bg-surface-2/60 p-2.5">
                    <p className="inline-flex items-center gap-1 text-muted">
                      <Thermometer className="size-3" /> دما
                    </p>
                    <p className="num mt-1 text-sm font-bold">
                      {device.temperature_c !== null ? `${faNumber(device.temperature_c)}°` : "—"}
                    </p>
                  </div>
                  <div className="rounded-xl border border-border bg-surface-2/60 p-2.5">
                    <p className="inline-flex items-center gap-1 text-muted">
                      <ScanEye className="size-3" /> نرخ تشخیص
                    </p>
                    <p className="num mt-1 text-sm font-bold">
                      {faNumber(device.detection_fps, 1)} <span className="text-[11px] text-muted">fps</span>
                    </p>
                  </div>
                </div>
                <div className="flex items-center justify-between rounded-xl border border-border bg-surface-2/60 p-2.5 text-[11px]">
                  <span className="inline-flex items-center gap-1 text-muted">
                    <Camera className="size-3" /> دوربین‌های در حال تشخیص
                  </span>
                  <span className="num font-bold text-brand">{toFaDigits(device.active_cameras)}</span>
                </div>
                {gpu.data?.driver_version ? (
                  <p className="text-[11px] text-muted">
                    درایور {toFaDigits(gpu.data.driver_version)}
                    {gpu.data.cuda_version ? ` — CUDA ${toFaDigits(gpu.data.cuda_version)}` : ""}
                  </p>
                ) : null}
              </>
            ) : (
              <EmptyState
                icon={<Cpu />}
                title="اطلاعات GPU در دسترس نیست"
                description="nvidia-smi پیدا نشد یا درایور نصب نیست؛ پردازش روی CPU اجرا می‌شود."
              />
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="ترکیب اشیای شناسایی‌شده" icon={<Images />} />
          <CardBody>
            {byType.length === 0 ? (
              <EmptyState icon={<Images />} title="داده‌ای موجود نیست" />
            ) : (
              <div className="h-56 w-full" dir="ltr">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={byType} dataKey="value" nameKey="name" innerRadius={48} outerRadius={80} paddingAngle={3}>
                      {byType.map((entry) => (
                        <Cell key={entry.name} fill={entry.color} stroke="transparent" />
                      ))}
                    </Pie>
                    <ReTooltip
                      contentStyle={{
                        background: "var(--surface-2)",
                        border: "1px solid var(--border)",
                        borderRadius: 12,
                        fontSize: 11,
                        direction: "rtl",
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            )}
            <div className="mt-2 flex flex-wrap gap-2">
              {byType.map((entry) => (
                <span key={entry.name} className="inline-flex items-center gap-1.5 text-[11px] text-muted">
                  <span className="size-2 rounded-full" style={{ background: entry.color }} />
                  {entry.name}
                  <span className="num">{toFaDigits(entry.value)}</span>
                </span>
              ))}
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="جهت حرکت اشیا" subtitle="پرتکرارترین جهت‌ها" icon={<Users />} />
          <CardBody>
            {byDirection.length === 0 ? (
              <EmptyState icon={<Users />} title="داده‌ای موجود نیست" />
            ) : (
              <div className="h-56 w-full" dir="ltr">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={byDirection} layout="vertical" margin={{ left: 8, right: 12 }}>
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 10 }} stroke="var(--border-strong)" allowDecimals={false} />
                    <YAxis
                      type="category"
                      dataKey="name"
                      tick={{ fontSize: 10 }}
                      stroke="var(--border-strong)"
                      width={64}
                    />
                    <ReTooltip
                      contentStyle={{
                        background: "var(--surface-2)",
                        border: "1px solid var(--border)",
                        borderRadius: 12,
                        fontSize: 11,
                        direction: "rtl",
                      }}
                    />
                    <Bar dataKey="count" name="تعداد" fill="#6366f1" radius={[0, 6, 6, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="انواع رویداد" icon={<Activity />} />
          <CardBody>
            {byEvent.length === 0 ? (
              <EmptyState icon={<Activity />} title="داده‌ای موجود نیست" />
            ) : (
              <div className="h-56 w-full" dir="ltr">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={byEvent}>
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="name" tick={{ fontSize: 10 }} stroke="var(--border-strong)" />
                    <YAxis tick={{ fontSize: 10 }} stroke="var(--border-strong)" allowDecimals={false} />
                    <ReTooltip
                      contentStyle={{
                        background: "var(--surface-2)",
                        border: "1px solid var(--border)",
                        borderRadius: 12,
                        fontSize: 11,
                        direction: "rtl",
                      }}
                    />
                    <Bar dataKey="value" name="تعداد" fill="#f59e0b" radius={[6, 6, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="وضعیت دوربین‌ها و تصاویر زنده"
          subtitle="پیش‌فرض: تصویر خام — با دکمه چشم، اشیای شناسایی‌شده روی تصویر علامت‌گذاری می‌شوند"
          icon={<Camera />}
          actions={
            <Link
              to="/cameras"
              className="text-[11px] text-brand hover:underline"
            >
              مدیریت دوربین‌ها
            </Link>
          }
        />
        <CardBody>
          {cameras.isLoading ? (
            <LoadingBlock />
          ) : (cameras.data ?? []).length === 0 ? (
            <EmptyState
              icon={<Camera />}
              title="هنوز دوربینی اضافه نشده"
              description="برای شروع، آدرس RTSP دوربین را در بخش دوربین‌ها ثبت کنید."
              action={
                <Link
                  to="/cameras"
                  className="rounded-xl bg-brand px-4 py-2 text-xs font-medium text-[#04202a]"
                >
                  افزودن دوربین
                </Link>
              }
            />
          ) : (
            <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
              {(cameras.data ?? []).slice(0, 6).map((camera) => (
                <CameraTile key={camera.id} camera={camera} status={statusMap.get(camera.id)} />
              ))}
            </div>
          )}
        </CardBody>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="آخرین رویدادهای زنده"
            subtitle="رویدادهایی که همین حالا از دوربین‌ها دریافت شده‌اند"
            icon={<Activity />}
            actions={
              <Link to="/events" className="text-[11px] text-brand hover:underline">
                همه رویدادها
              </Link>
            }
          />
          <CardBody className="p-0">
            {recentEvents.length === 0 ? (
              <EmptyState icon={<Activity />} title="در انتظار رویداد جدید" description="با فعال بودن جریان زنده، رویدادها بی‌درنگ اینجا نمایش داده می‌شوند." />
            ) : (
              <ul className="divide-y divide-border/60">
                {recentEvents.slice(0, 8).map((event) => (
                  <li key={`${event.id}-${event.occurred_at}`} className="flex items-center gap-3 px-4 py-2.5">
                    <Badge tone={severityTone(event.severity)}>{eventTypeLabel(event.event_type)}</Badge>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[11px]">{event.message}</p>
                      <p className="mt-0.5 text-[11px] text-muted">
                        {event.camera_name} — {formatRelative(event.occurred_at)}
                      </p>
                    </div>
                    {event.object_type ? (
                      <span className="shrink-0 text-[11px] text-muted">{classLabel(event.object_type)}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="آمار دوربین‌ها" subtitle="۷ روز گذشته" icon={<ChartColumn />} />
          <CardBody className="space-y-2.5">
            {(data?.cameras ?? []).length === 0 ? (
              <EmptyState icon={<Camera />} title="آماری موجود نیست" />
            ) : (
              (data?.cameras ?? []).map((camera) => {
                const status = statusMap.get(camera.camera_id);
                const tone = stateTone(status?.state);
                return (
                  <Link
                    key={camera.camera_id}
                    to={`/cameras/${camera.camera_id}`}
                    className="flex items-center gap-3 rounded-xl border border-border bg-surface-2/60 p-2.5 transition-colors hover:border-brand/40"
                  >
                    <StatusDot tone={tone} pulse={status?.state === "online"} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[11px] font-medium">{camera.camera_name}</p>
                      <p className="text-[11px] text-muted">
                        <span className="num">{toFaDigits(camera.total_tracks)}</span> شیء —{" "}
                        <span className="num">{toFaDigits(camera.total_events)}</span> رویداد —{" "}
                        <span className="num">{toFaDigits(camera.total_alerts)}</span> هشدار
                      </p>
                    </div>
                    <span className="num shrink-0 text-[11px] text-muted">{faNumber(camera.fps, 1)} fps</span>
                  </Link>
                );
              })
            )}

            <div className="mt-3 flex items-center justify-between border-t border-border pt-3 text-[11px]">
              <span className="inline-flex items-center gap-1.5 text-muted">
                <HardDrive className="size-3" /> مصرف فضای ذخیره‌سازی
              </span>
              <span className="num font-medium">{formatBytes(data?.storage_bytes ?? 0)}</span>
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

import {
  Activity,
  Bell,
  Boxes,
  Cpu,
  Database,
  HardDrive,
  Info,
  Moon,
  RefreshCw,
  Save,
  Settings as SettingsIcon,
  Sun,
  Trash,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Badge, StatusDot } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "../components/ui/card";
import { Field, Input, Select, Switch } from "../components/ui/field";
import { EmptyState, KeyValue, LoadingBlock, Metric, Progress, Slider } from "../components/ui/misc";
import { TableWrap, Tbody, Td, Th, Thead, Tr } from "../components/ui/table";
import { toast } from "../components/ui/toast";
import {
  useCompatibility,
  useGPU,
  useInitDb,
  useModels,
  useRunRetention,
  useRuntime,
  useSaveSetting,
  useSettings,
  useStorage,
  useSystemInfo,
} from "../lib/queries";
import { formatJalaliDateTime } from "../lib/jalali";
import { faNumber, formatBytes, formatRelative, toFaDigits } from "../lib/utils";
import { useUI } from "../store/ui";

function RawValue({ value }: { value: unknown }) {
  return <span className="num break-all text-[10px]">{typeof value === "string" ? value : JSON.stringify(value)}</span>;
}

export function SettingsPage() {
  const info = useSystemInfo(30_000);
  const gpu = useGPU(6000);
  const models = useModels();
  const compatibility = useCompatibility();
  const storage = useStorage();
  const settings = useSettings();
  const runtime = useRuntime(8000);
  const saveSetting = useSaveSetting();
  const retention = useRunRetention();
  const initDb = useInitDb();

  const theme = useUI((state) => state.theme);
  const setTheme = useUI((state) => state.setTheme);
  const soundEnabled = useUI((state) => state.soundEnabled);
  const setSound = useUI((state) => state.setSound);
  const volume = useUI((state) => state.volume);
  const setVolume = useUI((state) => state.setVolume);
  const autoRefreshSeconds = useUI((state) => state.autoRefreshSeconds);
  const setAutoRefresh = useUI((state) => state.setAutoRefresh);
  const overlayDefault = useUI((state) => state.overlayDefault);
  const setOverlayDefault = useUI((state) => state.setOverlayDefault);

  const [retentionDays, setRetentionDays] = useState(30);
  const [dryRun, setDryRun] = useState(true);
  const [editing, setEditing] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!settings.data) return;
    const next: Record<string, string> = {};
    for (const entry of settings.data) {
      next[entry.key] =
        typeof entry.value === "string" ? entry.value : JSON.stringify(entry.value ?? "");
    }
    setEditing(next);
  }, [settings.data]);

  const device = gpu.data?.devices?.[0];

  const components = useMemo(() => info.data?.components ?? [], [info.data]);
  const missingComponents = components.filter((component) => !component.available);

  const compatibilityIssues = (compatibility.data?.issues as string[] | undefined) ?? [];
  const compatibilityNotes = (compatibility.data?.notes as string[] | undefined) ?? [];
  const compatibilityVersions = (compatibility.data?.versions as Record<string, string | null>) ?? {};

  return (
    <div className="space-y-5">
      <PageHeader
        title="تنظیمات سیستم"
        subtitle="اطلاعات محیط اجرا، وضعیت کتابخانه‌ها، مدل‌ها، فضای ذخیره‌سازی و نگه‌داری داده‌ها"
        icon={<SettingsIcon />}
        actions={
          <Button variant="outline" size="sm" onClick={() => void info.refetch()} loading={info.isFetching}>
            <RefreshCw />
            به‌روزرسانی
          </Button>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="نسخه سامانه"
          value={toFaDigits(info.data?.version ?? "—")}
          icon={<Info />}
          tone="brand"
          hint={info.data ? `پایتون ${info.data.python_version}` : undefined}
        />
        <Metric
          label="فضای ذخیره‌سازی مصرفی"
          value={formatBytes(info.data?.storage_bytes ?? 0)}
          icon={<HardDrive />}
          hint={`${toFaDigits(storage.data?.file_count ?? 0)} فایل تصویر`}
        />
        <Metric
          label="تعداد مدل‌ها"
          value={`${toFaDigits((models.data ?? []).filter((model) => model.available).length)} / ${toFaDigits(
            (models.data ?? []).length,
          )}`}
          icon={<Boxes />}
          tone={(models.data ?? []).some((model) => model.available) ? "ok" : "warn"}
          hint="وزن‌های RF-DETR موجود روی دیسک"
        />
        <Metric
          label="منطقه زمانی"
          value={toFaDigits(info.data?.timezone ?? "—")}
          icon={<Activity />}
          hint={info.data ? `الان: ${formatJalaliDateTime(info.data.utc_now)}` : undefined}
        />
      </div>

      {compatibilityIssues.length > 0 ? (
        <div className="flex items-start gap-3 rounded-xl border border-warn/50 bg-warn/10 px-4 py-3 text-[11px] text-warn">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <div className="space-y-1">
            <p className="font-medium">ناسازگاری نسخه‌ها شناسایی شد</p>
            {compatibilityIssues.map((issue) => (
              <p key={issue} className="leading-6">
                {issue}
              </p>
            ))}
          </div>
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="اطلاعات محیط اجرا" icon={<Info />} />
          <CardBody>
            {info.isLoading ? (
              <LoadingBlock />
            ) : (
              <>
                <KeyValue label="نام سامانه" value={info.data?.app_name ?? "—"} />
                <KeyValue label="نسخه" value={toFaDigits(info.data?.version ?? "—")} />
                <KeyValue label="نسخه پایتون" value={toFaDigits(info.data?.python_version ?? "—")} />
                <KeyValue label="سیستم‌عامل" value={info.data?.platform ?? "—"} />
                <KeyValue label="منطقه زمانی نمایش" value={toFaDigits(info.data?.timezone ?? "—")} />
                <KeyValue
                  label="زمان فعلی (شمسی)"
                  value={info.data ? formatJalaliDateTime(info.data.utc_now) : "—"}
                />
                <KeyValue label="پایگاه داده" value={<RawValue value={info.data?.database_url ?? "—"} />} />
                <KeyValue label="مسیر ذخیره‌سازی" value={<RawValue value={info.data?.storage_root ?? "—"} />} />
                <p className="mt-3 rounded-xl border border-border bg-surface-2/60 px-3 py-2 text-[10px] leading-6 text-muted">
                  همه زمان‌ها در پایگاه داده به وقت جهانی (UTC) ذخیره می‌شوند و در رابط کاربری با منطقه زمانی
                  انتخاب‌شده و تقویم شمسی نمایش داده می‌شوند.
                </p>
              </>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="وضعیت کتابخانه‌ها و سازگاری نسخه‌ها"
            subtitle="PyTorch، TorchVision، CUDA و ONNX Runtime"
            icon={<Boxes />}
            actions={
              missingComponents.length > 0 ? (
                <Badge tone="warn">
                  <span className="num">{toFaDigits(missingComponents.length)}</span> نصب‌نشده
                </Badge>
              ) : (
                <Badge tone="ok">کامل</Badge>
              )
            }
          />
          <CardBody className="space-y-3">
            <div className="grid gap-x-6 md:grid-cols-2">
              {components.map((component) => (
                <div
                  key={component.name}
                  className="flex items-center justify-between gap-2 border-b border-border/50 py-1.5 last:border-0"
                >
                  <span className="inline-flex items-center gap-1.5 text-[11px] text-muted">
                    <StatusDot tone={component.available ? "ok" : "muted"} />
                    {component.name}
                  </span>
                  <span className="num text-[11px]">{toFaDigits(component.version)}</span>
                </div>
              ))}
            </div>

            {Object.keys(compatibilityVersions).length > 0 ? (
              <div className="rounded-xl border border-border bg-surface-2/60 p-3">
                <p className="mb-2 text-[11px] font-medium">نسخه‌های کلیدی</p>
                {Object.entries(compatibilityVersions).map(([name, version]) => (
                  <KeyValue
                    key={name}
                    label={name}
                    value={version ? toFaDigits(version) : <span className="text-danger">نصب‌نشده</span>}
                  />
                ))}
              </div>
            ) : null}

            {compatibilityNotes.length > 0 ? (
              <ul className="space-y-1.5">
                {compatibilityNotes.map((note) => (
                  <li key={note} className="text-[10px] leading-6 text-muted">
                    • {note}
                  </li>
                ))}
              </ul>
            ) : null}

            <p className="rounded-xl border border-border bg-surface-2/60 px-3 py-2 text-[10px] leading-6 text-muted">
              کتابخانه‌های سنگین هوش مصنوعی (PyTorch/TorchVision/ONNX Runtime/RF-DETR) به‌صورت تنبل بارگذاری
              می‌شوند؛ برای راه‌اندازی آفلاین از اسکریپت‌های پوشه scripts استفاده کنید.
            </p>
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="مدل‌های RF-DETR" subtitle="وزن‌های قابل انتخاب برای هر دوربین" icon={<Boxes />} />
          <CardBody className="p-0">
            {models.isLoading ? (
              <LoadingBlock />
            ) : (
              <TableWrap>
                <Thead>
                  <tr>
                    <Th>مدل</Th>
                    <Th>تفکیک‌پذیری</Th>
                    <Th>فایل وزن</Th>
                    <Th>وضعیت</Th>
                    <Th>توضیح</Th>
                  </tr>
                </Thead>
                <Tbody>
                  {(models.data ?? []).map((model) => (
                    <Tr key={model.key}>
                      <Td className="font-medium">{model.name}</Td>
                      <Td className="num">{toFaDigits(model.resolution)}</Td>
                      <Td>
                        <RawValue value={model.weights_filename} />
                      </Td>
                      <Td>
                        {model.available ? <Badge tone="ok">موجود</Badge> : <Badge tone="muted">دانلود نشده</Badge>}
                      </Td>
                      <Td className="max-w-[280px] text-[10px] text-muted">{model.description}</Td>
                    </Tr>
                  ))}
                </Tbody>
              </TableWrap>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="پردازنده گرافیکی" icon={<Cpu />} />
          <CardBody className="space-y-3">
            {device ? (
              <>
                <p className="truncate text-[11px] text-muted" title={device.name}>
                  {device.name}
                </p>
                <div>
                  <div className="mb-1 flex items-center justify-between text-[11px]">
                    <span className="text-muted">مصرف پردازنده</span>
                    <span className="num">{faNumber(device.utilization)}٪</span>
                  </div>
                  <Progress value={device.utilization} />
                </div>
                <div>
                  <div className="mb-1 flex items-center justify-between text-[11px]">
                    <span className="text-muted">حافظه گرافیکی</span>
                    <span className="num">
                      {faNumber(device.memory_used_mb / 1024, 1)} / {faNumber(device.memory_total_mb / 1024, 1)} GB
                    </span>
                  </div>
                  <Progress
                    value={device.memory_percent}
                    tone={device.memory_percent > 90 ? "danger" : device.memory_percent > 75 ? "warn" : "brand"}
                  />
                </div>
                <KeyValue label="دما" value={device.temperature_c !== null ? `${faNumber(device.temperature_c)}°` : "—"} />
                <KeyValue label="نرخ تشخیص" value={`${faNumber(device.detection_fps, 1)} fps`} />
                <KeyValue label="دوربین‌های فعال" value={toFaDigits(device.active_cameras)} />
                <KeyValue label="درایور" value={toFaDigits(gpu.data?.driver_version ?? "—")} />
                <KeyValue label="CUDA" value={toFaDigits(gpu.data?.cuda_version ?? "—")} />
                <KeyValue label="منبع داده" value={gpu.data?.source ?? "—"} />
              </>
            ) : (
              <EmptyState
                icon={<Cpu />}
                title="GPU در دسترس نیست"
                description="برای استفاده از تسریع گرافیکی، درایور NVIDIA و nvidia-smi باید نصب باشند."
              />
            )}
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="تنظیمات ظاهری و اعلان" subtitle="روی همین مرورگر ذخیره می‌شود" icon={<Bell />} />
          <CardBody className="space-y-3">
            <Field label="پوسته">
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant={theme === "dark" ? "primary" : "outline"}
                  onClick={() => setTheme("dark")}
                >
                  <Moon />
                  تاریک
                </Button>
                <Button
                  size="sm"
                  variant={theme === "light" ? "primary" : "outline"}
                  onClick={() => setTheme("light")}
                >
                  <Sun />
                  روشن
                </Button>
              </div>
            </Field>

            <Switch
              checked={overlayDefault}
              onChange={setOverlayDefault}
              label="نمایش پیش‌فرض لایه تشخیص"
              description="در حالت خام، تصویر دوربین بدون علامت‌گذاری پخش می‌شود."
            />

            <Switch
              checked={soundEnabled}
              onChange={setSound}
              label="پخش صدا هنگام هشدار"
              description="صدای هشدار به‌صورت محلی در مرورگر تولید می‌شود."
            />

            <Slider
              value={volume}
              onChange={setVolume}
              min={0}
              max={1}
              step={0.05}
              label="بلندی صدا"
              format={(value) => `${faNumber(value * 100, 0)}٪`}
            />

            <Field label="فاصله به‌روزرسانی خودکار صفحات (ثانیه)">
              <Select
                value={autoRefreshSeconds}
                onChange={(event) => setAutoRefresh(Number(event.target.value))}
              >
                {[5, 10, 15, 30, 60, 120].map((seconds) => (
                  <option key={seconds} value={seconds}>
                    {toFaDigits(seconds)} ثانیه
                  </option>
                ))}
              </Select>
            </Field>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="نگه‌داری داده‌ها و فضای ذخیره‌سازی" icon={<Database />} />
          <CardBody className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="مدت نگه‌داری (روز)">
                <Input
                  type="number"
                  min={1}
                  max={3650}
                  value={retentionDays}
                  onChange={(event) => setRetentionDays(Number(event.target.value))}
                />
              </Field>
              <Field label="حالت اجرا">
                <Select value={dryRun ? "yes" : "no"} onChange={(event) => setDryRun(event.target.value === "yes")}>
                  <option value="yes">آزمایشی (بدون حذف)</option>
                  <option value="no">حذف واقعی</option>
                </Select>
              </Field>
            </div>

            <Button
              size="sm"
              variant={dryRun ? "outline" : "danger"}
              loading={retention.isPending}
              onClick={async () => {
                try {
                  const report = await retention.mutateAsync({ days: retentionDays, dryRun });
                  toast.success("عملیات نگه‌داری اجرا شد", JSON.stringify(report));
                } catch (error) {
                  toast.error("اجرای نگه‌داری ناموفق بود", (error as Error).message);
                }
              }}
            >
              <Trash />
              اجرای پاک‌سازی
            </Button>

            <div className="rounded-xl border border-border bg-surface-2/60 p-3">
              <p className="mb-2 text-[11px] font-medium">مصرف فضا به تفکیک دوربین</p>
              {Object.keys(storage.data?.by_camera ?? {}).length === 0 ? (
                <p className="text-[10px] text-muted">داده‌ای برای نمایش نیست</p>
              ) : (
                Object.entries(storage.data?.by_camera ?? {})
                  .sort((a, b) => b[1] - a[1])
                  .slice(0, 8)
                  .map(([key, value]) => (
                    <KeyValue key={key} label={`دوربین #${toFaDigits(key)}`} value={formatBytes(value)} />
                  ))
              )}
            </div>

            <Button
              size="sm"
              variant="outline"
              loading={initDb.isPending}
              onClick={async () => {
                const result = await initDb.mutateAsync();
                toast.success(result.detail);
              }}
            >
              <Database />
              ایجاد/بررسی جداول پایگاه داده
            </Button>
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="تنظیمات ذخیره‌شده در پایگاه داده" icon={<Save />} />
          <CardBody className="space-y-3">
            {settings.isLoading ? (
              <LoadingBlock />
            ) : (settings.data ?? []).length === 0 ? (
              <EmptyState
                icon={<Save />}
                title="تنظیمی ثبت نشده است"
                description="تنظیمات سراسری سامانه در این بخش قابل ویرایش هستند."
              />
            ) : (
              (settings.data ?? []).map((entry) => (
                <div key={entry.key} className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="num text-[11px]">{entry.key}</span>
                    <span className="text-[10px] text-muted">{entry.description ?? ""}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Input
                      value={editing[entry.key] ?? ""}
                      onChange={(event) =>
                        setEditing((state) => ({ ...state, [entry.key]: event.target.value }))
                      }
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      loading={saveSetting.isPending}
                      onClick={async () => {
                        const raw = editing[entry.key];
                        let parsed: unknown = raw;
                        try {
                          parsed = JSON.parse(raw);
                        } catch {
                          parsed = raw;
                        }
                        try {
                          await saveSetting.mutateAsync({ key: entry.key, value: parsed });
                          toast.success("تنظیم ذخیره شد", entry.key);
                        } catch (error) {
                          toast.error("ذخیره ناموفق بود", (error as Error).message);
                        }
                      }}
                    >
                      <Save />
                      ذخیره
                    </Button>
                  </div>
                  <p className="text-[10px] text-muted">آخرین تغییر: {formatRelative(entry.updated_at)}</p>
                </div>
              ))
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="تلمتری درون‌پردازه‌ای" subtitle="وضعیت لحظه‌ای مدیر دوربین‌ها" icon={<Activity />} />
          <CardBody className="space-y-3">
            {runtime.isLoading ? (
              <LoadingBlock />
            ) : (
              <>
                <div className="grid gap-x-6 md:grid-cols-2">
                  {Object.entries((runtime.data?.manager as Record<string, unknown>) ?? {}).map(([key, value]) => (
                    <KeyValue key={key} label={key} value={<RawValue value={value} />} />
                  ))}
                </div>

                <div className="rounded-xl border border-border bg-surface-2/60 p-3">
                  <p className="mb-2 text-[11px] font-medium">پیش‌فرض‌های تشخیص</p>
                  {Object.entries((runtime.data?.detection_defaults as Record<string, unknown>) ?? {}).map(
                    ([key, value]) => (
                      <KeyValue key={key} label={key} value={<RawValue value={value} />} />
                    ),
                  )}
                </div>

                <div className="rounded-xl border border-border bg-surface-2/60 p-3">
                  <p className="mb-2 text-[11px] font-medium">دوربین‌ها</p>
                  {Object.entries((runtime.data?.cameras as Record<string, unknown>) ?? {}).length === 0 ? (
                    <p className="text-[10px] text-muted">هیچ استریم فعالی وجود ندارد</p>
                  ) : (
                    Object.entries((runtime.data?.cameras as Record<string, Record<string, unknown>>) ?? {}).map(
                      ([cameraId, health]) => (
                        <div key={cameraId} className="mb-2 last:mb-0">
                          <p className="num text-[10px] text-muted">دوربین #{toFaDigits(cameraId)}</p>
                          {Object.entries(health ?? {}).map(([key, value]) => (
                            <KeyValue key={key} label={key} value={<RawValue value={value} />} />
                          ))}
                        </div>
                      ),
                    )
                  )}
                </div>

                <Button size="sm" variant="ghost" onClick={() => void runtime.refetch()} loading={runtime.isFetching}>
                  <RefreshCw />
                  به‌روزرسانی تلمتری
                </Button>
              </>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

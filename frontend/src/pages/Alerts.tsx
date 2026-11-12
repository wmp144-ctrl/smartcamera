import { Bell, BellOff, CheckCheck, RefreshCw, Volume2, VolumeX, Zap } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { DateRangeBar } from "../components/DateRangeBar";
import { MultiChipGroup } from "../components/MultiChipGroup";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardBody, CardHeader, PageHeader } from "../components/ui/card";
import { Field, Input, Select, Switch } from "../components/ui/field";
import { EmptyState, LoadingBlock, Slider } from "../components/ui/misc";
import { Pager } from "../components/ui/table";
import { toast } from "../components/ui/toast";
import type { AlertItem } from "../lib/api";
import {
  useAcknowledgeAlert,
  useAcknowledgeAllAlerts,
  useAlerts,
  useCameras,
  useUnreadAlerts,
} from "../lib/queries";
import { formatJalaliDateTime } from "../lib/jalali";
import { playAlertSound, primeAudio, requestNotificationPermission } from "../lib/sound";
import { useDateRange } from "../lib/useDateRange";
import { classLabel, faNumber, formatRelative, severityTone, toFaDigits } from "../lib/utils";
import { useUI } from "../store/ui";

const SEVERITIES = ["info", "warning", "critical"];

export function AlertsPage() {
  const range = useDateRange("today");
  const [severities, setSeverities] = useState<string[]>([]);
  const [cameraIds, setCameraIds] = useState<number[]>([]);
  const [acknowledged, setAcknowledged] = useState<"all" | "yes" | "no">("no");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const cameras = useCameras();
  const unread = useUnreadAlerts(15_000);
  const alerts = useAlerts(
    {
      start: range.start,
      end: range.end,
      severity: severities.length > 0 ? severities : undefined,
      camera_id: cameraIds.length > 0 ? cameraIds : undefined,
      acknowledged: acknowledged === "all" ? undefined : acknowledged === "yes",
      page,
      page_size: pageSize,
    },
    20_000,
  );
  const acknowledge = useAcknowledgeAlert();
  const acknowledgeAll = useAcknowledgeAllAlerts();

  const soundEnabled = useUI((state) => state.soundEnabled);
  const setSound = useUI((state) => state.setSound);
  const volume = useUI((state) => state.volume);
  const setVolume = useUI((state) => state.setVolume);
  const desktopNotifications = useUI((state) => state.desktopNotifications);
  const setDesktopNotifications = useUI((state) => state.setDesktopNotifications);
  const notifications = useUI((state) => state.notifications);
  const clearNotifications = useUI((state) => state.clearNotifications);
  const setUnreadAlerts = useUI((state) => state.setUnreadAlerts);

  const cameraNames = useMemo(
    () => new Map((cameras.data ?? []).map((camera) => [camera.id, camera.name])),
    [cameras.data],
  );

  useEffect(() => {
    if (unread.data) setUnreadAlerts(unread.data.unread);
  }, [unread.data, setUnreadAlerts]);

  return (
    <div className="space-y-5">
      <PageHeader
        title="هشدارها"
        subtitle="هشدارهای قواعد (زمان و ناحیه) به همراه اعلان درون‌برنامه‌ای و صدا"
        icon={<Bell />}
        actions={
          <>
            <Badge tone={(unread.data?.unread ?? 0) > 0 ? "danger" : "ok"}>
              <span className="num">{toFaDigits(unread.data?.unread ?? 0)}</span> هشدار تأییدنشده
            </Badge>
            <Button variant="outline" size="sm" onClick={() => void alerts.refetch()} loading={alerts.isFetching}>
              <RefreshCw />
              به‌روزرسانی
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={acknowledgeAll.isPending}
              onClick={async () => {
                const result = await acknowledgeAll.mutateAsync();
                toast.success(result.detail);
              }}
            >
              <CheckCheck />
              تأیید همه
            </Button>
          </>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-4">
          <Card>
            <CardHeader title="فیلترها" icon={<BellOff />} />
            <CardBody className="space-y-4">
              <DateRangeBar range={range} />
              <div className="grid gap-4 md:grid-cols-2">
                <MultiChipGroup
                  label="شدت"
                  options={SEVERITIES.map((severity) => ({
                    value: severity,
                    label: severity === "critical" ? "بحرانی" : severity === "warning" ? "هشدار" : "اطلاع",
                  }))}
                  selected={severities}
                  onChange={(values) => {
                    setSeverities(values);
                    setPage(1);
                  }}
                />
                <MultiChipGroup
                  label="دوربین"
                  options={(cameras.data ?? []).map((camera) => ({ value: camera.id, label: camera.name }))}
                  selected={cameraIds}
                  onChange={(values) => {
                    setCameraIds(values);
                    setPage(1);
                  }}
                />
              </div>
              <Field label="وضعیت تأیید" className="max-w-xs">
                <Select
                  value={acknowledged}
                  onChange={(event) => {
                    setAcknowledged(event.target.value as "all" | "yes" | "no");
                    setPage(1);
                  }}
                >
                  <option value="no">تأییدنشده</option>
                  <option value="all">همه</option>
                  <option value="yes">تأییدشده</option>
                </Select>
              </Field>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="فهرست هشدارها"
              subtitle="هر هشدار شامل زمان وقوع (شمسی) و پیام قاعده است"
              icon={<Bell />}
            />
            <CardBody className="p-0">
              {alerts.isLoading ? (
                <LoadingBlock />
              ) : (alerts.data?.items ?? []).length === 0 ? (
                <EmptyState
                  icon={<BellOff />}
                  title="هشداری در این بازه وجود ندارد"
                  description="برای دریافت هشدار، در تب «نواحی و خطوط» هر دوربین ناحیه یا خط هشدار تعریف کنید."
                />
              ) : (
                (alerts.data?.items ?? []).map((alert) => <AlertRow key={alert.id} alert={alert} cameraNames={cameraNames} onAcknowledge={acknowledge.mutate} />)
              )}
            </CardBody>
            {(alerts.data?.total ?? 0) > 0 ? (
              <Pager
                page={alerts.data?.page ?? page}
                pageSize={alerts.data?.page_size ?? pageSize}
                total={alerts.data?.total ?? 0}
                onPage={setPage}
                onPageSize={(size) => {
                  setPageSize(size);
                  setPage(1);
                }}
              />
            ) : null}
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader title="تنظیمات اعلان" subtitle="صدا و اعلان دسکتاپ" icon={<Volume2 />} />
            <CardBody className="space-y-3">
              <Switch
                checked={soundEnabled}
                onChange={(value) => {
                  primeAudio();
                  setSound(value);
                  if (value) playAlertSound("info", volume);
                }}
                label="پخش صدا هنگام هشدار"
                description="صدای هشدار به‌صورت محلی تولید می‌شود و به فایل صوتی نیاز ندارد."
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
              <Switch
                checked={desktopNotifications}
                onChange={async (value) => {
                  if (value) {
                    const granted = await requestNotificationPermission();
                    setDesktopNotifications(granted);
                    if (!granted) toast.warn("اجازه اعلان دسکتاپ داده نشد");
                  } else {
                    setDesktopNotifications(false);
                  }
                }}
                label="اعلان دسکتاپ"
                description="نمایش هشدار در ناحیه اعلان ویندوز (نیازمند اجازه مرورگر)."
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    primeAudio();
                    playAlertSound("critical", volume);
                  }}
                >
                  <Zap />
                  آزمایش صدای بحرانی
                </Button>
                <Button size="sm" variant="ghost" onClick={clearNotifications}>
                  پاک کردن اعلان‌های زنده
                </Button>
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="اعلان‌های زنده این نشست"
              subtitle="هشدارهایی که از طریق WebSocket دریافت شده‌اند"
              icon={<Bell />}
              actions={<Badge tone="muted">{toFaDigits(notifications.length)}</Badge>}
            />
            <CardBody className="p-0">
              {notifications.length === 0 ? (
                <EmptyState
                  icon={soundEnabled ? <Volume2 /> : <VolumeX />}
                  title="اعلان زنده‌ای ثبت نشده"
                  description="با وقوع هشدار جدید، اعلان بی‌درنگ اینجا نمایش داده می‌شود."
                />
              ) : (
                <ul className="divide-y divide-border/60">
                  {notifications.slice(0, 12).map((item) => (
                    <li key={item.id} className="px-3 py-2.5">
                      <div className="flex items-center gap-2">
                        <Badge tone={severityTone(item.severity)}>
                          {item.severity === "critical" ? "بحرانی" : item.severity === "warning" ? "هشدار" : "اطلاع"}
                        </Badge>
                        <span className="text-[11px] text-muted">{formatRelative(item.at)}</span>
                      </div>
                      <p className="mt-1 text-[11px] font-medium">{item.title}</p>
                      <p className="mt-0.5 text-[11px] leading-5 text-muted">{item.message}</p>
                      {item.cameraName ? (
                        <p className="mt-1 text-[11px] text-muted">دوربین: {item.cameraName}</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="جست‌وجوی سریع بازه" icon={<RefreshCw />} />
            <CardBody>
              <Field label="تاریخ شمسی (نمونه)">
                <Input
                  readOnly
                  value="۱۴۰۵/۰۷/۱۳"
                  className="num text-center"
                  title="نمونه قالب تاریخ شمسی"
                />
              </Field>
              <p className="mt-2 text-[11px] leading-6 text-muted">
                همه زمان‌ها در پایگاه داده به وقت UTC ذخیره و در رابط کاربری به وقت محلی و تقویم شمسی نمایش داده
                می‌شوند. منطقه زمانی پیش‌فرض: Asia/Tehran.
              </p>
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}

function AlertRow({
  alert,
  cameraNames,
  onAcknowledge,
}: {
  alert: AlertItem;
  cameraNames: Map<number, string>;
  onAcknowledge: (id: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-start gap-3 border-b border-border/60 px-4 py-3 last:border-0">
      <Badge tone={severityTone(alert.severity)}>
        {alert.severity === "critical" ? "بحرانی" : alert.severity === "warning" ? "هشدار" : "اطلاع"}
      </Badge>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium">{alert.title}</p>
        <p className="mt-1 text-[11px] leading-6 text-muted">{alert.message}</p>
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-muted">
          <span>{cameraNames.get(alert.camera_id) ?? `#${alert.camera_id}`}</span>
          <span className="text-muted">•</span>
          <span>{formatJalaliDateTime(alert.triggered_at)}</span>
          <span className="text-muted">•</span>
          <span>{formatRelative(alert.triggered_at)}</span>
          <span className="text-muted">•</span>
          <span>قاعده: {alert.rule_name}</span>
          {alert.object_type ? <span>— {classLabel(alert.object_type)}</span> : null}
          {alert.sound ? <Badge tone="warn">صدا</Badge> : null}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {alert.acknowledged ? (
          <Badge tone="ok">تأییدشده</Badge>
        ) : (
          <Button size="xs" variant="outline" onClick={() => onAcknowledge(alert.id)}>
            <CheckCheck />
            تأیید
          </Button>
        )}
      </div>
    </div>
  );
}

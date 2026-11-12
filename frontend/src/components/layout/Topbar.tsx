import { Activity, Bell, Cpu, Menu, Moon, Sun, Volume2, VolumeX, Wifi, WifiOff } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useGPU } from "../../lib/queries";
import { faNumber, formatRelative, severityTone, toFaDigits } from "../../lib/utils";
import {
  JALALI_WEEKDAYS,
  formatJalaliDate,
  gregorianToJalali,
  jalaliWeekday,
} from "../../lib/jalali";
import { playAlertSound, primeAudio, requestNotificationPermission } from "../../lib/sound";
import { useLive } from "../../store/live";
import { useUI } from "../../store/ui";
import { Badge, StatusDot } from "../ui/badge";
import { Button } from "../ui/button";

function JalaliClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const jalali = gregorianToJalali(now.getFullYear(), now.getMonth() + 1, now.getDate());
  const weekday = JALALI_WEEKDAYS[jalaliWeekday(jalali.year, jalali.month, jalali.day)];

  return (
    <div className="hidden flex-col items-end leading-tight lg:flex">
      <span className="num text-xs font-semibold">
        {toFaDigits(
          `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(
            now.getSeconds(),
          ).padStart(2, "0")}`,
        )}
      </span>
      <span className="text-[10px] text-muted">
        {weekday} — {formatJalaliDate(jalali)}
      </span>
    </div>
  );
}

function GpuMini() {
  const gpu = useGPU(6000);
  const device = gpu.data?.devices?.[0];
  if (!gpu.data?.available || !device) {
    return (
      <Badge tone="muted" title="GPU در دسترس نیست">
        <Cpu className="size-3" />
        CPU
      </Badge>
    );
  }
  const tone = device.temperature_c && device.temperature_c > 80 ? "danger" : "ok";
  return (
    <Badge tone={tone} title={`${device.name} — ${faNumber(device.memory_used_mb / 1024, 1)} گیگابایت`}>
      <Cpu className="size-3" />
      <span className="num">{faNumber(device.utilization)}%</span>
      {device.temperature_c !== null ? (
        <span className="num">{faNumber(device.temperature_c)}°</span>
      ) : null}
    </Badge>
  );
}

function ConnectionPill() {
  const connection = useLive((state) => state.connection);
  const lastMessageAt = useLive((state) => state.lastMessageAt);
  const tone = connection === "open" ? "ok" : connection === "connecting" ? "warn" : "danger";
  const label = connection === "open" ? "زنده" : connection === "connecting" ? "در حال اتصال" : "قطع";
  return (
    <span
      className="hidden items-center gap-2 rounded-full border border-border bg-surface-2/60 px-2.5 py-1 text-[10px] text-muted sm:inline-flex"
      title={lastMessageAt ? `آخرین پیام ${formatRelative(lastMessageAt)}` : "هنوز پیامی دریافت نشده"}
    >
      <StatusDot tone={tone} pulse={connection === "open"} />
      {label}
    </span>
  );
}

function NotificationBell() {
  const [open, setOpen] = useState(false);
  const notifications = useUI((state) => state.notifications);
  const clear = useUI((state) => state.clearNotifications);
  const dismiss = useUI((state) => state.dismissNotification);
  const soundEnabled = useUI((state) => state.soundEnabled);
  const setSound = useUI((state) => state.setSound);
  const desktopNotifications = useUI((state) => state.desktopNotifications);
  const setDesktopNotifications = useUI((state) => state.setDesktopNotifications);
  const volume = useUI((state) => state.volume);

  const unread = notifications.length;

  return (
    <div className="relative">
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="اعلان‌ها"
        onClick={() => setOpen((state) => !state)}
        className={unread > 0 ? "text-warn" : undefined}
      >
        <Bell />
        {unread > 0 ? (
          <span className="absolute -left-0.5 -top-0.5 grid size-4 place-items-center rounded-full bg-danger text-[9px] font-bold text-white">
            {unread > 9 ? "۹+" : toFaDigits(unread)}
          </span>
        ) : null}
      </Button>

      {open ? (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden />
          <div className="panel animate-fade-up absolute left-0 top-[calc(100%+8px)] z-50 w-80 overflow-hidden shadow-2xl">
            <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
              <span className="text-xs font-semibold">اعلان‌های زنده</span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  title={soundEnabled ? "صدا روشن" : "صدا خاموش"}
                  onClick={() => {
                    primeAudio();
                    setSound(!soundEnabled);
                    if (!soundEnabled) playAlertSound("info", volume);
                  }}
                  className="grid size-7 place-items-center rounded-lg text-muted hover:bg-surface-2 hover:text-fg"
                >
                  {soundEnabled ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
                </button>
                <button
                  type="button"
                  title="اعلان دسکتاپ"
                  onClick={async () => {
                    const granted = await requestNotificationPermission();
                    setDesktopNotifications(granted);
                  }}
                  className={`grid size-7 place-items-center rounded-lg hover:bg-surface-2 ${
                    desktopNotifications ? "text-ok" : "text-muted hover:text-fg"
                  }`}
                >
                  <Activity className="size-4" />
                </button>
                <button
                  type="button"
                  onClick={clear}
                  className="rounded-lg px-2 py-1 text-[10px] text-muted hover:bg-surface-2 hover:text-fg"
                >
                  پاک کردن
                </button>
              </div>
            </div>

            <div className="max-h-80 overflow-y-auto">
              {notifications.length === 0 ? (
                <p className="px-3 py-8 text-center text-[11px] text-muted">
                  هنوز اعلانی دریافت نشده است
                </p>
              ) : (
                notifications.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-start gap-2 border-b border-border/60 px-3 py-2.5 last:border-0"
                  >
                    <Badge tone={severityTone(item.severity)} className="mt-0.5 shrink-0">
                      {item.kind === "alert" ? "هشدار" : "رویداد"}
                    </Badge>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[11px] font-medium">{item.title}</p>
                      <p className="mt-0.5 line-clamp-2 text-[10px] leading-5 text-muted">{item.message}</p>
                      <p className="mt-1 text-[9px] text-muted">
                        {item.cameraName ? `${item.cameraName} — ` : ""}
                        {formatRelative(item.at)}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => dismiss(item.id)}
                      className="text-[10px] text-muted hover:text-danger"
                    >
                      حذف
                    </button>
                  </div>
                ))
              )}
            </div>

            <Link
              to="/alerts"
              onClick={() => setOpen(false)}
              className="block border-t border-border px-3 py-2.5 text-center text-[11px] text-brand hover:bg-surface-2"
            >
              مشاهده همه هشدارها
            </Link>
          </div>
        </>
      ) : null}
    </div>
  );
}

export function Topbar({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  const theme = useUI((state) => state.theme);
  const toggleTheme = useUI((state) => state.toggleTheme);
  const soundEnabled = useUI((state) => state.soundEnabled);
  const setSound = useUI((state) => state.setSound);
  const volume = useUI((state) => state.volume);
  const setVolume = useUI((state) => state.setVolume);
  const connection = useLive((state) => state.connection);

  const connectionIcon = useMemo(
    () => (connection === "open" ? <Wifi className="size-3" /> : <WifiOff className="size-3" />),
    [connection],
  );

  return (
    <header className="glass sticky top-0 z-30 flex h-16 items-center gap-3 border-b px-4">
      <Button variant="ghost" size="icon-sm" aria-label="نمایش/مخفی نوار کناری" onClick={onToggleSidebar}>
        <Menu />
      </Button>

      <div className="flex items-center gap-2">
        <ConnectionPill />
        <span className="hidden sm:inline-flex">{connectionIcon}</span>
        <GpuMini />
      </div>

      <div className="flex-1" />

      <JalaliClock />

      <div className="flex items-center gap-1">
        <div className="hidden items-center gap-2 rounded-full border border-border bg-surface-2/60 px-2.5 py-1 md:flex">
          <button
            type="button"
            onClick={() => {
              primeAudio();
              setSound(!soundEnabled);
            }}
            title={soundEnabled ? "خاموش کردن صدا" : "روشن کردن صدا"}
            className="text-muted hover:text-fg"
          >
            {soundEnabled ? <Volume2 className="size-3.5" /> : <VolumeX className="size-3.5" />}
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={volume}
            onChange={(event) => setVolume(Number(event.target.value))}
            aria-label="بلندی صدا"
            className="h-1 w-16 cursor-pointer appearance-none rounded-full bg-surface-3 accent-[var(--brand)] [&::-webkit-slider-thumb]:size-3 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-brand"
          />
        </div>

        <NotificationBell />

        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="تغییر پوسته"
          onClick={() => {
            toggleTheme();
          }}
        >
          {theme === "dark" ? <Sun /> : <Moon />}
        </Button>
      </div>
    </header>
  );
}

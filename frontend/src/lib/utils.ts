import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

const FA_DIGITS = ["۰", "۱", "۲", "۳", "۴", "۵", "۶", "۷", "۸", "۹"];

export function toFaDigits(value: string | number): string {
  return String(value).replace(/\d/g, (digit) => FA_DIGITS[Number(digit)]);
}

export function faNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return toFaDigits(value.toFixed(digits));
}

export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (!bytes || bytes <= 0) return "۰ ب";
  const units = ["ب", "کیلوبایت", "مگابایت", "گیگابایت", "ترابایت"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** index;
  return `${faNumber(value, index === 0 ? 0 : digits)} ${units[index]}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes >= 60) {
    const hours = Math.floor(minutes / 60);
    return `${faNumber(hours)} ساعت و ${faNumber(minutes % 60)} دقیقه`;
  }
  if (minutes > 0) return `${faNumber(minutes)} دقیقه و ${faNumber(rest)} ثانیه`;
  return `${faNumber(rest)} ثانیه`;
}

export function formatRelative(iso: string): string {
  const date = new Date(iso.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`);
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 10) return "همین حالا";
  if (diff < 60) return `${faNumber(Math.round(diff))} ثانیه پیش`;
  if (diff < 3600) return `${faNumber(Math.round(diff / 60))} دقیقه پیش`;
  if (diff < 86400) return `${faNumber(Math.round(diff / 3600))} ساعت پیش`;
  return `${faNumber(Math.round(diff / 86400))} روز پیش`;
}

export const CLASS_LABELS_FA: Record<string, string> = {
  person: "انسان",
  bicycle: "دوچرخه",
  car: "خودرو",
  motorcycle: "موتورسیکلت",
  airplane: "هواپیما",
  bus: "اتوبوس",
  train: "قطار",
  truck: "کامیون",
  boat: "قایق",
  "traffic light": "چراغ راهنما",
  "fire hydrant": "شیر آتش‌نشانی",
  "stop sign": "تابلوی ایست",
  bench: "نیمکت",
  bird: "پرنده",
  cat: "گربه",
  dog: "سگ",
  horse: "اسب",
  sheep: "گوسفند",
  cow: "گاو",
  backpack: "کوله‌پشتی",
  umbrella: "چتر",
  handbag: "کیف دستی",
  suitcase: "چمدان",
  bottle: "بطری",
  chair: "صندلی",
  couch: "مبل",
  "potted plant": "گیاه گلدانی",
  bed: "تخت",
  tv: "تلویزیون",
  laptop: "لپ‌تاپ",
  "cell phone": "تلفن همراه",
  book: "کتاب",
  clock: "ساعت",
};

export function classLabel(name: string | null | undefined): string {
  if (!name) return "—";
  return CLASS_LABELS_FA[name] ?? name;
}

export const DIRECTION_LABELS_FA: Record<string, string> = {
  stationary: "ثابت",
  left: "چپ",
  right: "راست",
  up: "بالا",
  down: "پایین",
  "up-left": "بالا-چپ",
  "up-right": "بالا-راست",
  "down-left": "پایین-چپ",
  "down-right": "پایین-راست",
};

export function directionLabel(direction: string | null | undefined): string {
  if (!direction) return "—";
  return DIRECTION_LABELS_FA[direction] ?? direction;
}

export const DIRECTION_ARROWS: Record<string, string> = {
  stationary: "•",
  left: "←",
  right: "→",
  up: "↑",
  down: "↓",
  "up-left": "↖",
  "up-right": "↗",
  "down-left": "↙",
  "down-right": "↘",
};

export const EVENT_TYPE_LABELS_FA: Record<string, string> = {
  enter: "ورود",
  exit: "خروج",
  stationary: "توقف",
  moving: "شروع حرکت",
  line_cross: "عبور از خط",
  motion: "حرکت",
  alert: "هشدار",
  detection: "تشخیص",
};

export function eventTypeLabel(type: string | null | undefined): string {
  if (!type) return "—";
  return EVENT_TYPE_LABELS_FA[type] ?? type;
}

export const CAMERA_STATE_LABELS_FA: Record<string, string> = {
  online: "آنلاین",
  offline: "آفلاین",
  reconnecting: "در حال اتصال",
  error: "خطا",
  disabled: "غیرفعال",
  unknown: "نامشخص",
};

export function cameraStateLabel(state: string | null | undefined): string {
  if (!state) return "نامشخص";
  return CAMERA_STATE_LABELS_FA[state] ?? state;
}

export function stateTone(state: string | null | undefined): "ok" | "warn" | "danger" | "muted" {
  switch (state) {
    case "online":
      return "ok";
    case "reconnecting":
      return "warn";
    case "error":
    case "offline":
      return "danger";
    default:
      return "muted";
  }
}

export function severityTone(severity: string | null | undefined): "ok" | "warn" | "danger" | "muted" {
  switch (severity) {
    case "critical":
      return "danger";
    case "warning":
      return "warn";
    case "info":
      return "ok";
    default:
      return "muted";
  }
}

export function debounce<Args extends unknown[]>(fn: (...args: Args) => void, delay = 300) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return (...args: Args) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

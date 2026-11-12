/**
 * Jalali (Solar Hijri) calendar helpers.
 *
 * Port of the canonical `jalaali-js` algorithm — the exact same one the backend
 * uses — so dates rendered here can never disagree with the API.
 * Everything here is pure and side-effect free.
 */

export const JALALI_MONTHS = [
  "فروردین",
  "اردیبهشت",
  "خرداد",
  "تیر",
  "مرداد",
  "شهریور",
  "مهر",
  "آبان",
  "آذر",
  "دی",
  "بهمن",
  "اسفند",
] as const;

export const JALALI_MONTHS_SHORT = [
  "فرو",
  "ارد",
  "خرد",
  "تیر",
  "مرد",
  "شهر",
  "مهر",
  "آبا",
  "آذر",
  "دی",
  "بهم",
  "اسف",
] as const;

/** Persian week starts on Saturday. Index 0 = شنبه … 6 = جمعه */
export const JALALI_WEEKDAYS = [
  "شنبه",
  "یک‌شنبه",
  "دوشنبه",
  "سه‌شنبه",
  "چهارشنبه",
  "پنج‌شنبه",
  "جمعه",
] as const;

export const JALALI_WEEKDAYS_SHORT = ["ش", "ی", "د", "س", "چ", "پ", "ج"] as const;

export interface JalaliDate {
  year: number;
  month: number; // 1..12
  day: number; // 1..31
}

const div = (a: number, b: number) => Math.floor(a / b);

export function gregorianToJalali(gy: number, gm: number, gd: number): JalaliDate {
  const gDaysInMonth = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
  const gy2 = gm > 2 ? gy + 1 : gy;
  let days =
    355666 +
    365 * gy +
    div(gy2 + 3, 4) -
    div(gy2 + 99, 100) +
    div(gy2 + 399, 400) +
    gd +
    gDaysInMonth[gm - 1];
  let jy = -1595 + 33 * div(days, 12053);
  days %= 12053;
  jy += 4 * div(days, 1461);
  days %= 1461;
  if (days > 365) {
    jy += div(days - 1, 365);
    days = (days - 1) % 365;
  }
  let jm: number;
  let jd: number;
  if (days < 186) {
    jm = 1 + div(days, 31);
    jd = 1 + (days % 31);
  } else {
    jm = 7 + div(days - 186, 30);
    jd = 1 + ((days - 186) % 30);
  }
  return { year: jy, month: jm, day: jd };
}

export function jalaliToGregorian(jy: number, jm: number, jd: number) {
  let year = jy + 1595;
  let days =
    -355668 + 365 * year + div(year, 33) * 8 + div((year % 33) + 3, 4) + jd;
  days += jm < 7 ? (jm - 1) * 31 : (jm - 7) * 30 + 186;
  let gy = 400 * div(days, 146097);
  days %= 146097;
  if (days > 36524) {
    days -= 1;
    gy += 100 * div(days, 36524);
    days %= 36524;
    if (days >= 365) days += 1;
  }
  gy += 4 * div(days, 1461);
  days %= 1461;
  if (days > 365) {
    gy += div(days - 1, 365);
    days = (days - 1) % 365;
  }
  let gd = days + 1;
  const leap = (gy % 4 === 0 && gy % 100 !== 0) || gy % 400 === 0;
  const monthDays = [0, 31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  let gm = 0;
  while (gm < 13 && gd > monthDays[gm]) {
    gd -= monthDays[gm];
    gm += 1;
  }
  return { year: gy, month: gm, day: gd };
}

/** Number of days in a Jalali month (1..12). */
export function jalaliMonthLength(year: number, month: number): number {
  if (month <= 6) return 31;
  if (month <= 11) return 30;
  return isJalaliLeapYear(year) ? 30 : 29;
}

export function isJalaliLeapYear(year: number): boolean {
  // 33-year cycle approximation used by the jalaali algorithm
  return ((year + 12) % 33) % 4 === 1;
}

/** Weekday index (0 = شنبه) for a Jalali date. */
export function jalaliWeekday(year: number, month: number, day: number): number {
  const g = jalaliToGregorian(year, month, day);
  const date = new Date(Date.UTC(g.year, g.month - 1, g.day));
  // JS: Sunday = 0 -> shift so Saturday = 0
  return (date.getUTCDay() + 1) % 7;
}

export function todayJalali(): JalaliDate {
  const now = new Date();
  return gregorianToJalali(now.getFullYear(), now.getMonth() + 1, now.getDate());
}

const pad = (value: number) => String(value).padStart(2, "0");

/** ``1405/07/13`` */
export function formatJalaliDate(input: Date | string | JalaliDate): string {
  const { year, month, day } = toJalali(input);
  return `${year}/${pad(month)}/${pad(day)}`;
}

/** ``13 مهر 1405`` */
export function formatJalaliLong(input: Date | string | JalaliDate): string {
  const { year, month, day } = toJalali(input);
  return `${day} ${JALALI_MONTHS[month - 1]} ${year}`;
}

/** ``1405/07/13 - 11:30:05`` (local time) */
export function formatJalaliDateTime(input: Date | string | JalaliDate): string {
  if (typeof input === "string" || input instanceof Date) {
    const date = typeof input === "string" ? parseApiDate(input) : input;
    const j = gregorianToJalali(date.getFullYear(), date.getMonth() + 1, date.getDate());
    return `${formatJalaliDate(j)} - ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(
      date.getSeconds(),
    )}`;
  }
  return formatJalaliDate(input);
}

/** ``11:30`` */
export function formatClock(input: Date | string): string {
  const date = typeof input === "string" ? parseApiDate(input) : input;
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function parseApiDate(value: string): Date {
  // The API always returns UTC ISO strings; make sure browsers that dislike the
  // trailing "Z" (or "+00:00") still parse them as UTC.
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(value)) {
    return new Date(`${value}Z`);
  }
  return new Date(value);
}

/** Local `Date` at 00:00 of a Jalali day (Gregorian equivalent under the hood). */
export function jalaliToLocalDate(date: JalaliDate, hour = 0, minute = 0): Date {
  const g = jalaliToGregorian(date.year, date.month, date.day);
  return new Date(g.year, g.month - 1, g.day, hour, minute, 0, 0);
}

export function toJalali(input: Date | string | JalaliDate): JalaliDate {
  if (typeof input === "object" && "year" in input && "month" in input && "day" in input) {
    return input as JalaliDate;
  }
  const date = typeof input === "string" ? parseApiDate(input) : input;
  return gregorianToJalali(date.getFullYear(), date.getMonth() + 1, date.getDate());
}

/**
 * UTC ISO bounds of a Jalali day, ready to send to the API.
 *
 * The Jalali day is first mapped to its Gregorian local date, then local
 * midnight is what gets converted to a UTC instant — this is exactly the
 * window the backend uses for "today" in the display timezone.
 */
export function jalaliDayBounds(date: JalaliDate): { start: string; end: string } {
  const start = jalaliToLocalDate(date, 0, 0);
  const g = jalaliToGregorian(date.year, date.month, date.day);
  const end = new Date(g.year, g.month - 1, g.day + 1, 0, 0, 0, 0);
  return { start: start.toISOString(), end: end.toISOString() };
}

/** Build the week grid (6 rows × 7 columns) for a Jalali month. */
export function jalaliMonthGrid(year: number, month: number): (JalaliDate | null)[][] {
  const length = jalaliMonthLength(year, month);
  const offset = jalaliWeekday(year, month, 1);
  const cells: (JalaliDate | null)[] = Array.from({ length: offset }, () => null);
  for (let day = 1; day <= length; day += 1) cells.push({ year, month, day });
  while (cells.length % 7 !== 0) cells.push(null);
  while (cells.length < 42) cells.push(null);

  const weeks: (JalaliDate | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

export function shiftJalaliMonth(date: JalaliDate, delta: number): JalaliDate {
  let month = date.month + delta;
  let year = date.year;
  while (month > 12) {
    month -= 12;
    year += 1;
  }
  while (month < 1) {
    month += 12;
    year -= 1;
  }
  const maxDay = jalaliMonthLength(year, month);
  return { year, month, day: Math.min(date.day, maxDay) };
}

export function sameJalaliDay(a: JalaliDate | null, b: JalaliDate | null): boolean {
  if (!a || !b) return false;
  return a.year === b.year && a.month === b.month && a.day === b.day;
}

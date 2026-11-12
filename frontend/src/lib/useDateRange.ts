/**
 * Date-range state expressed in the Jalali calendar.
 *
 * Components work with Jalali dates because that is what the operator picks;
 * the API always receives UTC ISO bounds. This hook is the only place that
 * translates between the two.
 */

import { useCallback, useMemo, useState } from "react";
import {
  formatJalaliDate,
  gregorianToJalali,
  jalaliDayBounds,
  jalaliToLocalDate,
  todayJalali,
  type JalaliDate,
} from "./jalali";

export type RangePreset = "today" | "yesterday" | "last7" | "last30" | "thisMonth" | "custom";

export const PRESET_LABELS: Record<RangePreset, string> = {
  today: "امروز",
  yesterday: "دیروز",
  last7: "۷ روز اخیر",
  last30: "۳۰ روز اخیر",
  thisMonth: "این ماه",
  custom: "بازه دلخواه",
};

function shiftDays(date: JalaliDate, days: number): JalaliDate {
  const base = jalaliToLocalDate(date, 0, 0);
  const shifted = new Date(base.getFullYear(), base.getMonth(), base.getDate() + days);
  return gregorianToJalali(shifted.getFullYear(), shifted.getMonth() + 1, shifted.getDate());
}

function presetRange(preset: RangePreset): { from: JalaliDate; to: JalaliDate } {
  const today = todayJalali();
  switch (preset) {
    case "yesterday": {
      const day = shiftDays(today, -1);
      return { from: day, to: day };
    }
    case "last7":
      return { from: shiftDays(today, -6), to: today };
    case "last30":
      return { from: shiftDays(today, -29), to: today };
    case "thisMonth":
      return { from: { year: today.year, month: today.month, day: 1 }, to: today };
    case "today":
    default:
      return { from: today, to: today };
  }
}

export interface UseDateRange {
  preset: RangePreset;
  from: JalaliDate;
  to: JalaliDate;
  start: string;
  end: string;
  label: string;
  setPreset: (preset: RangePreset) => void;
  setFrom: (date: JalaliDate | null) => void;
  setTo: (date: JalaliDate | null) => void;
  setExplicit: (from: JalaliDate | null, to: JalaliDate | null) => void;
  reset: () => void;
}

export function useDateRange(initial: RangePreset = "today"): UseDateRange {
  const [preset, setPresetState] = useState<RangePreset>(initial);
  const initialRange = useMemo(() => presetRange(initial), [initial]);
  const [from, setFromState] = useState<JalaliDate>(initialRange.from);
  const [to, setToState] = useState<JalaliDate>(initialRange.to);

  const setPreset = useCallback((next: RangePreset) => {
    setPresetState(next);
    if (next !== "custom") {
      const range = presetRange(next);
      setFromState(range.from);
      setToState(range.to);
    }
  }, []);

  const setFrom = useCallback((date: JalaliDate | null) => {
    if (!date) return;
    setFromState(date);
    setPresetState("custom");
  }, []);

  const setTo = useCallback((date: JalaliDate | null) => {
    if (!date) return;
    setToState(date);
    setPresetState("custom");
  }, []);

  const setExplicit = useCallback((nextFrom: JalaliDate | null, nextTo: JalaliDate | null) => {
    if (nextFrom) setFromState(nextFrom);
    if (nextTo) setToState(nextTo);
    setPresetState("custom");
  }, []);

  const reset = useCallback(() => setPreset("today"), [setPreset]);

  return useMemo(() => {
    // Guard against a reversed selection: normalise so start <= end.
    const startBounds = jalaliDayBounds(from);
    const endBounds = jalaliDayBounds(to);
    const ordered = startBounds.start <= endBounds.start;
    const start = ordered ? startBounds.start : endBounds.start;
    const end = ordered ? endBounds.end : startBounds.end;
    const label =
      formatJalaliDate(from) === formatJalaliDate(to)
        ? formatJalaliDate(from)
        : `${formatJalaliDate(ordered ? from : to)} تا ${formatJalaliDate(ordered ? to : from)}`;
    return { preset, from, to, start, end, label, setPreset, setFrom, setTo, setExplicit, reset };
  }, [preset, from, to, setPreset, setFrom, setTo, setExplicit, reset]);
}

/** A relative window (e.g. "last 24 hours") as UTC ISO bounds. */
export function hoursAgoBounds(hours: number): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end.getTime() - hours * 60 * 60 * 1000);
  return { start: start.toISOString(), end: end.toISOString() };
}

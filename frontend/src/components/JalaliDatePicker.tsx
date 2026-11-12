import { CalendarDays, ChevronLeft, ChevronRight, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  JALALI_MONTHS,
  JALALI_WEEKDAYS_SHORT,
  formatJalaliLong,
  jalaliMonthGrid,
  sameJalaliDay,
  shiftJalaliMonth,
  todayJalali,
  type JalaliDate,
} from "../lib/jalali";
import { cn, faNumber, toFaDigits } from "../lib/utils";

export function JalaliDatePicker({
  value,
  onChange,
  placeholder = "انتخاب تاریخ",
  clearable = true,
  className,
  align = "right",
}: {
  value: JalaliDate | null;
  onChange: (value: JalaliDate | null) => void;
  placeholder?: string;
  clearable?: boolean;
  className?: string;
  align?: "right" | "left";
}) {
  const [open, setOpen] = useState(false);
  const today = useMemo(() => todayJalali(), []);
  const [cursor, setCursor] = useState<JalaliDate>(value ?? today);
  const wrapper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) setCursor(value ?? today);
  }, [open, value, today]);

  useEffect(() => {
    if (!open) return;
    const handler = (event: MouseEvent) => {
      if (wrapper.current && !wrapper.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const weeks = useMemo(() => jalaliMonthGrid(cursor.year, cursor.month), [cursor]);

  return (
    <div ref={wrapper} className={cn("relative", className)}>
      <button
        type="button"
        onClick={() => setOpen((state) => !state)}
        className={cn(
          "flex h-10 w-full items-center gap-2 rounded-xl border border-border bg-surface-2/80 px-3 text-xs transition-colors hover:border-brand/50",
          open && "border-brand/60",
        )}
      >
        <CalendarDays className="size-4 text-brand" />
        <span className={cn("flex-1 text-right", !value && "text-muted")}>
          {value ? formatJalaliLong(value) : placeholder}
        </span>
        {clearable && value ? (
          <span
            role="button"
            tabIndex={0}
            aria-label="پاک کردن تاریخ"
            onClick={(event) => {
              event.stopPropagation();
              onChange(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.stopPropagation();
                onChange(null);
              }
            }}
            className="grid size-5 place-items-center rounded text-muted hover:bg-surface-3 hover:text-danger"
          >
            <X className="size-3.5" />
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          className={cn(
            "panel animate-fade-up absolute top-[calc(100%+6px)] z-50 w-72 p-3 shadow-2xl",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          <div className="mb-3 flex items-center justify-between">
            <button
              type="button"
              aria-label="ماه بعد"
              onClick={() => setCursor((state) => shiftJalaliMonth(state, 1))}
              className="grid size-7 place-items-center rounded-lg text-muted hover:bg-surface-2 hover:text-fg"
            >
              <ChevronRight className="size-4" />
            </button>
            <span className="text-xs font-semibold">
              {JALALI_MONTHS[cursor.month - 1]} {toFaDigits(cursor.year)}
            </span>
            <button
              type="button"
              aria-label="ماه قبل"
              onClick={() => setCursor((state) => shiftJalaliMonth(state, -1))}
              className="grid size-7 place-items-center rounded-lg text-muted hover:bg-surface-2 hover:text-fg"
            >
              <ChevronLeft className="size-4" />
            </button>
          </div>

          <div className="mb-1 grid grid-cols-7 gap-1 text-center text-[10px] text-muted">
            {JALALI_WEEKDAYS_SHORT.map((day) => (
              <span key={day}>{day}</span>
            ))}
          </div>

          <div className="grid grid-cols-7 gap-1">
            {weeks.flat().map((cell, index) => {
              if (!cell) return <span key={`empty-${index}`} className="h-8" />;
              const isSelected = sameJalaliDay(cell, value);
              const isToday = sameJalaliDay(cell, today);
              return (
                <button
                  key={`${cell.year}-${cell.month}-${cell.day}`}
                  type="button"
                  onClick={() => {
                    onChange(cell);
                    setOpen(false);
                  }}
                  className={cn(
                    "num h-8 rounded-lg text-[11px] transition-colors",
                    isSelected
                      ? "bg-brand font-bold text-[#04202a]"
                      : isToday
                        ? "border border-brand/50 text-brand"
                        : "text-fg hover:bg-surface-3",
                  )}
                >
                  {toFaDigits(cell.day)}
                </button>
              );
            })}
          </div>

          <div className="mt-3 flex items-center justify-between border-t border-border pt-2.5">
            <button
              type="button"
              onClick={() => {
                onChange(today);
                setOpen(false);
              }}
              className="text-[11px] text-brand hover:underline"
            >
              امروز
            </button>
            <span className="num text-[10px] text-muted">
              {toFaDigits(today.year)}/{toFaDigits(String(today.month).padStart(2, "0"))}/
              {toFaDigits(String(today.day).padStart(2, "0"))}
              {" — "}
              {faNumber(new Date().getHours())}:{faNumber(new Date().getMinutes())}
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}

import { X } from "lucide-react";
import { cn, toFaDigits } from "../lib/utils";

export interface ChipOption<T extends string | number> {
  value: T;
  label: string;
  count?: number;
}

export function MultiChipGroup<T extends string | number>({
  label,
  options,
  selected,
  onChange,
  className,
  allowEmpty = true,
  limit,
}: {
  label?: string;
  options: ChipOption<T>[];
  selected: T[];
  onChange: (values: T[]) => void;
  className?: string;
  allowEmpty?: boolean;
  limit?: number;
}) {
  const visible = typeof limit === "number" ? options.slice(0, limit) : options;

  const toggle = (value: T) => {
    if (selected.includes(value)) {
      if (!allowEmpty && selected.length === 1) return;
      onChange(selected.filter((item) => item !== value));
      return;
    }
    onChange([...selected, value]);
  };

  return (
    <div className={cn("space-y-1.5", className)}>
      {label ? (
        <div className="flex items-center justify-between">
          <span className="text-[11px] text-muted">{label}</span>
          {selected.length > 0 ? (
            <button
              type="button"
              onClick={() => onChange([])}
              className="inline-flex items-center gap-1 text-[11px] text-muted hover:text-danger"
            >
              <X className="size-3" />
              پاک کردن
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-1.5">
        {visible.map((option) => {
          const active = selected.includes(option.value);
          return (
            <button
              key={String(option.value)}
              type="button"
              onClick={() => toggle(option.value)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] transition-colors",
                active
                  ? "border-brand/50 bg-brand/15 text-brand"
                  : "border-border bg-surface-2/60 text-muted hover:border-brand/30 hover:text-fg",
              )}
            >
              {option.label}
              {option.count !== undefined ? (
                <span className="num text-[11px] opacity-75">{toFaDigits(option.count)}</span>
              ) : null}
            </button>
          );
        })}
        {visible.length === 0 ? <span className="text-[11px] text-muted">گزینه‌ای موجود نیست</span> : null}
      </div>
    </div>
  );
}

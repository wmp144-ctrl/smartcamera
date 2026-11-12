import { Clock } from "lucide-react";
import { cn } from "../lib/utils";
import { PRESET_LABELS, type RangePreset, type UseDateRange } from "../lib/useDateRange";
import { JalaliDatePicker } from "./JalaliDatePicker";

const PRESETS: RangePreset[] = ["today", "yesterday", "last7", "last30", "thisMonth", "custom"];

export function DateRangeBar({ range, className }: { range: UseDateRange; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      <div className="inline-flex flex-wrap items-center gap-1 rounded-xl border border-border bg-surface-2/60 p-1">
        {PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            onClick={() => range.setPreset(preset)}
            className={cn(
              "rounded-lg px-2.5 py-1.5 text-[11px] transition-colors",
              range.preset === preset
                ? "bg-brand/15 text-brand shadow-[inset_0_0_0_1px_rgb(34_211_238/0.35)]"
                : "text-muted hover:bg-surface-3 hover:text-fg",
            )}
          >
            {PRESET_LABELS[preset]}
          </button>
        ))}
      </div>

      {range.preset === "custom" ? (
        <div className="flex flex-wrap items-center gap-2">
          <div className="w-40">
            <JalaliDatePicker value={range.from} onChange={range.setFrom} placeholder="از تاریخ" clearable={false} />
          </div>
          <span className="text-muted">—</span>
          <div className="w-40">
            <JalaliDatePicker value={range.to} onChange={range.setTo} placeholder="تا تاریخ" clearable={false} />
          </div>
        </div>
      ) : (
        <span className="inline-flex items-center gap-1.5 rounded-xl border border-border bg-surface-2/60 px-2.5 py-2 text-[11px] text-muted">
          <Clock className="size-3.5 text-brand" />
          {range.label}
        </span>
      )}
    </div>
  );
}

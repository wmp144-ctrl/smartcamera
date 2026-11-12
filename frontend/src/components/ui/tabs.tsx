import type { ReactNode } from "react";
import { cn } from "../../lib/utils";

export interface TabItem<T extends string> {
  value: T;
  label: ReactNode;
  icon?: ReactNode;
  badge?: ReactNode;
}

export function Tabs<T extends string>({
  items,
  value,
  onChange,
  className,
  size = "md",
}: {
  items: TabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
  size?: "sm" | "md";
}) {
  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex max-w-full flex-wrap items-center gap-1 rounded-xl border border-border bg-surface-2/60 p-1",
        className,
      )}
    >
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(item.value)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-lg font-medium transition-all",
              size === "sm" ? "px-2.5 py-1 text-[11px]" : "px-3 py-1.5 text-xs",
              active
                ? "bg-brand/15 text-brand shadow-[inset_0_0_0_1px_rgb(34_211_238/0.35)]"
                : "text-muted hover:bg-surface-3 hover:text-fg",
              "[&_svg]:size-3.5",
            )}
          >
            {item.icon}
            {item.label}
            {item.badge !== undefined && item.badge !== null ? (
              <span className="num rounded-full bg-surface-3 px-1.5 text-[10px]">{item.badge}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

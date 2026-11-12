import type { ReactNode } from "react";
import { cn } from "../../lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton rounded-xl", className)} />;
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="در حال بارگذاری"
      className={cn(
        "inline-block size-5 animate-spin rounded-full border-2 border-brand/30 border-t-brand",
        className,
      )}
    />
  );
}

export function LoadingBlock({ label = "در حال بارگذاری…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-12 text-xs text-muted">
      <Spinner />
      {label}
    </div>
  );
}

export function ErrorBlock({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-10 text-center">
      <p className="max-w-md text-xs leading-6 text-danger">{message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted hover:border-brand/50 hover:text-fg"
        >
          تلاش دوباره
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 px-6 py-14 text-center", className)}>
      {icon ? (
        <span className="grid size-14 place-items-center rounded-2xl border border-border bg-surface-2 text-muted [&_svg]:size-6">
          {icon}
        </span>
      ) : null}
      <div>
        <p className="text-sm font-medium">{title}</p>
        {description ? <p className="mx-auto mt-1 max-w-sm text-[11px] leading-6 text-muted">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function Progress({
  value,
  tone = "brand",
  className,
  showLabel,
}: {
  value: number;
  tone?: "brand" | "ok" | "warn" | "danger";
  className?: string;
  showLabel?: boolean;
}) {
  const pct = Math.max(0, Math.min(100, value));
  const bar =
    tone === "ok" ? "bg-ok" : tone === "warn" ? "bg-warn" : tone === "danger" ? "bg-danger" : "bg-brand";
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
        <div className={cn("h-full rounded-full transition-all duration-500", bar)} style={{ width: `${pct}%` }} />
      </div>
      {showLabel ? <span className="num w-11 text-[10px] text-muted">{pct.toFixed(0)}%</span> : null}
    </div>
  );
}

export function Slider({
  value,
  onChange,
  min = 0,
  max = 1,
  step = 0.01,
  label,
  format,
  disabled,
}: {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  label?: ReactNode;
  format?: (value: number) => string;
  disabled?: boolean;
}) {
  return (
    <div className={cn("rounded-xl border border-border bg-surface-2/60 p-3", disabled && "opacity-55")}>
      {label ? (
        <div className="mb-2 flex items-center justify-between text-xs">
          <span className="text-muted">{label}</span>
          <span className="num text-brand">{format ? format(value) : value.toFixed(2)}</span>
        </div>
      ) : null}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-surface-3 accent-[var(--brand)] [&::-webkit-slider-thumb]:size-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-brand"
      />
    </div>
  );
}

export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <span className="group/tt relative inline-flex">
      {children}
      <span className="pointer-events-none absolute bottom-[calc(100%+6px)] right-1/2 z-50 hidden translate-x-1/2 whitespace-nowrap rounded-lg border border-border bg-surface-2 px-2 py-1 text-[10px] text-fg shadow-lg group-hover/tt:block">
        {content}
      </span>
    </span>
  );
}

export function Metric({
  label,
  value,
  unit,
  icon,
  tone = "default",
  hint,
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
  icon?: ReactNode;
  tone?: "default" | "ok" | "warn" | "danger" | "brand";
  hint?: ReactNode;
  className?: string;
}) {
  const toneClass =
    tone === "ok"
      ? "text-ok"
      : tone === "warn"
        ? "text-warn"
        : tone === "danger"
          ? "text-danger"
          : tone === "brand"
            ? "text-brand"
            : "text-fg";
  return (
    <div className={cn("panel p-3.5", className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-muted">{label}</span>
        {icon ? <span className={cn("[&_svg]:size-4", toneClass)}>{icon}</span> : null}
      </div>
      <div className="mt-2 flex items-baseline gap-1.5">
        <span className={cn("num text-xl font-bold leading-none", toneClass)}>{value}</span>
        {unit ? <span className="text-[11px] text-muted">{unit}</span> : null}
      </div>
      {hint ? <p className="mt-1.5 text-[10px] text-muted">{hint}</p> : null}
    </div>
  );
}

export function KeyValue({
  label,
  value,
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-3 border-b border-border/50 py-1.5 last:border-0", className)}>
      <span className="text-[11px] text-muted">{label}</span>
      <span className="num text-[11px] font-medium">{value}</span>
    </div>
  );
}

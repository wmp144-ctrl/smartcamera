import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

const badgeStyles = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-5",
  {
    variants: {
      tone: {
        muted: "border-border bg-surface-2 text-muted",
        brand: "border-brand/35 bg-brand/12 text-brand",
        ok: "border-ok/35 bg-ok/12 text-ok",
        warn: "border-warn/40 bg-warn/12 text-warn",
        danger: "border-danger/40 bg-danger/14 text-danger",
        info: "border-info/35 bg-info/12 text-info",
      },
    },
    defaultVariants: { tone: "muted" },
  },
);

export type BadgeTone = NonNullable<VariantProps<typeof badgeStyles>["tone"]>;

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeStyles> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeStyles({ tone }), className)} {...props} />;
}

export function StatusDot({ tone = "muted", pulse }: { tone?: BadgeTone; pulse?: boolean }) {
  const color =
    tone === "ok"
      ? "bg-ok"
      : tone === "warn"
        ? "bg-warn"
        : tone === "danger"
          ? "bg-danger"
          : tone === "info"
            ? "bg-info"
            : tone === "brand"
              ? "bg-brand"
              : "bg-muted";
  return (
    <span className="relative inline-flex size-2.5">
      {pulse ? (
        <span className={cn("absolute inline-flex size-full animate-ping rounded-full opacity-70", color)} />
      ) : null}
      <span className={cn("relative inline-flex size-2.5 rounded-full", color)} />
    </span>
  );
}

import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "../../lib/utils";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "panel relative overflow-hidden shadow-[0_18px_50px_-30px_rgb(2_6_23/0.9)]",
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({
  title,
  subtitle,
  icon,
  actions,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-3 border-b border-border/70 px-4 py-3",
        className,
      )}
    >
      <div className="flex items-center gap-2.5">
        {icon ? <span className="text-brand [&_svg]:size-4.5">{icon}</span> : null}
        <div>
          <h3 className="text-sm font-semibold leading-tight">{title}</h3>
          {subtitle ? <p className="mt-0.5 text-[11px] text-muted">{subtitle}</p> : null}
        </div>
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)} {...props} />;
}

export function CardFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("flex items-center justify-between gap-2 border-t border-border/70 px-4 py-3", className)}
      {...props}
    />
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
  icon,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="flex items-center gap-3">
        {icon ? (
          <span className="grid size-11 place-items-center rounded-2xl border border-brand/25 bg-brand/10 text-brand [&_svg]:size-5.5">
            {icon}
          </span>
        ) : null}
        <div>
          <h1 className="text-xl font-bold tracking-tight">{title}</h1>
          {subtitle ? <p className="mt-1 text-xs text-muted">{subtitle}</p> : null}
        </div>
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

import type { HTMLAttributes, ReactNode, ThHTMLAttributes, TdHTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export function TableWrap({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn("w-full overflow-x-auto", className)}>
      <table className="w-full border-collapse text-right text-xs">{children}</table>
    </div>
  );
}

export function Thead({ children }: { children: ReactNode }) {
  return <thead className="bg-surface-2/70 text-[11px] text-muted">{children}</thead>;
}

export function Th({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn("whitespace-nowrap border-b border-border px-3 py-2.5 font-medium", className)}
      {...props}
    />
  );
}

export function Tbody({ children }: { children: ReactNode }) {
  return <tbody className="divide-y divide-border/60">{children}</tbody>;
}

export function Tr({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn("transition-colors hover:bg-surface-2/60", className)} {...props} />;
}

export function Td({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-3 py-2.5 align-middle", className)} {...props} />;
}

export function Pager({
  page,
  pageSize,
  total,
  onPage,
  onPageSize,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  onPageSize?: (size: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/70 px-3 py-2.5 text-[11px] text-muted">
      <span className="num">
        {total.toLocaleString("fa-IR")} مورد — صفحه {page.toLocaleString("fa-IR")} از{" "}
        {pages.toLocaleString("fa-IR")}
      </span>
      <div className="flex items-center gap-2">
        {onPageSize ? (
          <select
            value={pageSize}
            onChange={(event) => onPageSize(Number(event.target.value))}
            className="h-8 rounded-lg border border-border bg-surface-2 px-2 text-[11px]"
          >
            {[20, 40, 60, 100, 200].map((size) => (
              <option key={size} value={size}>
                {size.toLocaleString("fa-IR")} در صفحه
              </option>
            ))}
          </select>
        ) : null}
        <div className="flex items-center gap-1">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => onPage(page - 1)}
            className="rounded-lg border border-border px-2 py-1 transition-colors hover:border-brand/50 hover:text-fg disabled:opacity-40"
          >
            قبلی
          </button>
          <button
            type="button"
            disabled={page >= pages}
            onClick={() => onPage(page + 1)}
            className="rounded-lg border border-border px-2 py-1 transition-colors hover:border-brand/50 hover:text-fg disabled:opacity-40"
          >
            بعدی
          </button>
        </div>
      </div>
    </div>
  );
}

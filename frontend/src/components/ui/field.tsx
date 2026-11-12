import {
  forwardRef,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { cn } from "../../lib/utils";

export function Label({
  children,
  hint,
  required,
  className,
}: {
  children: ReactNode;
  hint?: ReactNode;
  required?: boolean;
  className?: string;
}) {
  return (
    <label className={cn("mb-1.5 flex items-center justify-between gap-2 text-xs text-muted", className)}>
      <span>
        {children}
        {required ? <span className="text-danger"> *</span> : null}
      </span>
      {hint ? <span className="text-[11px] text-muted/80">{hint}</span> : null}
    </label>
  );
}

export function Field({
  label,
  hint,
  required,
  error,
  children,
  className,
}: {
  label?: ReactNode;
  hint?: ReactNode;
  required?: boolean;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      {label ? (
        <Label hint={hint} required={required}>
          {label}
        </Label>
      ) : null}
      {children}
      {error ? <p className="mt-1 text-[11px] text-danger">{error}</p> : null}
    </div>
  );
}

const controlBase =
  "w-full rounded-xl border border-border bg-surface-2/80 px-3 text-sm text-fg placeholder:text-muted/70 transition-colors focus:border-brand/60 focus:bg-surface-2 focus:outline-none disabled:cursor-not-allowed disabled:opacity-55";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={cn(controlBase, "h-10", className)} {...props} />;
  },
);

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return <textarea ref={ref} className={cn(controlBase, "min-h-20 py-2 leading-6", className)} {...props} />;
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, ...props }, ref) {
    return (
      <select
        ref={ref}
        className={cn(
          controlBase,
          "h-10 cursor-pointer appearance-none bg-[length:14px] bg-[left_0.75rem_center] bg-no-repeat pl-3 pr-8",
          className,
        )}
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238ea3c2' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")",
          backgroundPosition: "left 0.75rem center",
        }}
        {...props}
      >
        {children}
      </select>
    );
  },
);

export function Checkbox({
  checked,
  onChange,
  label,
  description,
  disabled,
  className,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label?: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-2.5 rounded-xl border border-border bg-surface-2/60 p-2.5 transition-colors hover:border-brand/40",
        disabled && "cursor-not-allowed opacity-55",
        className,
      )}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 size-4 shrink-0 accent-[var(--brand)]"
      />
      <span className="min-w-0">
        {label ? <span className="block text-xs font-medium">{label}</span> : null}
        {description ? <span className="mt-0.5 block text-[11px] leading-5 text-muted">{description}</span> : null}
      </span>
    </label>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label?: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "flex w-full items-center justify-between gap-3 rounded-xl border border-border bg-surface-2/60 p-3 text-right transition-colors hover:border-brand/40 disabled:opacity-50",
      )}
    >
      <span className="min-w-0">
        {label ? <span className="block text-xs font-medium">{label}</span> : null}
        {description ? <span className="mt-0.5 block text-[11px] leading-5 text-muted">{description}</span> : null}
      </span>
      <span
        className={cn(
          "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
          checked ? "bg-brand" : "bg-border-strong",
        )}
      >
        <span
          className={cn(
            "absolute size-4.5 rounded-full bg-white shadow transition-all",
            checked ? "right-1" : "right-6",
          )}
        />
      </span>
    </button>
  );
}

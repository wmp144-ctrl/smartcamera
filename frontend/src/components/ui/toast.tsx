import { Check, Info, TriangleAlert, X } from "lucide-react";
import { createPortal } from "react-dom";
import { create } from "zustand";
import { cn } from "../../lib/utils";

export type ToastTone = "success" | "error" | "info" | "warn";

interface ToastItem {
  id: number;
  tone: ToastTone;
  title: string;
  description?: string;
}

interface ToastState {
  items: ToastItem[];
  push: (tone: ToastTone, title: string, description?: string) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;

export const useToasts = create<ToastState>((set) => ({
  items: [],
  push: (tone, title, description) => {
    const id = nextId++;
    set((state) => ({ items: [...state.items, { id, tone, title, description }].slice(-5) }));
    setTimeout(() => {
      set((state) => ({ items: state.items.filter((item) => item.id !== id) }));
    }, 5200);
  },
  dismiss: (id) => set((state) => ({ items: state.items.filter((item) => item.id !== id) })),
}));

/** Imperative helper usable outside React components. */
export const toast = {
  success: (title: string, description?: string) => useToasts.getState().push("success", title, description),
  error: (title: string, description?: string) => useToasts.getState().push("error", title, description),
  info: (title: string, description?: string) => useToasts.getState().push("info", title, description),
  warn: (title: string, description?: string) => useToasts.getState().push("warn", title, description),
};

export function Toaster() {
  const items = useToasts((state) => state.items);
  const dismiss = useToasts((state) => state.dismiss);

  if (typeof document === "undefined") return null;

  return createPortal(
    <div className="pointer-events-none fixed bottom-4 left-4 z-[200] flex w-full max-w-sm flex-col gap-2">
      {items.map((item) => (
        <div
          key={item.id}
          className={cn(
            "panel animate-fade-up pointer-events-auto flex items-start gap-2.5 p-3 shadow-2xl",
            item.tone === "success" && "border-ok/45",
            item.tone === "error" && "border-danger/50",
            item.tone === "warn" && "border-warn/50",
            item.tone === "info" && "border-info/45",
          )}
        >
          <span
            className={cn(
              "mt-0.5 [&_svg]:size-4",
              item.tone === "success" && "text-ok",
              item.tone === "error" && "text-danger",
              item.tone === "warn" && "text-warn",
              item.tone === "info" && "text-info",
            )}
          >
            {item.tone === "success" ? (
              <Check />
            ) : item.tone === "error" || item.tone === "warn" ? (
              <TriangleAlert />
            ) : (
              <Info />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium">{item.title}</p>
            {item.description ? (
              <p className="mt-0.5 break-words text-[11px] leading-5 text-muted">{item.description}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={() => dismiss(item.id)}
            aria-label="بستن"
            className="grid size-6 place-items-center rounded-md text-muted hover:bg-surface-2 hover:text-fg"
          >
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>,
    document.body,
  );
}

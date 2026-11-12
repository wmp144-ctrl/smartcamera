import { Suspense, useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { cn } from "../../lib/utils";
import { useUI } from "../../store/ui";
import { LiveWatcher } from "../live/LiveWatcher";
import { LoadingBlock } from "../ui/misc";
import { Toaster } from "../ui/toast";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

export function AppShell() {
  const theme = useUI((state) => state.theme);
  const toggleSidebar = useUI((state) => state.toggleSidebar);
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    root.classList.toggle("light", theme === "light");
    root.style.colorScheme = theme;
  }, [theme]);

  // The drawer is the only way to reach another page on a phone, so it must
  // behave like a real overlay: Escape closes it and the page behind it does
  // not scroll.
  useEffect(() => {
    if (!navOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setNavOpen(false);
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [navOpen]);

  function handleMenu() {
    // Desktop: collapse the rail. Phone/tablet: open the drawer.
    if (window.matchMedia("(min-width: 768px)").matches) {
      toggleSidebar();
    } else {
      setNavOpen((value) => !value);
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      <div className="hidden md:flex">
        <Sidebar />
      </div>

      <div className={cn("flex min-w-0 flex-1 flex-col")}>
        <Topbar onToggleSidebar={handleMenu} />
        <main className="grid-noise flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1800px] p-4 lg:p-6">
            {/* Pages are lazy-loaded; keep the shell (nav, live view, toasts)
                mounted while a route chunk arrives. */}
            <Suspense fallback={<LoadingBlock label="در حال بارگذاری صفحه…" />}>
              <Outlet />
            </Suspense>
          </div>
        </main>
      </div>

      {navOpen ? (
        <div className="fixed inset-0 z-50 md:hidden" role="dialog" aria-modal="true" aria-label="منوی اصلی">
          <button
            type="button"
            aria-label="بستن منو"
            onClick={() => setNavOpen(false)}
            className="absolute inset-0 h-full w-full cursor-default bg-black/60 backdrop-blur-sm"
          />
          <div className="animate-fade-up absolute inset-y-0 right-0 w-64 shadow-[0_0_80px_-20px_rgb(0_0_0/0.9)]">
            <Sidebar forceExpanded onNavigate={() => setNavOpen(false)} />
          </div>
        </div>
      ) : null}

      <LiveWatcher />
      <Toaster />
    </div>
  );
}

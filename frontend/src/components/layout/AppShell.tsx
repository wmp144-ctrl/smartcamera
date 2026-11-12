import { Suspense, useEffect } from "react";
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

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    root.classList.toggle("light", theme === "light");
    root.style.colorScheme = theme;
  }, [theme]);

  return (
    <div className="flex h-full overflow-hidden">
      <div className="hidden md:flex">
        <Sidebar />
      </div>

      <div className={cn("flex min-w-0 flex-1 flex-col")}>
        <Topbar onToggleSidebar={toggleSidebar} />
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

      <LiveWatcher />
      <Toaster />
    </div>
  );
}

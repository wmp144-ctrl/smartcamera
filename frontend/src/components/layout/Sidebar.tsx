import {
  Activity,
  Bell,
  ChartColumn,
  Crosshair,
  Images,
  LayoutDashboard,
  Settings,
  ShieldCheck,
  Video,
} from "lucide-react";
import { NavLink } from "react-router-dom";
import { cn } from "../../lib/utils";
import { useUnreadAlerts } from "../../lib/queries";
import { useUI } from "../../store/ui";

interface NavItem {
  to: string;
  label: string;
  icon: typeof Video;
  badge?: number;
}

export function Sidebar({
  onNavigate,
  forceExpanded = false,
}: {
  onNavigate?: () => void;
  /** The mobile drawer always shows labels, even if the desktop rail is collapsed. */
  forceExpanded?: boolean;
}) {
  const storedCollapsed = useUI((state) => state.sidebarCollapsed);
  const collapsed = forceExpanded ? false : storedCollapsed;
  const unreadFromApi = useUnreadAlerts();
  const unreadLocal = useUI((state) => state.unreadAlerts);
  const unread = Math.max(unreadFromApi.data?.unread ?? 0, unreadLocal);

  const items: NavItem[] = [
    { to: "/dashboard", label: "داشبورد", icon: LayoutDashboard },
    { to: "/cameras", label: "دوربین‌ها", icon: Video },
    { to: "/objects", label: "اشیای ثبت‌شده", icon: Images },
    { to: "/events", label: "رویدادها", icon: Activity },
    { to: "/alerts", label: "هشدارها", icon: Bell, badge: unread },
    { to: "/search", label: "جست‌وجوی مکانی", icon: Crosshair },
    { to: "/stats", label: "آمار و نقشه حرارتی", icon: ChartColumn },
    { to: "/settings", label: "تنظیمات سیستم", icon: Settings },
  ];

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col border-l border-border bg-surface/70 backdrop-blur-xl transition-[width] duration-200",
        collapsed ? "w-[68px]" : "w-60",
      )}
    >
      <div className="flex h-16 items-center gap-2.5 border-b border-border px-4">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-brand/12 text-brand ring-1 ring-brand/30">
          <ShieldCheck className="size-5" />
        </span>
        {!collapsed ? (
          <div className="min-w-0">
            <p className="truncate text-sm font-bold leading-tight">CameraSmart</p>
            <p className="truncate text-[11px] text-muted">پایش هوشمند دوربین</p>
          </div>
        ) : null}
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto p-2.5">
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            onClick={onNavigate}
            title={collapsed ? item.label : undefined}
            className={({ isActive }) =>
              cn(
                "group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-xs font-medium transition-all",
                collapsed && "justify-center px-0",
                isActive
                  ? "bg-brand/12 text-brand shadow-[inset_0_0_0_1px_rgb(34_211_238/0.3)]"
                  : "text-muted hover:bg-surface-2 hover:text-fg",
              )
            }
          >
            <item.icon className="size-4.5 shrink-0" />
            {!collapsed ? <span className="flex-1 truncate">{item.label}</span> : null}
            {item.badge && item.badge > 0 ? (
              <span
                className={cn(
                  "num rounded-full bg-danger/20 px-1.5 text-[11px] font-bold text-danger",
                  collapsed && "absolute mt-[-18px] mr-[-24px] px-1",
                )}
              >
                {item.badge > 99 ? "۹۹+" : item.badge.toLocaleString("fa-IR")}
              </span>
            ) : null}
          </NavLink>
        ))}
      </nav>

      {!collapsed ? (
        <div className="border-t border-border p-3">
          <div className="rounded-xl border border-border bg-surface-2/60 p-2.5">
            <p className="text-[11px] leading-5 text-muted">
              نسخه ۱.۰.۰ — ذخیره‌سازی محلی، بدون نیاز به اینترنت
            </p>
          </div>
        </div>
      ) : null}
    </aside>
  );
}

import {
  Clapperboard,
  LayoutDashboard,
  LayoutGrid,
  ListChecks,
  Scissors,
  Search,
  Settings,
  Tv,
  Film,
  type LucideIcon,
} from "lucide-react";
import { NavLink } from "react-router";

import { useSystemInfo } from "../api/system";
import { cn } from "../lib/cn";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/channels", label: "Channels", icon: Tv },
  { to: "/projects", label: "Projects", icon: Film },
  { to: "/research", label: "Research", icon: Search },
  { to: "/storyboard", label: "Storyboard", icon: LayoutGrid },
  { to: "/editor", label: "Editor", icon: Scissors },
  { to: "/review", label: "Review", icon: ListChecks },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const system = useSystemInfo();

  return (
    <aside className="flex h-screen w-60 shrink-0 flex-col border-r border-line bg-surface">
      <div className="flex h-16 items-center gap-3 border-b border-line px-5">
        <div className="flex size-8 items-center justify-center rounded-md bg-navy ring-1 ring-accent/70">
          <Clapperboard className="size-4 text-accent" aria-hidden />
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-ink">Cashflow Studio</div>
          <div className="text-[11px] text-ink-faint">Faceless video pipeline</div>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-4" aria-label="Main">
        <ul className="flex flex-col gap-0.5">
          {NAV_ITEMS.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    "group relative flex h-9 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                    isActive ? "bg-surface-2 text-ink" : "text-ink-muted hover:bg-surface-2/70 hover:text-ink",
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive ? (
                      <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r bg-accent" aria-hidden />
                    ) : null}
                    <item.icon className={cn("size-4 shrink-0", isActive ? "text-accent-text" : "text-ink-faint group-hover:text-ink-muted")} aria-hidden />
                    {item.label}
                  </>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <div className="border-t border-line px-5 py-4 text-[11px] leading-5 text-ink-faint">
        <div>Version {system.data?.version ?? "..."}</div>
        <div>Runs on this computer only (127.0.0.1)</div>
      </div>
    </aside>
  );
}

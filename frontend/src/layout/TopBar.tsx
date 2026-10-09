import { Moon, Sun } from "lucide-react";
import { useMatches } from "react-router";

import { useTheme } from "../lib/theme";
import { DoctorPill } from "./DoctorPill";

interface RouteHandle {
  title?: string;
}

function hasTitle(handle: unknown): handle is Required<RouteHandle> {
  return typeof handle === "object" && handle !== null && typeof (handle as RouteHandle).title === "string";
}

export function usePageTitle(): string {
  const matches = useMatches();
  for (let i = matches.length - 1; i >= 0; i -= 1) {
    const handle = matches[i]?.handle;
    if (hasTitle(handle)) return handle.title;
  }
  return "Cashflow Studio";
}

export function TopBar() {
  const title = usePageTitle();
  const { theme, toggle } = useTheme();

  return (
    <header className="flex h-16 shrink-0 items-center justify-between gap-4 border-b border-line bg-surface/80 px-8 backdrop-blur">
      <h1 className="text-lg font-semibold tracking-tight text-ink">{title}</h1>
      <div className="flex items-center gap-3">
        <DoctorPill />
        <button
          type="button"
          onClick={toggle}
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          className="flex size-8 items-center justify-center rounded-md border border-line bg-surface-2 text-ink-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
        >
          {theme === "dark" ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
        </button>
      </div>
    </header>
  );
}

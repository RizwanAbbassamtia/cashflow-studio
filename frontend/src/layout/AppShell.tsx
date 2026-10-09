import { useEffect, useRef } from "react";
import { Outlet, useLocation } from "react-router";

import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

/** Sidebar + top bar frame around every page. Designed for 1280px and wider. */
export function AppShell() {
  const location = useLocation();
  const main = useRef<HTMLElement>(null);

  // The page body scrolls inside <main>, so start each new page at the top.
  useEffect(() => {
    if (!location.hash) main.current?.scrollTo({ top: 0 });
  }, [location.pathname, location.hash]);

  return (
    <div className="flex h-screen min-w-[1024px] overflow-hidden bg-canvas text-ink">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main ref={main} className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1440px] px-8 py-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}

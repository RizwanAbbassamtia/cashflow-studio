import { ArrowUpRight, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { cn } from "../../lib/cn";

export interface StatCardProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon: LucideIcon;
  to?: string;
  /** colours the value, e.g. for the health check status */
  tone?: "default" | "ok" | "warn" | "fail" | "muted";
}

const toneClasses = {
  default: "text-ink",
  ok: "text-ok",
  warn: "text-warn",
  fail: "text-fail",
  muted: "text-ink-faint",
} as const;

export function StatCard({ label, value, hint, icon: Icon, to, tone = "default" }: StatCardProps) {
  const body = (
    <>
      <div className="flex items-start justify-between">
        <span className="text-[13px] font-medium text-ink-muted">{label}</span>
        <span className="flex size-8 items-center justify-center rounded-md bg-surface-2 text-ink-muted">
          <Icon className="size-4" aria-hidden />
        </span>
      </div>
      <div className={cn("mt-3 text-2xl font-semibold tracking-tight", toneClasses[tone])}>{value}</div>
      {hint ? <div className="mt-1 text-xs text-ink-faint">{hint}</div> : null}
      {to ? (
        <ArrowUpRight className="absolute bottom-4 right-4 size-4 text-ink-faint opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
      ) : null}
    </>
  );

  const className = "group relative block rounded-card border border-line bg-surface p-5 shadow-card transition-colors";

  return to ? (
    <Link to={to} className={cn(className, "hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60")}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  );
}

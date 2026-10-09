import { Activity } from "lucide-react";
import { Link } from "react-router";

import { ApiError } from "../api/client";
import { useDoctor } from "../api/doctor";
import { cn } from "../lib/cn";
import type { DoctorReport } from "../types";

export type DoctorLevel = "ok" | "warn" | "fail" | "pending";

export function summarizeDoctor(report: DoctorReport | undefined, error: unknown): { level: DoctorLevel; label: string } {
  if (error) {
    const offline = error instanceof ApiError && error.isNetworkError;
    return { level: "fail", label: offline ? "Server not reachable" : "Health check failed" };
  }
  if (!report) return { level: "pending", label: "Checking..." };
  const fails = report.checks.filter((c) => c.status === "fail").length;
  const warns = report.checks.filter((c) => c.status === "warn").length;
  if (fails > 0 || !report.ok) return { level: "fail", label: fails === 1 ? "1 problem" : `${fails} problems` };
  if (warns > 0) return { level: "warn", label: warns === 1 ? "1 warning" : `${warns} warnings` };
  return { level: "ok", label: "All checks passed" };
}

const levelClasses: Record<DoctorLevel, string> = {
  ok: "border-ok/30 bg-ok/10 text-ok",
  warn: "border-warn/30 bg-warn/10 text-warn",
  fail: "border-fail/30 bg-fail/10 text-fail",
  pending: "border-line bg-surface-2 text-ink-muted",
};

/** Top-bar health indicator; links to the Doctor panel on the Settings page. */
export function DoctorPill() {
  const doctor = useDoctor({ refetchInterval: 120_000 });
  const { level, label } = summarizeDoctor(doctor.data, doctor.error);

  return (
    <Link
      to="/settings#doctor"
      title="Open the health checks"
      className={cn(
        "inline-flex h-8 items-center gap-2 rounded-full border px-3 text-xs font-medium transition-colors hover:brightness-110",
        levelClasses[level],
      )}
    >
      <span className={cn("size-2 rounded-full bg-current", level === "pending" && "animate-pulse")} aria-hidden />
      <Activity className="size-3.5" aria-hidden />
      {label}
    </Link>
  );
}

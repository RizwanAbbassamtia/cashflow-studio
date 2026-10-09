import type { ReactNode } from "react";

import { cn } from "../../lib/cn";
import { STATUS_LABELS } from "../../lib/labels";
import type { ChannelStatus, DoctorStatus } from "../../types";

export type BadgeTone = "neutral" | "ok" | "warn" | "fail" | "accent" | "info";

const toneClasses: Record<BadgeTone, string> = {
  neutral: "bg-surface-2 text-ink-muted border-line",
  ok: "bg-ok/12 text-ok border-ok/30",
  warn: "bg-warn/12 text-warn border-warn/30",
  fail: "bg-fail/12 text-fail border-fail/30",
  accent: "bg-accent/15 text-accent-text border-accent/30",
  info: "bg-info/12 text-info border-info/30",
};

export function Badge({ tone = "neutral", className, children, dot }: { tone?: BadgeTone; className?: string; children: ReactNode; dot?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium leading-5",
        toneClasses[tone],
        className,
      )}
    >
      {dot ? <span className="size-1.5 rounded-full bg-current" aria-hidden /> : null}
      {children}
    </span>
  );
}

const channelStatusTone: Record<ChannelStatus, BadgeTone> = {
  setup: "info",
  active: "ok",
  paused: "warn",
  archived: "neutral",
};

export function ChannelStatusBadge({ status }: { status: ChannelStatus }) {
  return (
    <Badge tone={channelStatusTone[status]} dot>
      {STATUS_LABELS[status]}
    </Badge>
  );
}

export const doctorStatusTone: Record<DoctorStatus, BadgeTone> = { ok: "ok", warn: "warn", fail: "fail" };
export const doctorStatusLabel: Record<DoctorStatus, string> = { ok: "OK", warn: "Warning", fail: "Problem" };

export function DoctorStatusBadge({ status }: { status: DoctorStatus }) {
  return (
    <Badge tone={doctorStatusTone[status]} dot>
      {doctorStatusLabel[status]}
    </Badge>
  );
}

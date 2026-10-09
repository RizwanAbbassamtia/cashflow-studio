import { AlertTriangle, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { errorMessage } from "../../api/client";
import { cn } from "../../lib/cn";
import { Button } from "./Button";

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 px-6 py-14 text-center", className)}>
      {Icon ? (
        <div className="flex size-12 items-center justify-center rounded-full bg-surface-2 text-ink-muted">
          <Icon className="size-6" aria-hidden />
        </div>
      ) : null}
      <div className="max-w-sm">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {description ? <p className="mt-1 text-[13px] text-ink-muted">{description}</p> : null}
      </div>
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  error,
  title = "Something went wrong",
  onRetry,
  className,
}: {
  error: unknown;
  title?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 px-6 py-14 text-center", className)} role="alert">
      <div className="flex size-12 items-center justify-center rounded-full bg-fail/10 text-fail">
        <AlertTriangle className="size-6" aria-hidden />
      </div>
      <div className="max-w-md">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        <p className="mt-1 text-[13px] text-ink-muted">{errorMessage(error)}</p>
      </div>
      {onRetry ? (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

export type NoticeTone = "info" | "warn" | "fail" | "ok";

const noticeTone: Record<NoticeTone, string> = {
  info: "border-info/30 bg-info/10 text-ink",
  warn: "border-warn/30 bg-warn/10 text-ink",
  fail: "border-fail/30 bg-fail/10 text-ink",
  ok: "border-ok/30 bg-ok/10 text-ink",
};

/** Inline message block, e.g. a warning about the default shared folder. */
export function Notice({ tone = "info", title, children, className, action }: { tone?: NoticeTone; title?: string; children?: ReactNode; className?: string; action?: ReactNode }) {
  return (
    <div className={cn("flex items-start justify-between gap-4 rounded-md border px-4 py-3 text-[13px]", noticeTone[tone], className)}>
      <div className="min-w-0">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div className={cn(title && "mt-0.5", "text-ink-muted")}>{children}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

import { Check, Circle, Lock } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../../lib/cn";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";

export interface ChecklistStep {
  id: string;
  title: string;
  detail: ReactNode;
  state: "done" | "todo" | "locked";
  action?: ReactNode;
  /** shown for locked steps, e.g. "M1" */
  milestone?: string;
}

export function GetStartedChecklist({ steps }: { steps: ChecklistStep[] }) {
  const done = steps.filter((step) => step.state === "done").length;
  const total = steps.filter((step) => step.state !== "locked").length;

  return (
    <Card
      title="Get started"
      description={`${done} of ${total} steps done`}
      actions={
        <div className="h-1.5 w-32 overflow-hidden rounded-full bg-surface-2" aria-hidden>
          <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
        </div>
      }
      flush
    >
      <ol className="divide-y divide-line">
        {steps.map((step, index) => (
          <li key={step.id} className="flex items-start gap-4 px-5 py-4">
            <span
              className={cn(
                "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                step.state === "done" && "border-ok/40 bg-ok/15 text-ok",
                step.state === "todo" && "border-accent/60 bg-accent/10 text-accent-text",
                step.state === "locked" && "border-line bg-surface-2 text-ink-faint",
              )}
              aria-hidden
            >
              {step.state === "done" ? <Check className="size-3.5" /> : step.state === "locked" ? <Lock className="size-3" /> : index + 1}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className={cn("text-sm font-medium", step.state === "locked" ? "text-ink-muted" : "text-ink")}>{step.title}</span>
                {step.milestone ? <Badge tone="neutral">{step.milestone}</Badge> : null}
                {step.state === "todo" ? <Circle className="size-2 fill-accent-text text-accent-text" aria-hidden /> : null}
              </div>
              <p className="mt-0.5 text-[13px] text-ink-muted">{step.detail}</p>
            </div>
            {step.action ? <div className="shrink-0">{step.action}</div> : null}
          </li>
        ))}
      </ol>
    </Card>
  );
}

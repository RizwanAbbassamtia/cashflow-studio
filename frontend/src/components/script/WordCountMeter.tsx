import { cn } from "../../lib/cn";
import type { WordCountStatus } from "./scriptUtils";

/**
 * Word count against the target band (target +/- 15%). The bar spans 0 to 1.3x the target;
 * the lighter band is the accepted range, the marker is the current count.
 */
export function WordCountMeter({ status, className }: { status: WordCountStatus; className?: string }) {
  const { count, target, min, max, position, delta } = status;
  const scale = Math.max(target * 1.3, count, 1);
  const pct = (value: number) => `${Math.min(100, (value / scale) * 100)}%`;
  const tone = position === 0 ? "text-ok" : "text-warn";
  const message =
    target <= 0
      ? "No target length was set for this script."
      : position === 0
        ? `Within the target band (${min}-${max} words).`
        : position < 0
          ? `${Math.abs(delta)} words short of the ${target}-word target. The band is ${min}-${max}.`
          : `${delta} words over the ${target}-word target. The band is ${min}-${max}.`;

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] font-medium text-ink-muted">Length</span>
        <span className="text-sm tabular-nums">
          <span className={cn("font-semibold", tone)}>{count.toLocaleString()}</span>
          <span className="text-ink-faint"> / {target > 0 ? target.toLocaleString() : "-"} words</span>
        </span>
      </div>
      <div className="relative h-2 w-full overflow-hidden rounded-full bg-surface-2" aria-hidden>
        {target > 0 ? (
          <div className="absolute inset-y-0 rounded-full bg-ok/25" style={{ left: pct(min), width: `calc(${pct(max)} - ${pct(min)})` }} />
        ) : null}
        <div
          className={cn("absolute inset-y-0 w-1 rounded-full", position === 0 ? "bg-ok" : "bg-warn")}
          style={{ left: `calc(${pct(count)} - 2px)` }}
        />
      </div>
      <p className="text-xs text-ink-muted">{message}</p>
    </div>
  );
}

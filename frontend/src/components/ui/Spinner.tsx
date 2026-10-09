import { Loader2 } from "lucide-react";

import { cn } from "../../lib/cn";

export function Spinner({ className }: { className?: string }) {
  return <Loader2 aria-hidden className={cn("size-5 animate-spin text-ink-muted", className)} />;
}

/** Centered loading block for a page or a card body. */
export function LoadingBlock({ label = "Loading..." }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16 text-sm text-ink-muted" role="status">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}

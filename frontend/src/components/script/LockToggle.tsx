import { Lock, LockOpen } from "lucide-react";

import { cn } from "../../lib/cn";

export interface LockToggleProps {
  locked: boolean;
  onChange: (locked: boolean) => void;
  /** what is being locked, for the tooltip and screen readers: "this paragraph", "the image prompt" */
  subject: string;
  size?: "sm" | "md";
  className?: string;
}

/**
 * Lock / unlock button. A locked item is kept as it is when the stage is regenerated;
 * the accent colour marks the locked state so it stands out in a grid of cards.
 */
export function LockToggle({ locked, onChange, subject, size = "md", className }: LockToggleProps) {
  const title = locked ? `Unlock ${subject} so a regenerate may change it` : `Lock ${subject} so a regenerate keeps it`;
  return (
    <button
      type="button"
      aria-pressed={locked}
      aria-label={title}
      title={title}
      onClick={() => onChange(!locked)}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-md border transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
        size === "sm" ? "size-6 [&_svg]:size-3" : "size-7 [&_svg]:size-3.5",
        locked
          ? "border-accent/50 bg-accent/15 text-accent-text hover:bg-accent/25"
          : "border-line bg-surface text-ink-faint hover:border-line-strong hover:text-ink",
        className,
      )}
    >
      {locked ? <Lock aria-hidden /> : <LockOpen aria-hidden />}
    </button>
  );
}

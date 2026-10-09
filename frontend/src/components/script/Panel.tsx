import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

export interface PanelProps {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  bodyClassName?: string;
  children?: ReactNode;
}

/**
 * A bordered block with an optional header, lighter than a Card: the review panels render
 * inside the project page's stage Card, so they group their parts without stacking shadows.
 */
export function Panel({ title, description, actions, className, bodyClassName, children }: PanelProps) {
  const hasHeader = Boolean(title || description || actions);
  return (
    <section className={cn("rounded-md border border-line bg-surface", className)}>
      {hasHeader ? (
        <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-line px-4 py-3">
          <div className="min-w-0">
            {title ? <h3 className="text-sm font-semibold text-ink">{title}</h3> : null}
            {description ? <div className="mt-0.5 text-xs text-ink-muted">{description}</div> : null}
          </div>
          {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
        </header>
      ) : null}
      <div className={cn("px-4 py-4", bodyClassName)}>{children}</div>
    </section>
  );
}

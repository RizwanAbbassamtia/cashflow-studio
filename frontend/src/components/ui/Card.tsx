import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

export interface CardProps {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  bodyClassName?: string;
  /** remove body padding, e.g. for a table that should touch the edges */
  flush?: boolean;
  id?: string;
  children?: ReactNode;
}

export function Card({ title, description, actions, className, bodyClassName, flush, id, children }: CardProps) {
  const hasHeader = Boolean(title || description || actions);
  return (
    <section
      id={id}
      className={cn("rounded-card border border-line bg-surface shadow-card", className)}
    >
      {hasHeader ? (
        <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            {title ? <h2 className="text-[15px] font-semibold text-ink">{title}</h2> : null}
            {description ? <p className="mt-0.5 text-[13px] text-ink-muted">{description}</p> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </header>
      ) : null}
      <div className={cn(!flush && "px-5 py-5", bodyClassName)}>{children}</div>
    </section>
  );
}

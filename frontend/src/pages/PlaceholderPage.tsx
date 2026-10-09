import type { LucideIcon } from "lucide-react";

import { Badge } from "../components/ui/Badge";
import { LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";

export interface PlaceholderPageProps {
  title: string;
  milestone: string;
  summary: string;
  icon: LucideIcon;
  /** what the user can do right now instead */
  meanwhile?: { label: string; to: string };
}

/** Stand-in for a page that a later milestone brings. */
export function PlaceholderPage({ title, milestone, summary, icon: Icon, meanwhile }: PlaceholderPageProps) {
  return (
    <Card className="mx-auto max-w-2xl">
      <div className="flex flex-col items-center gap-4 py-10 text-center">
        <div className="flex size-14 items-center justify-center rounded-full bg-surface-2 text-accent-text">
          <Icon className="size-7" aria-hidden />
        </div>
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-ink">{title}</h2>
          <Badge tone="accent">Arrives in {milestone}</Badge>
        </div>
        <p className="max-w-md text-balance text-sm text-ink-muted">{summary}</p>
        <p className="text-xs text-ink-faint">This page is a placeholder in milestone M0. Milestone {milestone} fills it in.</p>
        {meanwhile ? (
          <LinkButton to={meanwhile.to} variant="secondary" size="sm">
            {meanwhile.label}
          </LinkButton>
        ) : null}
      </div>
    </Card>
  );
}

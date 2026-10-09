import { ExternalLink, Play, Sparkles } from "lucide-react";

import type { ProjectFormat } from "../../types/project";
import type { Candidate } from "../../types/research";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { formatAge, formatDuration, formatMultiple, formatViews, formatWhole } from "./numbers";
import { OutlierBadge } from "./OutlierBadge";

export interface AiPickCardProps {
  pick: Candidate;
  format: ProjectFormat;
  onStart: () => void;
  starting: boolean;
  disabled?: boolean;
}

/** The one video the AI would start production from, with the Start production button. */
export function AiPickCard({ pick, format, onStart, starting, disabled }: AiPickCardProps) {
  return (
    <section className="rounded-card border border-accent/50 bg-accent/5 p-5 shadow-card" aria-label="AI pick">
      <div className="flex flex-wrap items-start gap-5">
        <div className={pick.format === "shorts" ? "h-40 w-[90px] shrink-0 overflow-hidden rounded-md bg-surface-2" : "h-[108px] w-48 shrink-0 overflow-hidden rounded-md bg-surface-2"}>
          {pick.thumbnail_url ? <img src={pick.thumbnail_url} alt="" className="size-full object-cover" /> : null}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="accent">
              <Sparkles className="size-3" aria-hidden />
              AI pick
            </Badge>
            <OutlierBadge label={pick.label} />
            <span className="text-xs text-ink-muted">Rank #{pick.rank} of all competitors</span>
          </div>
          <h3 className="mt-2 text-balance text-base font-semibold text-ink">
            <a href={pick.url} target="_blank" rel="noreferrer" className="hover:underline">
              {pick.title}
              <ExternalLink className="ml-1.5 inline size-3.5 text-ink-faint" aria-hidden />
            </a>
          </h3>
          <p className="mt-1 text-sm text-ink-muted">
            {pick.channel_name} - {formatViews(pick.views)} views ({formatMultiple(pick.outlier_score)} the channel&apos;s usual {formatWhole(pick.baseline_views)}) - {formatAge(pick.age_days)} old
            {pick.duration_s ? ` - ${formatDuration(pick.duration_s)}` : ""}
          </p>
          <p className="mt-3 text-[13px] text-ink-muted">
            Production starts here: research saves this video&apos;s details and transcript, then the title stage writes new titles for your
            {format === "shorts" ? " Short" : " long video"}. Every later stage still waits for your approval where the channel says so.
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <Button variant="primary" icon={<Play />} onClick={onStart} loading={starting} disabled={disabled}>
            Start production with the AI pick
          </Button>
          <span className="text-[11px] text-ink-faint">Or pick another row below</span>
        </div>
      </div>
    </section>
  );
}

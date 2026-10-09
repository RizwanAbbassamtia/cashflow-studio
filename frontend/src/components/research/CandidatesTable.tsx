import { Check, ExternalLink, Sparkles } from "lucide-react";
import { useState } from "react";

import { cn } from "../../lib/cn";
import type { Candidate } from "../../types/research";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Table, TBody, TD, TH, THead, TR } from "../ui/Table";
import { formatAge, formatDuration, formatMultiple, formatViews, formatWhole } from "./numbers";
import { OutlierBadge } from "./OutlierBadge";

export interface CandidatesTableProps {
  candidates: Candidate[];
  /** the AI pick, highlighted */
  pickVideoId: string | null;
  /** the person's choice when it differs from the AI pick (review mode) */
  selectedVideoId?: string | null;
  onUse?: (candidate: Candidate) => void;
  useLabel?: string;
  /** row whose action is in flight */
  busyVideoId?: string | null;
  disabled?: boolean;
  /** show rows the picker excluded (too new, wrong length, used before), dimmed */
  showExcluded?: boolean;
}

function Thumbnail({ candidate }: { candidate: Candidate }) {
  const [failed, setFailed] = useState(false);
  const isShort = candidate.format === "shorts";
  return (
    <div className={cn("relative shrink-0 overflow-hidden rounded-md bg-surface-2", isShort ? "h-16 w-10" : "h-14 w-24")}>
      {candidate.thumbnail_url && !failed ? (
        <img src={candidate.thumbnail_url} alt="" loading="lazy" className="size-full object-cover" onError={() => setFailed(true)} />
      ) : null}
      {candidate.duration_s ? (
        <span className="absolute bottom-0.5 right-0.5 rounded bg-navy/80 px-1 text-[10px] font-medium tabular-nums text-white">{formatDuration(candidate.duration_s)}</span>
      ) : null}
    </div>
  );
}

/**
 * The ranked candidates across all competitors. Thumbnail, title, channel, views, age,
 * outlier score with its label, and whether this channel used the video before.
 */
export function CandidatesTable({
  candidates,
  pickVideoId,
  selectedVideoId,
  onUse,
  useLabel = "Use this one",
  busyVideoId,
  disabled,
  showExcluded = false,
}: CandidatesTableProps) {
  const rows = [...candidates]
    .filter((candidate) => showExcluded || !candidate.excluded_reason)
    .sort((a, b) => a.rank - b.rank);
  const chosen = selectedVideoId ?? pickVideoId;

  return (
    <Table>
      <THead>
        <TR>
          <TH className="w-12 text-right">#</TH>
          <TH>Video</TH>
          <TH className="w-[170px]">Channel</TH>
          <TH className="w-[90px] text-right">Views</TH>
          <TH className="w-[80px] text-right">Age</TH>
          <TH className="w-[200px]">Outlier</TH>
          <TH className="w-[120px]">Used before</TH>
          {onUse ? <TH className="w-[150px]" /> : null}
        </TR>
      </THead>
      <TBody>
        {rows.map((candidate) => {
          const isPick = candidate.video_id === pickVideoId;
          const isChosen = candidate.video_id === chosen;
          const excluded = Boolean(candidate.excluded_reason);
          return (
            <TR
              key={candidate.video_id}
              className={cn(
                isChosen && "bg-accent/10",
                isChosen && "shadow-[inset_3px_0_0_0_var(--color-accent)]",
                excluded && "opacity-55",
              )}
              aria-selected={isChosen || undefined}
            >
              <TD className="text-right tabular-nums text-ink-faint">{candidate.rank}</TD>
              <TD>
                <div className="flex items-start gap-3">
                  <Thumbnail candidate={candidate} />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      {isPick ? (
                        <Badge tone="accent">
                          <Sparkles className="size-3" aria-hidden />
                          AI pick
                        </Badge>
                      ) : null}
                      {isChosen && !isPick ? (
                        <Badge tone="ok">
                          <Check className="size-3" aria-hidden />
                          Your pick
                        </Badge>
                      ) : null}
                    </div>
                    <a
                      href={candidate.url}
                      target="_blank"
                      rel="noreferrer"
                      className={cn("line-clamp-2 text-sm font-medium text-ink hover:underline", isPick && "mt-1")}
                      title="Open on YouTube"
                    >
                      {candidate.title}
                      <ExternalLink className="ml-1 inline size-3 text-ink-faint" aria-hidden />
                    </a>
                    {excluded ? <p className="mt-0.5 text-xs text-warn">Left out: {candidate.excluded_reason}</p> : null}
                  </div>
                </div>
              </TD>
              <TD>
                <a href={candidate.channel_url} target="_blank" rel="noreferrer" className="line-clamp-2 text-ink-muted hover:text-ink hover:underline">
                  {candidate.channel_name}
                </a>
              </TD>
              <TD className="text-right tabular-nums">
                <span title={`${formatWhole(candidate.views)} views${candidate.views_exact ? "" : " (rounded)"}`}>{formatViews(candidate.views)}</span>
                <div className="text-[11px] text-ink-faint" title="Views per day">
                  {formatViews(candidate.vpd)}/day
                </div>
              </TD>
              <TD className="text-right tabular-nums text-ink-muted">{formatAge(candidate.age_days)}</TD>
              <TD>
                <div className="flex items-center gap-2">
                  <span className="w-12 text-right text-sm font-semibold tabular-nums text-ink" title={`${formatWhole(candidate.views)} views against a usual ${formatWhole(candidate.baseline_views)} on this channel`}>
                    {formatMultiple(candidate.outlier_score)}
                  </span>
                  <OutlierBadge label={candidate.label} />
                </div>
                <div className="mt-0.5 text-[11px] text-ink-faint" title="Views per day compared with the channel's recent videos">
                  pace {formatMultiple(candidate.vpd_ratio)}
                </div>
              </TD>
              <TD>
                {candidate.used_before ? (
                  <Badge tone="warn" dot>
                    Used before
                  </Badge>
                ) : (
                  <span className="text-xs text-ink-faint">New for us</span>
                )}
              </TD>
              {onUse ? (
                <TD className="text-right">
                  {isChosen ? (
                    <span className="inline-flex items-center gap-1 text-xs font-medium text-accent-text">
                      <Check className="size-3.5" aria-hidden />
                      Selected
                    </span>
                  ) : (
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => onUse(candidate)}
                      loading={busyVideoId === candidate.video_id}
                      disabled={disabled || (Boolean(busyVideoId) && busyVideoId !== candidate.video_id)}
                    >
                      {useLabel}
                    </Button>
                  )}
                </TD>
              ) : null}
            </TR>
          );
        })}
      </TBody>
    </Table>
  );
}

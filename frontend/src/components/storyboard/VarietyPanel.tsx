import { CheckCircle2, TriangleAlert } from "lucide-react";

import { cn } from "../../lib/cn";
import { MIN_TEXT_SHARE, type StoryboardVariety } from "../../types";
import { formatSeconds, type SceneBand, type SceneIssue } from "./storyboardUtils";

export interface VarietyPanelProps {
  /** computed from the draft on the board */
  variety: StoryboardVariety;
  /** what the backend found when it wrote the file */
  savedWarnings: string[];
  issues: SceneIssue[];
  band: SceneBand;
  onJumpToScene: (index: number) => void;
}

function Stat({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <div className="rounded-md border border-line bg-surface-2/40 px-3 py-2">
      <dt className="text-[11px] uppercase tracking-wide text-ink-muted">{label}</dt>
      <dd className={cn("mt-0.5 text-base font-semibold tabular-nums", ok ? "text-ink" : "text-warn")}>{value}</dd>
    </div>
  );
}

/**
 * Variety is a monetisation requirement: a run of same-looking scenes reads as a slideshow.
 * This panel shows the numbers and every warning, each one a link to the scene it is about.
 */
export function VarietyPanel({ variety, savedWarnings, issues, band, onJumpToScene }: VarietyPanelProps) {
  const [minS, maxS] = band;
  const avgOk = variety.scenes === 0 || (variety.avg_scene_s >= minS && variety.avg_scene_s <= maxS);
  const shareOk = variety.popup_share >= MIN_TEXT_SHARE;
  const transitionsOk = variety.scenes < 4 || variety.distinct_transitions >= 3;
  const motionsOk = variety.scenes < 4 || variety.distinct_motions >= 3;
  const clean = variety.warnings.length === 0 && issues.length === 0;

  return (
    <div className="flex flex-col gap-4">
      <dl className="grid grid-cols-2 gap-2">
        <Stat label="Scenes" value={String(variety.scenes)} ok />
        <Stat label="Average length" value={formatSeconds(variety.avg_scene_s)} ok={avgOk} />
        <Stat label="With text" value={`${Math.round(variety.popup_share * 100)}%`} ok={shareOk} />
        <Stat label="Transitions used" value={String(variety.distinct_transitions)} ok={transitionsOk} />
        <Stat label="Motions used" value={String(variety.distinct_motions)} ok={motionsOk} />
      </dl>

      {clean ? (
        <p className="flex items-center gap-2 text-[13px] text-ok">
          <CheckCircle2 className="size-4" aria-hidden />
          Good variety: no repeated moves, lengths in the {minS}-{maxS} s band, enough on-screen text.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {variety.warnings.length > 0 ? (
            <ul className="flex flex-col gap-1.5">
              {variety.warnings.map((warning, index) => (
                <li key={index} className="flex items-start gap-2 text-[13px] text-ink">
                  <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
                  <span>{warning}</span>
                </li>
              ))}
            </ul>
          ) : null}
          {issues.length > 0 ? (
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted">By scene</p>
              <ul className="mt-1.5 flex flex-col gap-1">
                {issues.map((issue, index) => (
                  <li key={index}>
                    <button
                      type="button"
                      onClick={() => onJumpToScene(issue.index)}
                      className="flex w-full items-start gap-2 rounded-md px-2 py-1 text-left text-[13px] text-ink hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
                    >
                      <span className="mt-px shrink-0 rounded bg-surface-2 px-1.5 text-[11px] font-semibold tabular-nums text-ink-muted">{issue.index + 1}</span>
                      <span className="text-ink-muted">{issue.message}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}

      {savedWarnings.length > 0 ? (
        <details className="text-xs text-ink-muted">
          <summary className="cursor-pointer select-none hover:text-ink">What the app reported when it wrote this storyboard ({savedWarnings.length})</summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-4">
            {savedWarnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

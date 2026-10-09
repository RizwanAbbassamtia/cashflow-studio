import { Clapperboard, ExternalLink, Play } from "lucide-react";

import type { RenderProgress } from "../../api/edit";
import { cn } from "../../lib/cn";
import { isRenderPreset, RENDER_PRESET_DESCRIPTIONS, RENDER_PRESET_SIZES, renderPresetLabel, type RenderOutput } from "../../types/timeline";
import type { StoryboardAspect } from "../../types/storyboard";
import { ProgressBar } from "../projects/ProgressBar";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { formatBytes, formatDuration, RENDER_STATUS_META } from "./editUtils";

export interface RenderPresetsProps {
  /** preset ids to offer, in order */
  available: string[];
  /** presets ticked for this project */
  selected: string[];
  renders: RenderOutput[];
  aspect: StoryboardAspect | null;
  /** 4K may be ticked (Settings > Render, or the payload) */
  enable4k: boolean;
  /** the stage is running right now */
  running: boolean;
  progress: RenderProgress | null;
  /** another request is in flight */
  busy: boolean;
  /** true when the stage can be rendered again (not done and locked) */
  canRender: boolean;
  mediaUrl: (render: RenderOutput) => string | null;
  onToggle: (preset: string, checked: boolean) => void;
  onRender: (presets: string[]) => void;
}

/**
 * One row per preset: a checkbox (what the final export includes), its status, a progress
 * bar while it renders, a play link once it is done and a Render button. "Render selected"
 * renders every ticked preset in one go.
 */
export function RenderPresets({ available, selected, renders, aspect, enable4k, running, progress, busy, canRender, mediaUrl, onToggle, onRender }: RenderPresetsProps) {
  const byPreset = new Map(renders.map((render) => [render.preset, render] as const));
  const portrait = aspect === "9:16";
  const lockedUi = running || busy || !canRender;

  return (
    <div className="flex flex-col gap-3">
      <ul className="divide-y divide-line rounded-md border border-line">
        {available.map((preset) => {
          const render = byPreset.get(preset);
          const checked = selected.includes(preset);
          const is4k = preset === "2160p";
          const blocked4k = is4k && !enable4k;
          const size = isRenderPreset(preset) ? RENDER_PRESET_SIZES[preset][portrait ? "portrait" : "landscape"] : null;
          const renderingThis = running && progress?.preset === preset;
          const status = renderingThis ? "rendering" : (render?.status ?? "not_rendered");
          const meta = RENDER_STATUS_META[status];
          const url = render ? mediaUrl(render) : null;
          const pct = renderingThis ? progress?.pct : status === "rendering" ? (render?.progress ?? null) : null;
          return (
            <li key={preset} className={cn("flex flex-col gap-2 px-4 py-3", checked && "bg-surface-2/40")}>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <label className={cn("flex min-w-[180px] flex-1 items-start gap-3", blocked4k && "opacity-60")}>
                  <input
                    type="checkbox"
                    className="mt-1 size-4 accent-accent"
                    checked={checked}
                    disabled={blocked4k || lockedUi}
                    onChange={(event) => onToggle(preset, event.target.checked)}
                    aria-label={`Include ${renderPresetLabel(preset)} in the export`}
                  />
                  <span className="min-w-0">
                    <span className="flex items-center gap-2 text-sm font-medium text-ink">
                      {renderPresetLabel(preset)}
                      {size ? <span className="text-xs font-normal text-ink-faint">{size[0]} x {size[1]}</span> : null}
                    </span>
                    <span className="block text-xs text-ink-muted">
                      {blocked4k ? "Turn on 4K under Settings > Render to use this size." : isRenderPreset(preset) ? RENDER_PRESET_DESCRIPTIONS[preset] : "A size from the backend's render presets."}
                    </span>
                  </span>
                </label>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={meta.tone} dot>
                    {meta.label}
                  </Badge>
                  {status === "done" && render ? (
                    <span className="text-xs tabular-nums text-ink-faint">
                      {[render.duration_s !== null ? formatDuration(render.duration_s) : "", formatBytes(render.size_bytes)].filter(Boolean).join(" - ")}
                    </span>
                  ) : null}
                  {status === "done" && url ? (
                    <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-medium text-accent-text hover:underline">
                      <Play className="size-3.5" aria-hidden />
                      Play
                      <ExternalLink className="size-3" aria-hidden />
                    </a>
                  ) : null}
                  <Button
                    variant="secondary"
                    size="sm"
                    icon={<Clapperboard />}
                    disabled={blocked4k || lockedUi}
                    onClick={() => onRender([preset])}
                    title={status === "done" ? "Render this size again with the choices above" : "Render this size now"}
                  >
                    {status === "done" ? "Render again" : "Render"}
                  </Button>
                </div>
              </div>
              {status === "rendering" ? <ProgressBar value={pct ?? null} label={renderingThis ? progress?.message : `Rendering ${renderPresetLabel(preset)}...`} /> : null}
              {status === "failed" && render?.error ? <p className="text-xs text-fail">{render.error}</p> : null}
            </li>
          );
        })}
      </ul>
      {running && progress && !progress.preset ? <ProgressBar value={progress.pct} label={progress.message} /> : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-ink-muted">
          Ticked sizes are what the export includes. The proxy preview is always made; each size is rendered on this computer, one after the other.
        </p>
        <Button variant="primary" size="sm" icon={<Clapperboard />} disabled={selected.length === 0 || lockedUi} loading={running} onClick={() => onRender(selected)}>
          {running ? "Rendering..." : `Render selected (${selected.length})`}
        </Button>
      </div>
    </div>
  );
}

import { Check, ExternalLink, ImageOff, Smartphone } from "lucide-react";
import { useEffect, useState } from "react";

import { thumbnailUrl } from "../../api/export";
import { cn } from "../../lib/cn";
import type { ThumbnailChoice, ThumbnailVariant } from "../../types/export";
import { Badge } from "../ui/Badge";
import { Input } from "../ui/Input";

export interface ThumbnailPickerProps {
  projectId: string;
  variants: ThumbnailVariant[];
  choice: ThumbnailChoice;
  onChoose: (id: ThumbnailChoice) => void;
  /** the headline drawn on the chosen thumbnail (draft) */
  headline: string;
  /** what the stage drew, to show whether the draft differs */
  originalHeadline: string;
  onHeadlineChange: (text: string) => void;
  /** channel rule for the number of words, when known */
  maxWords: number | null;
  disabled?: boolean;
}

const VARIANT_LABELS: Record<ThumbnailChoice, string> = { v1: "Variant 1", v2: "Variant 2", v3: "Variant 3" };

function countWords(text: string): number {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

function Picture({ src, alt }: { src: string | null; alt: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  if (!src || failed) {
    return (
      <div className="flex aspect-video w-full flex-col items-center justify-center gap-1 bg-gradient-to-br from-surface-2 to-canvas text-ink-faint">
        <ImageOff className="size-5" aria-hidden />
        <span className="text-[11px]">{failed ? "Could not load the picture" : "No picture yet"}</span>
      </div>
    );
  }
  return <img src={src} alt={alt} className="aspect-video w-full object-cover" loading="lazy" onError={() => setFailed(true)} />;
}

/**
 * The three thumbnail variants as big radio cards, plus the headline box for the chosen
 * one. A changed headline is redrawn on the thumbnail when the export is approved.
 */
export function ThumbnailPicker({ projectId, variants, choice, onChoose, headline, originalHeadline, onHeadlineChange, maxWords, disabled }: ThumbnailPickerProps) {
  const chosen = variants.find((variant) => variant.id === choice) ?? variants[0];
  const words = countWords(headline);
  const tooMany = maxWords !== null && words > maxWords;
  const changed = headline.trim() !== originalHeadline.trim();
  const shortsUrl = chosen ? thumbnailUrl(projectId, chosen, true) : null;

  return (
    <div className="flex flex-col gap-4">
      <div role="radiogroup" aria-label="Thumbnail" className="grid gap-3 sm:grid-cols-3">
        {variants.map((variant) => {
          const selected = variant.id === choice;
          const src = thumbnailUrl(projectId, variant);
          return (
            <button
              key={variant.id}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled}
              onClick={() => onChoose(variant.id)}
              className={cn(
                "flex flex-col overflow-hidden rounded-md border text-left transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:cursor-not-allowed",
                selected ? "border-accent ring-2 ring-accent/40" : "border-line hover:border-line-strong",
              )}
            >
              <div className="relative">
                <Picture src={src} alt={`${VARIANT_LABELS[variant.id]}${variant.headline ? `: ${variant.headline}` : ""}`} />
                {selected ? (
                  <span className="absolute right-2 top-2 flex size-6 items-center justify-center rounded-full bg-accent text-accent-ink shadow-sm">
                    <Check className="size-3.5" aria-hidden />
                  </span>
                ) : null}
              </div>
              <div className="flex items-start justify-between gap-2 px-3 py-2">
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-ink">{VARIANT_LABELS[variant.id]}</p>
                  <p className="truncate text-xs text-ink-muted" title={variant.headline}>
                    {variant.headline ? `"${variant.headline}"` : "No headline recorded"}
                  </p>
                </div>
                {selected ? <Badge tone="accent">Chosen</Badge> : null}
              </div>
            </button>
          );
        })}
      </div>

      <div className="grid gap-x-6 gap-y-3 md:grid-cols-[1fr_auto] md:items-end">
        <label className="flex flex-col gap-1.5">
          <span className="flex items-center justify-between text-[13px] font-medium text-ink-muted">
            Headline on the chosen thumbnail
            <span className={cn("text-xs tabular-nums", tooMany ? "text-fail" : "text-ink-faint")}>
              {words} word{words === 1 ? "" : "s"}
              {maxWords !== null ? ` / ${maxWords}` : ""}
            </span>
          </span>
          <Input value={headline} disabled={disabled} invalid={tooMany} placeholder={chosen?.headline || "Short, bold words"} onChange={(event) => onHeadlineChange(event.target.value)} />
          <span className="text-xs text-ink-faint">
            {changed ? "The text is redrawn on the thumbnail when you approve the export." : "Keep it to a few big words; the picture stays as it is."}
            {tooMany ? ` The channel allows ${maxWords} words.` : ""}
          </span>
        </label>
        {shortsUrl ? (
          <a href={shortsUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-xs font-medium text-accent-text hover:underline">
            <Smartphone className="size-3.5" aria-hidden />
            Shorts version (9:16)
            <ExternalLink className="size-3" aria-hidden />
          </a>
        ) : null}
      </div>
    </div>
  );
}

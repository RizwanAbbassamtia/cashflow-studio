import { ImageIcon, ImageOff, Lock, RefreshCw, Upload, X } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "../../lib/cn";
import { qaFlags, qaPasses, type SceneImageRecord } from "../../types/images";
import type { StoryboardAspect } from "../../types/storyboard";
import { formatUsd } from "../projects/stageMeta";
import { LockToggle } from "../script/LockToggle";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { FilePickButton } from "../voice/FilePickButton";
import { IMAGE_FILE_ACCEPT, sceneLabel, type PendingUpload } from "./imagesUtils";
import { QaPill } from "./QaPill";

export interface ImageSceneCardProps {
  scene: SceneImageRecord;
  aspect: StoryboardAspect;
  selected: boolean;
  onSelect: (selected: boolean) => void;
  locked: boolean;
  onLock: (locked: boolean) => void;
  /** the note queued for a regenerate, or null */
  pendingNote: string | null;
  pendingUpload: PendingUpload | null;
  uploading: boolean;
  disabled: boolean;
  onRegenerate: () => void;
  onUpload: (file: File) => void;
  onClearRegenerate: () => void;
  onClearUpload: () => void;
}

function ScenePicture({ src, aspect, alt, missingText }: { src: string | null; aspect: StoryboardAspect; alt: string; missingText: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  const show = Boolean(src) && !failed;
  return (
    <div className={cn("relative w-full overflow-hidden rounded-md border border-line bg-canvas", aspect === "9:16" ? "aspect-[9/16] max-h-80" : "aspect-video")}>
      {show ? (
        <img src={src ?? undefined} alt={alt} className="size-full object-cover" loading="lazy" onError={() => setFailed(true)} />
      ) : (
        <div className="flex size-full flex-col items-center justify-center gap-2 bg-gradient-to-br from-surface-2 to-canvas p-4 text-center">
          {failed ? <ImageOff className="size-6 text-ink-faint" aria-hidden /> : <ImageIcon className="size-6 text-ink-faint" aria-hidden />}
          <p className="line-clamp-3 text-xs leading-5 text-ink-muted">{failed ? "The picture file could not be loaded." : missingText}</p>
        </div>
      )}
    </div>
  );
}

/**
 * One scene of the images grid: the picture (or the uploaded replacement), the QA verdict,
 * the attempt count, the lock, the queued changes and the per-scene actions. The card does
 * not talk to the server; the board collects its changes and sends them on approve.
 */
export function ImageSceneCard({
  scene,
  aspect,
  selected,
  onSelect,
  locked,
  onLock,
  pendingNote,
  pendingUpload,
  uploading,
  disabled,
  onRegenerate,
  onUpload,
  onClearRegenerate,
  onClearUpload,
}: ImageSceneCardProps) {
  const label = sceneLabel(scene.scene);
  const flags = scene.qa && !qaPasses(scene.qa) ? qaFlags(scene.qa) : [];
  const src = pendingUpload?.previewUrl ?? scene.image_url;
  const details = [scene.provider, scene.model, scene.seed !== null ? `seed ${scene.seed}` : "", scene.cost_usd > 0 ? formatUsd(scene.cost_usd) : ""].filter(Boolean).join(" / ");
  const provenance = [scene.provenance.c2pa ? "C2PA manifest" : "", scene.provenance.synthid ? "SynthID watermark" : ""].filter(Boolean).join(", ");
  const problems = [scene.error, scene.duplicate_of, ...scene.notes].filter((item): item is string => Boolean(item));

  return (
    <article
      id={`image-scene-${scene.scene}`}
      className={cn(
        "flex flex-col gap-3 rounded-card border bg-surface p-3 shadow-card transition-[border-color]",
        selected ? "border-accent/60" : pendingNote !== null || pendingUpload ? "border-info/40" : "border-line",
      )}
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <input type="checkbox" className="accent-accent" aria-label={`Select ${label}`} checked={selected} disabled={disabled} onChange={(event) => onSelect(event.target.checked)} />
        <span className="text-sm font-semibold text-ink">{label}</span>
        <QaPill status={pendingUpload ? "approved" : scene.status} source={pendingUpload ? "upload" : scene.source} qa={pendingUpload ? null : scene.qa} verdict={pendingUpload ? "Uploaded" : scene.verdict} />
        {scene.attempts > 0 ? (
          <Badge tone={scene.attempts > 1 ? "warn" : "neutral"}>
            {scene.attempts} {scene.attempts === 1 ? "try" : "tries"}
          </Badge>
        ) : null}
        <LockToggle size="sm" locked={locked} onChange={onLock} subject={`the picture of ${label.toLowerCase()}`} className="ml-auto" />
      </header>

      <div className="relative">
        <ScenePicture src={src} aspect={aspect} alt={scene.prompt || `${label} picture`} missingText={scene.prompt || "No picture was made for this scene yet."} />
        <div className="pointer-events-none absolute inset-x-2 top-2 flex items-start justify-between gap-2">
          {pendingUpload ? (
            <Badge tone="info" className="bg-surface/90 backdrop-blur">
              Your upload
            </Badge>
          ) : pendingNote !== null ? (
            <Badge tone="warn" className="bg-surface/90 backdrop-blur">
              Will be made again
            </Badge>
          ) : (
            <span />
          )}
          {locked ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-navy/70 px-2 py-0.5 text-[11px] font-medium text-white backdrop-blur">
              <Lock className="size-3" aria-hidden />
              Locked
            </span>
          ) : null}
        </div>
      </div>

      {pendingNote !== null ? (
        <div className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-ink">
          <RefreshCw className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="font-medium">Will be made again.</span> {pendingNote || "No note."}
          </span>
          <button type="button" aria-label="Do not regenerate this scene" title="Undo" className="rounded p-0.5 text-ink-faint hover:bg-surface-2 hover:text-ink" onClick={onClearRegenerate} disabled={disabled}>
            <X className="size-3.5" aria-hidden />
          </button>
        </div>
      ) : null}

      {pendingUpload ? (
        <div className="flex items-start gap-2 rounded-md border border-info/30 bg-info/10 px-3 py-2 text-xs text-ink">
          <Upload className="mt-0.5 size-3.5 shrink-0 text-info" aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="font-medium">Replaced by</span> <span className="break-all">{pendingUpload.name}</span>
          </span>
          <button type="button" aria-label="Drop the uploaded picture" title="Undo" className="rounded p-0.5 text-ink-faint hover:bg-surface-2 hover:text-ink" onClick={onClearUpload} disabled={disabled}>
            <X className="size-3.5" aria-hidden />
          </button>
        </div>
      ) : null}

      {scene.narration ? <p className="line-clamp-2 text-[13px] leading-5 text-ink">{scene.narration}</p> : null}

      {flags.length > 0 ? <p className="text-xs font-medium text-fail">{flags.join(" - ")}</p> : null}
      {scene.qa?.reason ? <p className="line-clamp-3 text-xs text-ink-muted">{scene.qa.reason}</p> : scene.verdict && scene.status === "rejected" ? <p className="line-clamp-3 text-xs text-ink-muted">{scene.verdict}</p> : null}
      {problems.map((problem, index) => (
        <p key={`${index}-${problem.slice(0, 20)}`} className="text-xs text-warn">
          {problem}
        </p>
      ))}

      <details className="text-xs text-ink-muted">
        <summary className="cursor-pointer select-none font-medium hover:text-ink">Prompt and details</summary>
        <div className="mt-2 flex flex-col gap-2">
          <p className="whitespace-pre-wrap break-words">{scene.prompt || "No prompt recorded."}</p>
          {scene.negative_prompt ? (
            <p>
              <span className="font-medium">Avoid: </span>
              {scene.negative_prompt}
            </p>
          ) : null}
          {details ? <p className="font-mono text-[11px]">{details}</p> : null}
          {provenance ? <p>{provenance}</p> : null}
          {scene.file ? <p className="break-all font-mono text-[11px]">{scene.file}</p> : null}
          {scene.rejected_urls.length > 0 ? (
            <div>
              <p className="font-medium">
                {scene.rejected_urls.length} rejected {scene.rejected_urls.length === 1 ? "try" : "tries"}
              </p>
              <div className="mt-1 flex flex-wrap gap-1">
                {scene.rejected_urls.map((url) => (
                  <a key={url} href={url} target="_blank" rel="noreferrer" className="block size-14 overflow-hidden rounded border border-line bg-canvas" title="Open the rejected picture">
                    <img src={url} alt="" className="size-full object-cover" loading="lazy" />
                  </a>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </details>

      <footer className="mt-auto flex flex-wrap items-center gap-2">
        <Button variant="secondary" size="sm" icon={<RefreshCw />} onClick={onRegenerate} disabled={disabled}>
          Regenerate with a note
        </Button>
        <FilePickButton variant="secondary" size="sm" icon={<Upload />} accept={IMAGE_FILE_ACCEPT} onPick={onUpload} loading={uploading} disabled={disabled}>
          Upload image
        </FilePickButton>
      </footer>
    </article>
  );
}

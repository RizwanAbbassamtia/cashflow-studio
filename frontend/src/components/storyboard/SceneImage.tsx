import { ImageIcon, ImageOff } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "../../lib/cn";
import type { SceneImage as SceneImageModel, StoryboardAspect } from "../../types";
import { Badge, type BadgeTone } from "../ui/Badge";

const STATUS: Record<SceneImageModel["status"], { label: string; tone: BadgeTone }> = {
  pending: { label: "No image yet", tone: "neutral" },
  generated: { label: "Generated", tone: "info" },
  approved: { label: "Approved", tone: "ok" },
  rejected: { label: "Rejected", tone: "fail" },
};

export interface SceneImageProps {
  image: SceneImageModel;
  /** resolved http(s) URL or null for the placeholder */
  src: string | null;
  aspect: StoryboardAspect;
  /** shown on the placeholder so the card still tells the story */
  prompt: string;
  motionLabel: string;
  className?: string;
}

/** The scene's picture, or a placeholder that previews the prompt until the images stage runs. */
export function SceneImage({ image, src, aspect, prompt, motionLabel, className }: SceneImageProps) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  const status = STATUS[image.status];
  const showImage = Boolean(src) && !failed;

  return (
    <div
      className={cn(
        "relative w-full overflow-hidden rounded-md border border-line bg-canvas",
        aspect === "9:16" ? "aspect-[9/16] max-h-72" : "aspect-video",
        className,
      )}
    >
      {showImage ? (
        <img src={src ?? undefined} alt={prompt || "Scene image"} className="size-full object-cover" onError={() => setFailed(true)} loading="lazy" />
      ) : (
        <div className="flex size-full flex-col items-center justify-center gap-2 bg-gradient-to-br from-surface-2 to-canvas p-4 text-center">
          {failed ? <ImageOff className="size-6 text-ink-faint" aria-hidden /> : <ImageIcon className="size-6 text-ink-faint" aria-hidden />}
          <p className="line-clamp-3 text-xs leading-5 text-ink-muted">
            {failed ? "The image file could not be loaded." : prompt ? prompt : "No image prompt yet."}
          </p>
        </div>
      )}
      <div className="pointer-events-none absolute inset-x-2 bottom-2 flex items-end justify-between gap-2">
        <Badge tone={status.tone} className="bg-surface/90 backdrop-blur">
          {status.label}
        </Badge>
        <span className="rounded-full bg-navy/70 px-2 py-0.5 text-[11px] font-medium text-white backdrop-blur">{motionLabel}</span>
      </div>
    </div>
  );
}

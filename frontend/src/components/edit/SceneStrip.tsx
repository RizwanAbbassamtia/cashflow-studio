import { ImageIcon, Lock, MessageSquare } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { cn } from "../../lib/cn";
import type { StoryboardAspect } from "../../types/storyboard";
import { formatClock, sceneIndexAt, transitionLabel } from "./editUtils";

export interface SceneStripItem {
  index: number;
  start_s: number;
  end_s: number;
  /** resolved http(s) URL of the scene picture (06_images), or null for a placeholder */
  thumbUrl: string | null;
  transition: string | null;
  popupText: string | null;
  locked: boolean;
}

export interface SceneStripProps {
  scenes: SceneStripItem[];
  /** the player's current time in seconds */
  currentTime: number;
  aspect: StoryboardAspect | null;
  onSeek: (scene: SceneStripItem) => void;
  className?: string;
}

function Thumb({ scene, portrait }: { scene: SceneStripItem; portrait: boolean }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [scene.thumbUrl]);
  const show = Boolean(scene.thumbUrl) && !failed;
  return (
    <div className={cn("relative overflow-hidden rounded bg-canvas", portrait ? "aspect-[9/16] h-24" : "aspect-video w-36")}>
      {show ? (
        <img src={scene.thumbUrl ?? undefined} alt={`Scene ${scene.index + 1}`} className="size-full object-cover" loading="lazy" onError={() => setFailed(true)} />
      ) : (
        <div className="flex size-full items-center justify-center text-ink-faint">
          <ImageIcon className="size-5" aria-hidden />
        </div>
      )}
      <span className="absolute left-1 top-1 rounded bg-navy/70 px-1.5 py-0.5 text-[10px] font-semibold text-white">{scene.index + 1}</span>
      {scene.popupText ? (
        <span className="absolute bottom-1 right-1 rounded bg-navy/70 p-0.5 text-white" title={`Popup: ${scene.popupText}`}>
          <MessageSquare className="size-3" aria-hidden />
        </span>
      ) : null}
      {scene.locked ? (
        <span className="absolute bottom-1 left-1 rounded bg-navy/70 p-0.5 text-white" title="Locked scene">
          <Lock className="size-3" aria-hidden />
        </span>
      ) : null}
    </div>
  );
}

/**
 * One thumbnail per scene under the player, in time order. Clicking a scene seeks the
 * player to its start; the scene playing right now is outlined.
 */
export function SceneStrip({ scenes, currentTime, aspect, onSeek, className }: SceneStripProps) {
  const active = sceneIndexAt(scenes, currentTime);
  const portrait = aspect === "9:16";
  const activeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  }, [active]);

  if (scenes.length === 0) {
    return <p className={cn("text-xs text-ink-faint", className)}>No scenes in the timeline yet.</p>;
  }

  return (
    <ul className={cn("flex gap-2 overflow-x-auto pb-2", className)} aria-label="Scenes">
      {scenes.map((scene, position) => {
        const isActive = position === active;
        return (
          <li key={`${scene.index}-${scene.start_s}`} className="shrink-0">
            <button
              ref={isActive ? activeRef : undefined}
              type="button"
              onClick={() => onSeek(scene)}
              title={`Scene ${scene.index + 1}: ${formatClock(scene.start_s)} to ${formatClock(scene.end_s)}${scene.transition ? `, then ${transitionLabel(scene.transition).toLowerCase()}` : ""}`}
              aria-current={isActive ? "true" : undefined}
              className={cn(
                "flex flex-col gap-1 rounded-md border p-1 text-left transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                isActive ? "border-accent bg-accent/10" : "border-line bg-surface hover:border-line-strong",
              )}
            >
              <Thumb scene={scene} portrait={portrait} />
              <span className="flex items-center justify-between gap-2 px-0.5 text-[11px] tabular-nums text-ink-muted">
                <span>{formatClock(scene.start_s)}</span>
                <span className="text-ink-faint">{(scene.end_s - scene.start_s).toFixed(1)} s</span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

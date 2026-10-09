import { Film, VideoOff } from "lucide-react";
import { useEffect, useState, type RefObject } from "react";

import { cn } from "../../lib/cn";
import type { StoryboardAspect } from "../../types/storyboard";

export interface ProxyPlayerProps {
  /** URL of 07_edit/proxy.mp4, or null while there is none */
  src: string | null;
  aspect: StoryboardAspect | null;
  videoRef: RefObject<HTMLVideoElement | null>;
  onTimeUpdate?: (seconds: number) => void;
  onDurationChange?: (seconds: number) => void;
  className?: string;
}

/**
 * The low-resolution preview of the whole video (proxy render). The browser seeks through
 * the backend's range support; the scene strip under it drives `videoRef`.
 */
export function ProxyPlayer({ src, aspect, videoRef, onTimeUpdate, onDurationChange, className }: ProxyPlayerProps) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  const portrait = aspect === "9:16";

  return (
    <div className={cn("overflow-hidden rounded-md border border-line bg-navy", className)}>
      <div className={cn("mx-auto", portrait ? "aspect-[9/16] max-h-[520px]" : "aspect-video w-full")}>
        {src && !failed ? (
          <video
            key={src}
            ref={videoRef}
            src={src}
            controls
            preload="metadata"
            playsInline
            className="size-full bg-navy"
            onTimeUpdate={(event) => onTimeUpdate?.(event.currentTarget.currentTime)}
            onLoadedMetadata={(event) => onDurationChange?.(event.currentTarget.duration)}
            onDurationChange={(event) => onDurationChange?.(event.currentTarget.duration)}
            onError={() => setFailed(true)}
          >
            Your browser cannot play this video.
          </video>
        ) : (
          <div className="flex size-full flex-col items-center justify-center gap-2 p-6 text-center text-white/80">
            {failed ? <VideoOff className="size-7 text-white/50" aria-hidden /> : <Film className="size-7 text-white/50" aria-hidden />}
            <p className="text-sm font-medium">{failed ? "The preview could not be played" : "No preview video yet"}</p>
            <p className="max-w-xs text-xs text-white/60">
              {failed
                ? "The file 07_edit/proxy.mp4 is missing or damaged. Render again to make a new one."
                : "The edit stage writes 07_edit/proxy.mp4 when it builds the timeline. It shows up here as soon as it exists."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

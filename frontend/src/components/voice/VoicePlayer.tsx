import { Pause, Play, SkipBack } from "lucide-react";
import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";

import { cn } from "../../lib/cn";
import type { VoiceReviewSentence } from "../../types/timing";
import { Button } from "../ui/Button";
import { Notice } from "../ui/States";
import { formatClock } from "./voiceUtils";

/** What the sentence list drives: seek and play a range of the main player. */
export interface VoicePlayerHandle {
  /** seeks to `start` and plays; stops at `end` when given */
  playRange: (start: number, end: number | null) => void;
  seek: (time: number) => void;
  pause: () => void;
}

export interface VoicePlayerProps {
  /** URL of 05_voice/voice.wav (served with range support, so seeking works) */
  src: string;
  durationS: number;
  sentences: VoiceReviewSentence[];
  /** the sentence being spoken, for the highlighted segment */
  activeIndex: number;
  onTime: (time: number) => void;
  onPlayingChange?: (playing: boolean) => void;
  onPickSentence: (index: number) => void;
  ref?: Ref<VoicePlayerHandle>;
}

/**
 * The narration player: the browser's audio controls, a strip with one segment per
 * sentence (click to play it) and a playhead. Range playback stops itself at the end of
 * the sentence; the native controls keep working for free listening.
 */
export function VoicePlayer({ src, durationS, sentences, activeIndex, onTime, onPlayingChange, onPickSentence, ref }: VoicePlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const stopAt = useRef<number | null>(null);
  const [time, setTime] = useState(0);
  const [loadedDuration, setLoadedDuration] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
    setTime(0);
    setLoadedDuration(null);
    stopAt.current = null;
  }, [src]);

  useImperativeHandle(
    ref,
    () => ({
      playRange(start, end) {
        const audio = audioRef.current;
        if (!audio) return;
        stopAt.current = end;
        audio.currentTime = Math.max(0, start);
        void audio.play().catch(() => {
          /* autoplay refused or file missing: the controls still show the state */
        });
      },
      seek(value) {
        const audio = audioRef.current;
        if (!audio) return;
        stopAt.current = null;
        audio.currentTime = Math.max(0, value);
      },
      pause() {
        audioRef.current?.pause();
      },
    }),
    [],
  );

  const total = loadedDuration && loadedDuration > 0 ? loadedDuration : durationS > 0 ? durationS : 1;

  const setPlayingState = (value: boolean) => {
    setPlaying(value);
    onPlayingChange?.(value);
  };

  const onTimeUpdate = () => {
    const audio = audioRef.current;
    if (!audio) return;
    const current = audio.currentTime;
    setTime(current);
    onTime(current);
    if (stopAt.current !== null && current >= stopAt.current - 0.02) {
      stopAt.current = null;
      audio.pause();
    }
  };

  const togglePlay = () => {
    const audio = audioRef.current;
    if (!audio) return;
    stopAt.current = null;
    if (audio.paused) void audio.play().catch(() => {});
    else audio.pause();
  };

  const restart = () => {
    const audio = audioRef.current;
    if (!audio) return;
    stopAt.current = null;
    audio.currentTime = 0;
    void audio.play().catch(() => {});
  };

  return (
    <div className="flex flex-col gap-3">
      <audio
        ref={audioRef}
        key={src}
        src={src}
        controls
        preload="metadata"
        className="w-full"
        onTimeUpdate={onTimeUpdate}
        onLoadedMetadata={(event) => {
          const value = event.currentTarget.duration;
          setLoadedDuration(Number.isFinite(value) && value > 0 ? value : null);
        }}
        onPlay={() => setPlayingState(true)}
        onPause={() => setPlayingState(false)}
        onEnded={() => setPlayingState(false)}
        onError={() => setFailed(true)}
      >
        Your browser cannot play this audio file.
      </audio>

      {failed ? (
        <Notice tone="fail" title="The narration file could not be loaded">
          Check that 05_voice/voice.wav exists in the project folder, then press Check again above.
        </Notice>
      ) : null}

      <div className="flex items-center gap-2">
        <Button variant="secondary" size="sm" icon={playing ? <Pause /> : <Play />} onClick={togglePlay} disabled={failed}>
          {playing ? "Pause" : "Play"}
        </Button>
        <Button variant="ghost" size="sm" icon={<SkipBack />} onClick={restart} disabled={failed}>
          From the start
        </Button>
        <span className="ml-auto text-xs tabular-nums text-ink-muted">
          {formatClock(time)} / {formatClock(total)}
        </span>
      </div>

      {sentences.length > 0 ? (
        <div className="relative h-7 w-full overflow-hidden rounded-md border border-line bg-surface-2" role="group" aria-label="Sentences on the timeline">
          {sentences.map((sentence, index) => {
            const left = Math.max(0, Math.min(100, (sentence.start_s / total) * 100));
            const width = Math.max(0.3, Math.min(100 - left, ((sentence.end_s - sentence.start_s) / total) * 100));
            return (
              <button
                key={sentence.id}
                type="button"
                title={`Sentence ${index + 1}: ${formatClock(sentence.start_s)} to ${formatClock(sentence.end_s)}`}
                aria-label={`Play sentence ${index + 1}`}
                onClick={() => onPickSentence(index)}
                className={cn(
                  "absolute inset-y-1 rounded-sm border-r border-surface transition-colors",
                  index === activeIndex ? "bg-accent" : "bg-accent/35 hover:bg-accent/60",
                )}
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            );
          })}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 w-0.5 bg-ink transition-[left] duration-200 ease-linear"
            style={{ left: `${Math.max(0, Math.min(100, (time / total) * 100))}%` }}
          />
        </div>
      ) : null}
    </div>
  );
}

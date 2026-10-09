import { ChevronDown, ExternalLink, Play, Square } from "lucide-react";
import { useState } from "react";

import { cn } from "../../lib/cn";
import { WORD_SOURCE_LABELS, type VoiceReviewSentence } from "../../types/timing";
import { formatClock, isWordFlagged, wordDuration, wordFlags } from "./voiceUtils";

export interface SentenceListProps {
  sentences: VoiceReviewSentence[];
  /** ids ticked for a re-record */
  selected: ReadonlySet<string>;
  onToggle: (id: string) => void;
  /** the sentence being spoken by the main player */
  activeIndex: number;
  /** the sentence whose range is playing after a per-sentence Play, or null */
  playingIndex: number | null;
  onPlay: (index: number) => void;
  onStop: () => void;
  disabled?: boolean;
}

/**
 * One row per sentence: a tick box for re-recording, the text, start and end, the
 * length, flags from the timing check and a Play button that seeks the main player.
 * The row being spoken is highlighted as the audio runs. Expanding a row shows its words.
 */
export function SentenceList({ sentences, selected, onToggle, activeIndex, playingIndex, onPlay, onStop, disabled = false }: SentenceListProps) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());

  const toggleExpanded = (id: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <ol className="flex flex-col gap-2">
      {sentences.map((sentence, index) => {
        const active = index === activeIndex;
        const isPlaying = index === playingIndex;
        const open = expanded.has(sentence.id);
        // The stage's own flags win; the word-level ones are computed once timing.json is loaded.
        const computed = wordFlags(sentence);
        const flags = sentence.flags.length > 0 ? sentence.flags : [];
        if (sentence.flags.length === 0) {
          if (computed.short > 0) flags.push(`${computed.short} word${computed.short === 1 ? "" : "s"} under 40 ms`);
          if (computed.long > 0) flags.push(`${computed.long} word${computed.long === 1 ? "" : "s"} over 2 s`);
        }
        const words = sentence.word_count > 0 ? sentence.word_count : sentence.words.length;
        const length = Math.max(0, sentence.end_s - sentence.start_s);
        return (
          <li
            key={sentence.id}
            id={`sentence-${index}`}
            className={cn(
              "flex items-start gap-3 rounded-md border px-3 py-2 transition-colors",
              active ? "border-accent bg-accent/5" : "border-line bg-surface",
            )}
          >
            <input
              type="checkbox"
              className="mt-1 accent-accent"
              aria-label={`Select sentence ${index + 1} for re-recording`}
              checked={selected.has(sentence.id)}
              disabled={disabled}
              onChange={() => onToggle(sentence.id)}
            />
            <button
              type="button"
              aria-label={isPlaying ? `Stop sentence ${index + 1}` : `Play sentence ${index + 1}`}
              title={isPlaying ? "Stop" : "Play this sentence"}
              onClick={() => (isPlaying ? onStop() : onPlay(index))}
              className={cn(
                "mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-md border transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                isPlaying ? "border-accent/50 bg-accent/15 text-accent-text" : "border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink",
              )}
            >
              {isPlaying ? <Square className="size-3.5" aria-hidden /> : <Play className="size-3.5" aria-hidden />}
            </button>
            <div className="min-w-0 flex-1">
              <p className="text-[13px] leading-5 text-ink">
                <span className="mr-2 text-[11px] font-semibold tabular-nums text-ink-faint">{index + 1}</span>
                {sentence.text || <span className="italic text-ink-faint">(no text)</span>}
              </p>
              <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] tabular-nums text-ink-muted">
                <span>
                  {formatClock(sentence.start_s)} to {formatClock(sentence.end_s)}
                </span>
                <span>{length.toFixed(1)} s</span>
                <span>
                  {words} word{words === 1 ? "" : "s"}
                </span>
                {sentence.cached ? <span className="text-ink-faint">from cache</span> : null}
                {flags.map((flag) => (
                  <span key={flag} className="text-warn">
                    {flag}
                  </span>
                ))}
              </p>
              {open ? (
                <div className="mt-2 flex flex-col gap-2">
                  {sentence.words.length > 0 ? (
                    <p className="flex flex-wrap gap-1">
                      <span className="mr-1 text-[11px] leading-5 text-ink-faint">{WORD_SOURCE_LABELS[sentence.words_source]}:</span>
                      {sentence.words.map((word, wordIndex) => {
                        const flagged = isWordFlagged(word);
                        const faint = word.confidence < 0.5;
                        return (
                          <span
                            key={`${wordIndex}-${word.text}`}
                            title={`${formatClock(word.start_s)} to ${formatClock(word.end_s)} (${(wordDuration(word) * 1000).toFixed(0)} ms, confidence ${Math.round(word.confidence * 100)}%)`}
                            className={cn(
                              "rounded border px-1 text-[11px] leading-5",
                              flagged ? "border-warn/40 bg-warn/10 text-warn" : "border-line bg-surface-2 text-ink-muted",
                              faint && !flagged && "border-dashed",
                            )}
                          >
                            {word.text}
                          </span>
                        );
                      })}
                    </p>
                  ) : (
                    <p className="text-[11px] text-ink-faint">No word timings for this sentence.</p>
                  )}
                  <a
                    href={sentence.play_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex w-fit items-center gap-1 text-[11px] text-ink-muted underline-offset-2 hover:text-ink hover:underline"
                  >
                    <ExternalLink className="size-3" aria-hidden />
                    Open this sentence&apos;s audio file
                  </a>
                </div>
              ) : null}
            </div>
            <button
              type="button"
              aria-expanded={open}
              aria-label={open ? `Hide the words of sentence ${index + 1}` : `Show the words of sentence ${index + 1}`}
              title={open ? "Hide words" : "Show words"}
              onClick={() => toggleExpanded(sentence.id)}
              className="mt-0.5 rounded p-1 text-ink-faint hover:bg-surface-2 hover:text-ink"
            >
              <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden />
            </button>
          </li>
        );
      })}
    </ol>
  );
}

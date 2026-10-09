import { DURATION_TOLERANCE, MAX_WORD_S, MIN_WORD_S, type TimingSentence, type TimingWord } from "../../types/timing";

/** What the helpers need from a sentence: timing.json rows and review rows both have it. */
export type TimedLike = Pick<TimingSentence, "start_s" | "end_s" | "words">;

/** "0:04.2", "12:03.5": minutes, seconds and tenths, for timings next to a sentence. */
export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00.0";
  const tenths = Math.round(seconds * 10);
  const minutes = Math.floor(tenths / 600);
  const rest = tenths - minutes * 600;
  const whole = Math.floor(rest / 10);
  return `${minutes}:${String(whole).padStart(2, "0")}.${rest % 10}`;
}

/** "32.4 s", "1 min 32 s", "10 min": a length a person reads at a glance. */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0 s";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  let minutes = Math.floor(seconds / 60);
  let rest = Math.round(seconds - minutes * 60);
  if (rest === 60) {
    minutes += 1;
    rest = 0;
  }
  return rest === 0 ? `${minutes} min` : `${minutes} min ${rest} s`;
}

/** "1.2 MB", "840 KB" */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function wordDuration(word: TimingWord): number {
  return Math.max(0, word.end_s - word.start_s);
}

/** The voice gate flags words under 40 ms or over 2 s. */
export function isWordFlagged(word: TimingWord): boolean {
  const duration = wordDuration(word);
  return duration < MIN_WORD_S || duration > MAX_WORD_S;
}

export interface WordFlags {
  short: number;
  long: number;
  lowConfidence: number;
}

export function wordFlags(sentence: TimedLike): WordFlags {
  const flags: WordFlags = { short: 0, long: 0, lowConfidence: 0 };
  for (const word of sentence.words) {
    const duration = wordDuration(word);
    if (duration < MIN_WORD_S) flags.short += 1;
    else if (duration > MAX_WORD_S) flags.long += 1;
    if (word.confidence < 0.5) flags.lowConfidence += 1;
  }
  return flags;
}

export function sumWordFlags(sentences: TimedLike[]): WordFlags {
  const total: WordFlags = { short: 0, long: 0, lowConfidence: 0 };
  for (const sentence of sentences) {
    const flags = wordFlags(sentence);
    total.short += flags.short;
    total.long += flags.long;
    total.lowConfidence += flags.lowConfidence;
  }
  return total;
}

/** The sentence being spoken at `time`, or -1 between sentences. */
export function activeSentenceIndex(sentences: TimedLike[], time: number): number {
  for (let index = 0; index < sentences.length; index += 1) {
    const sentence = sentences[index];
    if (sentence && time >= sentence.start_s && time < sentence.end_s) return index;
  }
  return -1;
}

/** Signed share by which the narration misses the target (0.1 = 10% too long); null without a target. */
export function durationDelta(duration: number, target: number | null): number | null {
  if (target === null || target <= 0) return null;
  return (duration - target) / target;
}

export function durationOutOfBand(duration: number, target: number | null): boolean {
  const delta = durationDelta(duration, target);
  return delta !== null && Math.abs(delta) > DURATION_TOLERANCE;
}

/** "sentence 3", "sentences 2 and 5", "sentences 1, 4 and 9" from 0-based positions. */
export function sentenceNumbers(positions: number[]): string {
  const numbers = [...positions].sort((a, b) => a - b).map((position) => position + 1);
  if (numbers.length === 0) return "no sentences";
  if (numbers.length === 1) return `sentence ${numbers[0]}`;
  const head = numbers.slice(0, -1).join(", ");
  return `sentences ${head} and ${numbers[numbers.length - 1]}`;
}

export function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

/** What the file picker for an own recording accepts; the backend converts with FFmpeg. */
export const AUDIO_FILE_ACCEPT = "audio/*,.wav,.mp3,.m4a,.aac,.flac,.ogg,.opus,.wma,.aiff";

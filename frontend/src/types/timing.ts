/**
 * Voice stage shapes. Mirrors backend/cashcow_studio/models/timing.py (Pydantic v2, the
 * source of truth); see docs/M3-M4-CONTRACT.md section 1.
 *
 * `05_voice/timing.json` (`TimingDoc`) is the clock for everything after the voice stage:
 * scenes, images, popups, captions and music all take their times from it. The review
 * payload of `GET /api/projects/{id}/stage/voice` (`VoiceReviewPayload`) carries the
 * duration, the sentence list with a `play_url` per sentence, the timing source, a
 * confidence flag and warnings; the word-level times come from `timing_url`.
 *
 * The parsers accept the model's names plus a few plain variants, and fill missing file
 * URLs from the project file endpoint, so the panel keeps working if a field is left out.
 */
import type { GateResult } from "./project";
import { parseGateResults } from "./script";

export const TIMING_SOURCES = ["provider_word", "provider_sentence", "estimated", "aligned"] as const;
export type TimingSource = (typeof TIMING_SOURCES)[number];

export const TIMING_SOURCE_LABELS: Record<TimingSource, string> = {
  provider_word: "Word timings from the voice tool",
  provider_sentence: "Sentence timings from the voice tool",
  estimated: "Timings estimated from the text",
  aligned: "Timings aligned to the recording",
};

export const TIMING_SOURCE_HELP: Record<TimingSource, string> = {
  provider_word: "The voice tool reported when each word is spoken, so captions and popups land exactly.",
  provider_sentence: "Each sentence was made on its own, so sentence starts are exact; word times inside a sentence are spread by length.",
  estimated: "The narration is an own recording without a speech aligner: sentences and words are spread over it by text length.",
  aligned: "A speech aligner matched the words of the script to the recording.",
};

export const WORD_SOURCES = ["provider", "estimated", "aligner"] as const;
export type WordSource = (typeof WORD_SOURCES)[number];

export const WORD_SOURCE_LABELS: Record<WordSource, string> = {
  provider: "words from the voice tool",
  estimated: "words estimated",
  aligner: "words from the aligner",
};

export const TIMING_CONFIDENCES = ["high", "medium", "low"] as const;
export type TimingConfidence = (typeof TIMING_CONFIDENCES)[number];

export const TIMING_CONFIDENCE_LABELS: Record<TimingConfidence, string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};

/** Words shorter than this (seconds) are flagged by the voice gate. */
export const MIN_WORD_S = 0.04;
/** Words longer than this (seconds) are flagged by the voice gate. */
export const MAX_WORD_S = 2;
/** The narration may differ from the target length by this share before the gate warns. */
export const DURATION_TOLERANCE = 0.25;

export const DEFAULT_SAMPLE_RATE = 48000;

export interface TimingWord {
  text: string;
  start_s: number;
  end_s: number;
  /** 0-1; estimated words carry a low value */
  confidence: number;
}

export interface TimingSentence {
  /** sentence id from script.json / speech.json */
  id: string;
  text: string;
  start_s: number;
  end_s: number;
  words: TimingWord[];
  words_source: WordSource;
  /** sample offsets in voice.wav, when known */
  start_frame: number | null;
  end_frame: number | null;
  /** `05_voice/sentences/<id>.wav` */
  audio_path: string;
}

/** `05_voice/timing.json` */
export interface TimingDoc {
  sample_rate: number;
  duration_s: number;
  source: TimingSource;
  sentences: TimingSentence[];
  timing_confidence: TimingConfidence;
  provider: string;
  model: string;
  voice_id: string;
  language: string;
  /** aligner used for an own recording; "none" otherwise */
  aligner: string;
  own_recording: boolean;
  generated_at: string | null;
  warnings: string[];
  schema_version: number;
}

/** One row of the voice review (`VoiceReviewSentence` in the backend) plus its word times. */
export interface VoiceReviewSentence {
  id: string;
  text: string;
  /** the spoken form (numbers and abbreviations written out) */
  speech_text: string;
  start_s: number;
  end_s: number;
  duration_s: number;
  /** `GET /api/projects/{id}/files/05_voice/sentences/{sid}.wav` */
  play_url: string;
  /** how many words the sentence has */
  word_count: number;
  words_source: WordSource;
  /** true when the sentence came from the voice cache (no new cost) */
  cached: boolean;
  cost_usd: number;
  /** plain-English flags from the timing check, e.g. "2 words under 40 ms" */
  flags: string[];
  /** word times from timing.json; empty until that file is loaded */
  words: TimingWord[];
}

/** What `GET /api/projects/{id}/stage/voice` returns, normalised. */
export interface VoiceReviewPayload {
  provider: string;
  model: string;
  voice_id: string;
  language: string;
  duration_s: number;
  /** the channel's target length for this format, when the stage reports it */
  target_s: number | null;
  sample_rate: number;
  source: TimingSource;
  timing_confidence: TimingConfidence;
  /** true when the audio is a person's own recording rather than the voice tool's */
  own_recording: boolean;
  aligner: string;
  /** URL of `05_voice/voice.wav` */
  voice_url: string;
  /** URL of `05_voice/timing.json`, for the word times */
  timing_url: string;
  sentences: VoiceReviewSentence[];
  warnings: string[];
  gate_results: GateResult[];
  cost_usd: number;
  cached_sentences: number;
  synthesized_sentences: number;
  consent: Record<string, unknown> | null;
}

/** `edits` on `POST .../stage/voice/approve` (and, kept for the next run, on redo). */
export type VoiceApproveEdits = {
  /** sentence ids to synthesise again (the cache is bypassed); the rest keep their audio */
  re_record?: string[];
  /** an own recording: a path inside the project folder (from the upload endpoint) */
  audio_path?: string;
  /** hand-nudged timing.json */
  timing?: TimingDoc;
  /** approve even though a blocking check fails (the server refuses otherwise) */
  override_gates?: boolean;
};

// ---------------------------------------------------------------------------
// Defensive parsing
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function oneOf<T extends string>(value: unknown, allowed: ReadonlyArray<T>, fallback: T): T {
  return typeof value === "string" && (allowed as ReadonlyArray<string>).includes(value) ? (value as T) : fallback;
}

/** Looks up the first key that carries a value. */
function pick(raw: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    if (raw[key] !== undefined && raw[key] !== null) return raw[key];
  }
  return undefined;
}

export function parseTimingWord(raw: unknown): TimingWord | null {
  if (!isRecord(raw)) return null;
  const start = num(pick(raw, ["start_s", "start"]), NaN);
  const end = num(pick(raw, ["end_s", "end"]), NaN);
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return {
    text: str(pick(raw, ["text", "word"])),
    start_s: start,
    end_s: Math.max(start, end),
    confidence: Math.max(0, Math.min(1, num(raw.confidence, 1))),
  };
}

export function sentenceAudioPath(sentenceId: string): string {
  return `05_voice/sentences/${sentenceId}.wav`;
}

export function parseTimingSentence(raw: unknown, position: number): TimingSentence | null {
  if (!isRecord(raw)) return null;
  const start = num(pick(raw, ["start_s", "start"]), 0);
  const end = Math.max(start, num(pick(raw, ["end_s", "end"]), start));
  const words = Array.isArray(raw.words) ? raw.words.map(parseTimingWord).filter((word): word is TimingWord => word !== null) : [];
  const id = str(pick(raw, ["id", "sentence_id"]), "") || `s${position + 1}`;
  const startFrame = raw.start_frame;
  const endFrame = raw.end_frame;
  return {
    id,
    text: str(pick(raw, ["text", "speech_text", "narration"])),
    start_s: start,
    end_s: end,
    words,
    words_source: oneOf(raw.words_source, WORD_SOURCES, "estimated"),
    start_frame: typeof startFrame === "number" && Number.isFinite(startFrame) ? startFrame : null,
    end_frame: typeof endFrame === "number" && Number.isFinite(endFrame) ? endFrame : null,
    audio_path: str(raw.audio_path) || sentenceAudioPath(id),
  };
}

/** The confidence the stage implies when it does not say: exact words are high, estimates medium. */
export function defaultConfidence(source: TimingSource): TimingConfidence {
  return source === "provider_word" || source === "aligned" ? "high" : source === "estimated" ? "low" : "medium";
}

export function parseTimingDoc(raw: unknown): TimingDoc | null {
  if (!isRecord(raw) || !Array.isArray(raw.sentences)) return null;
  const sentences = raw.sentences.map(parseTimingSentence).filter((sentence): sentence is TimingSentence => sentence !== null);
  const last = sentences.at(-1);
  const source = oneOf(pick(raw, ["source", "timing_source"]), TIMING_SOURCES, "estimated");
  return {
    sample_rate: num(pick(raw, ["sample_rate", "sample_rate_hz"]), DEFAULT_SAMPLE_RATE),
    duration_s: num(pick(raw, ["duration_s", "duration"]), last?.end_s ?? 0),
    source,
    sentences,
    timing_confidence: oneOf(pick(raw, ["timing_confidence", "confidence"]), TIMING_CONFIDENCES, defaultConfidence(source)),
    provider: str(raw.provider),
    model: str(raw.model),
    voice_id: str(pick(raw, ["voice_id", "voice"])),
    language: str(raw.language),
    aligner: str(raw.aligner, "none"),
    own_recording: bool(raw.own_recording),
    generated_at: typeof raw.generated_at === "string" ? raw.generated_at : null,
    warnings: strList(raw.warnings),
    schema_version: num(raw.schema_version, 1),
  };
}

/** Builds a URL for a file inside the project folder; `api/files.ts` supplies the real one. */
export type FileUrlBuilder = (relativePath: string) => string;

export const VOICE_AUDIO_PATH = "05_voice/voice.wav";
export const VOICE_TIMING_PATH = "05_voice/timing.json";

function isAbsoluteUrl(value: string): boolean {
  return /^(https?:)?\/\//i.test(value) || value.startsWith("/") || value.startsWith("blob:") || value.startsWith("data:");
}

/** Keeps a ready URL, turns a project-relative path into one through `fileUrl`, else `null`. */
function toUrl(value: unknown, fileUrl: FileUrlBuilder): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  return isAbsoluteUrl(value) ? value : fileUrl(value);
}

export function parseVoiceReviewSentence(raw: unknown, position: number, fileUrl: FileUrlBuilder): VoiceReviewSentence | null {
  const timed = parseTimingSentence(raw, position);
  if (!timed) return null;
  const record = isRecord(raw) ? raw : {};
  // In the review payload `words` is a count; in timing.json it is the list of timed words.
  const wordCount = typeof record.words === "number" ? Math.max(0, Math.trunc(record.words)) : timed.words.length;
  return {
    id: timed.id,
    text: timed.text,
    speech_text: str(record.speech_text),
    start_s: timed.start_s,
    end_s: timed.end_s,
    duration_s: num(record.duration_s, Math.max(0, timed.end_s - timed.start_s)),
    play_url: toUrl(pick(record, ["play_url", "url", "audio_url"]), fileUrl) ?? fileUrl(timed.audio_path),
    word_count: wordCount,
    words_source: timed.words_source,
    cached: bool(record.cached),
    cost_usd: num(record.cost_usd),
    flags: strList(record.flags),
    words: timed.words,
  };
}

/**
 * Turn whatever `GET /api/projects/{id}/stage/voice` returns into a review payload.
 * Returns null when it holds neither sentences nor a narration file.
 */
export function parseVoicePayload(raw: unknown, fileUrl: FileUrlBuilder): VoiceReviewPayload | null {
  if (!isRecord(raw)) return null;
  const timing = parseTimingDoc(raw.timing);
  const rawSentences = Array.isArray(raw.sentences) ? raw.sentences : (timing?.sentences ?? []);
  const sentences = rawSentences
    .map((item, position) => parseVoiceReviewSentence(item, position, fileUrl))
    .filter((sentence): sentence is VoiceReviewSentence => sentence !== null);
  const duration = num(pick(raw, ["duration_s", "duration"]), timing?.duration_s ?? (sentences.at(-1)?.end_s ?? 0));
  if (sentences.length === 0 && duration <= 0) return null;

  const source = oneOf(pick(raw, ["source", "timing_source"]), TIMING_SOURCES, timing?.source ?? "provider_sentence");
  const target = pick(raw, ["target_s", "target_duration_s", "target_length_s"]);
  return {
    provider: str(raw.provider, timing?.provider ?? ""),
    model: str(raw.model, timing?.model ?? ""),
    voice_id: str(pick(raw, ["voice_id", "voice"]), timing?.voice_id ?? ""),
    language: str(raw.language, timing?.language ?? ""),
    duration_s: duration,
    target_s: typeof target === "number" && Number.isFinite(target) && target > 0 ? target : null,
    sample_rate: num(pick(raw, ["sample_rate", "sample_rate_hz"]), timing?.sample_rate ?? DEFAULT_SAMPLE_RATE),
    source,
    timing_confidence: oneOf(pick(raw, ["timing_confidence", "confidence"]), TIMING_CONFIDENCES, timing?.timing_confidence ?? defaultConfidence(source)),
    own_recording: bool(raw.own_recording, timing?.own_recording ?? false) || str(raw.provider) === "own_recording",
    aligner: str(raw.aligner, timing?.aligner ?? "none"),
    voice_url: toUrl(pick(raw, ["voice_url", "audio_url", "play_url", "url"]), fileUrl) ?? fileUrl(VOICE_AUDIO_PATH),
    timing_url: toUrl(raw.timing_url, fileUrl) ?? fileUrl(VOICE_TIMING_PATH),
    sentences,
    warnings: strList(raw.warnings),
    gate_results: parseGateResults(pick(raw, ["gate_results", "gates"])),
    cost_usd: num(raw.cost_usd, sentences.reduce((sum, sentence) => sum + sentence.cost_usd, 0)),
    cached_sentences: num(raw.cached_sentences, sentences.filter((sentence) => sentence.cached).length),
    synthesized_sentences: num(raw.synthesized_sentences, sentences.filter((sentence) => !sentence.cached).length),
    consent: isRecord(raw.consent) ? raw.consent : null,
  };
}

/** Fills the word times of the review sentences from the timing document, by sentence id. */
export function withWordTimings(sentences: VoiceReviewSentence[], timing: TimingDoc | null): VoiceReviewSentence[] {
  if (!timing) return sentences;
  const byId = new Map(timing.sentences.map((sentence) => [sentence.id, sentence]));
  return sentences.map((sentence) => {
    const timed = byId.get(sentence.id);
    if (!timed || timed.words.length === 0) return sentence;
    return { ...sentence, words: timed.words, words_source: timed.words_source };
  });
}

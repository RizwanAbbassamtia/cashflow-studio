/**
 * Script stage shapes. Mirrors backend/cashcow_studio/models/script.py (Pydantic v2, the
 * source of truth); see docs/M1-M2-CONTRACT.md section 5.
 *
 * `script.json`, `speech.json` and `originality.json` live in `03_script/`; the review payload
 * of `GET /api/projects/{id}/stage/script` carries them. Ids are positional and stable within
 * one document: `sec-01`, `p-01-02` (section 1, paragraph 2), `s-01-02-03` (sentence 3).
 */
import type { GateResult, ProjectFormat } from "./project";

export interface ScriptSentence {
  id: string;
  text: string;
}

export interface ScriptParagraph {
  id: string;
  text: string;
  sentences: ScriptSentence[];
  /** kept word for word on a redo */
  locked: boolean;
}

export interface ScriptSection {
  id: string;
  name: string;
  purpose: string;
  paragraphs: ScriptParagraph[];
}

/** `03_script/script.json` */
export interface ScriptDoc {
  title: string;
  language: string;
  format: ProjectFormat;
  target_words: number;
  sections: ScriptSection[];
  word_count: number;
  model: string;
  generated_at: string;
  speaking_rate_wpm: number;
  framework_source: "file" | "default";
  framework_name: string | null;
  notes: string[];
  schema_version: number;
}

/** One sentence of `speech.json`: numbers, dates and abbreviations written out for the voice. */
export interface SpeechSentence {
  id: string;
  text: string;
  speech_text: string;
}

export interface SpeechDoc {
  language: string;
  sentences: SpeechSentence[];
  model: string;
  generated_at: string;
}

export interface PolicyFindings {
  /** the narrator poses as a professional adviser or tells the viewer what to do; blocks */
  advisory_persona: boolean;
  sensitive_topic: boolean;
  reasons: string[];
}

/** `03_script/originality.json`: the gate results of the script stage. */
export interface OriginalityDoc {
  /** 8-gram overlap with the competitor transcript, 0-1 */
  ngram_overlap_source: number;
  /** highest 8-gram overlap with one of the channel's last 30 scripts, 0-1 */
  ngram_overlap_history_max: number;
  semantic_note: string;
  policy: PolicyFindings;
  passed: boolean;
  word_count: number;
  target_words: number;
  /** the title's promise appears in the first 20% (reported, not blocking); null when unknown */
  title_claim_early: boolean | null;
  /** how many earlier scripts of the channel were compared */
  history_compared: number;
  /** false when no competitor transcript was available */
  source_compared: boolean;
  gate_results: GateResult[];
  blocking_reasons: string[];
}

export interface TranscriptBeat {
  name: string;
  purpose: string;
  /** rough share of the running time, 0-100 */
  share_percent: number;
}

/** Structure of the competitor video: beats, pacing and devices. Never sentences to reuse. */
export interface TranscriptSummary {
  beats: TranscriptBeat[];
  hook_style: string;
  pacing: string;
  devices: string[];
  ending_style: string;
  notes: string;
}

/** What `GET /api/projects/{id}/stage/script` returns (`ScriptReviewPayload`). */
export interface ScriptReviewPayload {
  script: ScriptDoc;
  /** the readable `script.md` */
  script_md: string | null;
  speech: SpeechDoc | null;
  originality: OriginalityDoc | null;
  transcript_summary: TranscriptSummary | null;
  /** the stage's gate results (from the payload, else from originality.json) */
  gate_results: GateResult[];
}

/** `edits` on `POST .../stage/script/approve` */
export interface ScriptApproveEdits {
  script_md?: string;
  locked_paragraph_ids?: string[];
  /** approve even though a blocking check fails (the server refuses otherwise) */
  override_gates?: boolean;
}

/** The thresholds from the contract, used to explain the originality numbers. */
export const SCRIPT_GATES = {
  /** 8-gram overlap with the competitor transcript must stay at or under this */
  overlap_source_max: 0.02,
  /** 8-gram overlap with any of the channel's last 30 scripts must stay at or under this */
  overlap_history_max: 0.05,
  /** the word count may differ from the target by this share either way */
  word_count_tolerance: 0.15,
} as const;

// ---------------------------------------------------------------------------
// Reading the payload defensively: the backend may send the script document on its own,
// or wrapped with the speech and originality files. Missing pieces become null, never a crash.
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

function parseSentence(raw: unknown, fallbackId: string): ScriptSentence {
  if (!isRecord(raw)) return { id: fallbackId, text: str(raw) };
  return { id: str(raw.id, fallbackId), text: str(raw.text) };
}

function parseParagraph(raw: unknown, fallbackId: string): ScriptParagraph {
  if (!isRecord(raw)) return { id: fallbackId, text: str(raw), sentences: [], locked: false };
  const id = str(raw.id, fallbackId);
  const sentences = Array.isArray(raw.sentences)
    ? raw.sentences.map((sentence, index) => parseSentence(sentence, `${id}-s${index + 1}`))
    : [];
  const text = str(raw.text) || sentences.map((sentence) => sentence.text).join(" ");
  return { id, text, sentences, locked: bool(raw.locked) };
}

function parseSection(raw: unknown, fallbackId: string): ScriptSection {
  if (!isRecord(raw)) return { id: fallbackId, name: "", purpose: "", paragraphs: [] };
  const id = str(raw.id, fallbackId);
  const paragraphs = Array.isArray(raw.paragraphs)
    ? raw.paragraphs.map((paragraph, index) => parseParagraph(paragraph, `${id}-p${index + 1}`))
    : [];
  return { id, name: str(raw.name), purpose: str(raw.purpose), paragraphs };
}

export function parseScriptDoc(raw: unknown): ScriptDoc | null {
  if (!isRecord(raw) || !Array.isArray(raw.sections)) return null;
  const sections = raw.sections.map((section, index) => parseSection(section, `sec-${String(index + 1).padStart(2, "0")}`));
  return {
    title: str(raw.title),
    language: str(raw.language),
    format: raw.format === "shorts" ? "shorts" : "long",
    target_words: num(raw.target_words),
    sections,
    word_count: num(raw.word_count),
    model: str(raw.model),
    generated_at: str(raw.generated_at),
    speaking_rate_wpm: num(raw.speaking_rate_wpm, 150),
    framework_source: raw.framework_source === "file" ? "file" : "default",
    framework_name: typeof raw.framework_name === "string" ? raw.framework_name : null,
    notes: strList(raw.notes),
    schema_version: num(raw.schema_version, 1),
  };
}

/** Accepts the SpeechDoc, a bare sentence list, or a `{sentence_id: speech_text}` map. */
export function parseSpeechDoc(raw: unknown): SpeechDoc | null {
  const list: unknown = isRecord(raw) && Array.isArray(raw.sentences) ? raw.sentences : raw;
  const base = { language: isRecord(raw) ? str(raw.language) : "", model: isRecord(raw) ? str(raw.model) : "", generated_at: isRecord(raw) ? str(raw.generated_at) : "" };
  if (Array.isArray(list)) {
    const sentences = list
      .filter(isRecord)
      .map((item) => ({ id: str(item.id), text: str(item.text), speech_text: str(item.speech_text, str(item.text)) }))
      .filter((item) => item.id);
    return { ...base, sentences };
  }
  if (isRecord(raw)) {
    const sentences: SpeechSentence[] = [];
    for (const [id, value] of Object.entries(raw)) {
      if (typeof value === "string") sentences.push({ id, text: "", speech_text: value });
    }
    return sentences.length > 0 ? { ...base, sentences } : null;
  }
  return null;
}

export function parseGateResults(raw: unknown): GateResult[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(isRecord).map((item) => ({
    id: str(item.id),
    title: str(item.title, str(item.id)),
    severity: item.severity === "block" ? "block" : "warn",
    passed: bool(item.passed, true),
    detail: str(item.detail),
  }));
}

export function parseOriginalityDoc(raw: unknown): OriginalityDoc | null {
  if (!isRecord(raw)) return null;
  const policy = isRecord(raw.policy) ? raw.policy : {};
  return {
    ngram_overlap_source: num(raw.ngram_overlap_source),
    ngram_overlap_history_max: num(raw.ngram_overlap_history_max),
    semantic_note: str(raw.semantic_note),
    policy: {
      advisory_persona: bool(policy.advisory_persona),
      sensitive_topic: bool(policy.sensitive_topic),
      reasons: strList(policy.reasons),
    },
    passed: bool(raw.passed, true),
    word_count: num(raw.word_count),
    target_words: num(raw.target_words),
    title_claim_early: typeof raw.title_claim_early === "boolean" ? raw.title_claim_early : null,
    history_compared: num(raw.history_compared),
    source_compared: bool(raw.source_compared, true),
    gate_results: parseGateResults(raw.gate_results),
    blocking_reasons: strList(raw.blocking_reasons),
  };
}

export function parseTranscriptSummary(raw: unknown): TranscriptSummary | null {
  if (!isRecord(raw) || !Array.isArray(raw.beats)) return null;
  return {
    beats: raw.beats.filter(isRecord).map((beat) => ({ name: str(beat.name), purpose: str(beat.purpose), share_percent: num(beat.share_percent) })),
    hook_style: str(raw.hook_style),
    pacing: str(raw.pacing),
    devices: strList(raw.devices),
    ending_style: str(raw.ending_style),
    notes: str(raw.notes),
  };
}

/**
 * Turn whatever `GET /api/projects/{id}/stage/script` returns into a review payload.
 * Returns null when no script document can be found in it.
 */
export function parseScriptPayload(raw: unknown): ScriptReviewPayload | null {
  if (!isRecord(raw)) return null;
  const script = parseScriptDoc(raw.script) ?? parseScriptDoc(raw);
  if (!script) return null;
  const originality = parseOriginalityDoc(raw.originality);
  const topLevelGates = parseGateResults(raw.gate_results);
  return {
    script,
    script_md: typeof raw.script_md === "string" ? raw.script_md : null,
    speech: parseSpeechDoc(raw.speech),
    originality,
    transcript_summary: parseTranscriptSummary(raw.transcript_summary),
    gate_results: topLevelGates.length > 0 ? topLevelGates : (originality?.gate_results ?? []),
  };
}

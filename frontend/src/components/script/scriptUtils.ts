/** Pure helpers for the script editor: word counts, markdown, locks, speech lookups. */
import { SCRIPT_GATES, type ScriptDoc, type ScriptParagraph, type SpeechDoc } from "../../types";

export function countWords(text: string): number {
  const trimmed = text.trim();
  if (!trimmed) return 0;
  return trimmed.split(/\s+/).length;
}

export function scriptWordCount(doc: ScriptDoc): number {
  let total = 0;
  for (const section of doc.sections) for (const paragraph of section.paragraphs) total += countWords(paragraph.text);
  return total;
}

export interface WordCountStatus {
  count: number;
  target: number;
  min: number;
  max: number;
  /** -1 under the band, 0 inside, 1 over */
  position: -1 | 0 | 1;
  /** difference from the target in words, negative when short */
  delta: number;
}

export function wordCountStatus(count: number, target: number): WordCountStatus {
  const tolerance = SCRIPT_GATES.word_count_tolerance;
  const min = Math.round(target * (1 - tolerance));
  const max = Math.round(target * (1 + tolerance));
  const position: -1 | 0 | 1 = target <= 0 ? 0 : count < min ? -1 : count > max ? 1 : 0;
  return { count, target, min, max, position, delta: count - target };
}

/**
 * The readable form the backend writes as script.md: `## Section` headings, the section's
 * `_Purpose: ..._` line (the server reads it back, so purposes survive an edited approve),
 * one paragraph per block.
 */
export function scriptToMarkdown(doc: ScriptDoc): string {
  const blocks: string[] = [];
  for (const section of doc.sections) {
    blocks.push(`## ${section.name.trim() || "Section"}`);
    const purpose = section.purpose.trim();
    if (purpose) blocks.push(`_Purpose: ${purpose}_`);
    for (const paragraph of section.paragraphs) {
      const text = paragraph.text.trim();
      if (text) blocks.push(text);
    }
  }
  return `${blocks.join("\n\n")}\n`;
}

export function lockedParagraphIds(doc: ScriptDoc): string[] {
  const ids: string[] = [];
  for (const section of doc.sections) for (const paragraph of section.paragraphs) if (paragraph.locked) ids.push(paragraph.id);
  return ids;
}

export function allParagraphs(doc: ScriptDoc): ScriptParagraph[] {
  return doc.sections.flatMap((section) => section.paragraphs);
}

/** Same paragraph texts, in the same order, as the saved script? Locks are compared separately. */
export function sameText(a: ScriptDoc, b: ScriptDoc): boolean {
  const pa = allParagraphs(a);
  const pb = allParagraphs(b);
  if (pa.length !== pb.length) return false;
  for (let i = 0; i < pa.length; i += 1) if (pa[i]!.text !== pb[i]!.text) return false;
  return true;
}

export function sameLocks(a: ScriptDoc, b: ScriptDoc): boolean {
  const la = lockedParagraphIds(a);
  const lb = lockedParagraphIds(b);
  return la.length === lb.length && la.every((id, index) => id === lb[index]);
}

/** `{sentence_id: speech_text}` for quick lookups while rendering paragraphs. */
export function speechIndex(speech: SpeechDoc | null): Map<string, string> {
  const map = new Map<string, string>();
  if (!speech) return map;
  for (const sentence of speech.sentences) map.set(sentence.id, sentence.speech_text);
  return map;
}

/** The spoken form of one paragraph, when speech.json covers every sentence of it. */
export function paragraphSpeech(paragraph: ScriptParagraph, index: Map<string, string>): string | null {
  if (paragraph.sentences.length === 0 || index.size === 0) return null;
  const parts: string[] = [];
  for (const sentence of paragraph.sentences) {
    const spoken = index.get(sentence.id);
    if (spoken === undefined) return null;
    parts.push(spoken);
  }
  const joined = parts.join(" ").trim();
  return joined && joined !== paragraph.text.trim() ? joined : null;
}

export function updateParagraph(doc: ScriptDoc, paragraphId: string, patch: Partial<Pick<ScriptParagraph, "text" | "locked">>): ScriptDoc {
  return {
    ...doc,
    sections: doc.sections.map((section) => ({
      ...section,
      paragraphs: section.paragraphs.map((paragraph) => (paragraph.id === paragraphId ? { ...paragraph, ...patch } : paragraph)),
    })),
  };
}

export function setAllLocks(doc: ScriptDoc, locked: boolean): ScriptDoc {
  return {
    ...doc,
    sections: doc.sections.map((section) => ({
      ...section,
      paragraphs: section.paragraphs.map((paragraph) => ({ ...paragraph, locked })),
    })),
  };
}

/** "0.4%" for overlap shares; one decimal is enough for a reviewer. */
export function formatShare(value: number): string {
  return `${(value * 100).toFixed(value * 100 < 10 ? 1 : 0)}%`;
}

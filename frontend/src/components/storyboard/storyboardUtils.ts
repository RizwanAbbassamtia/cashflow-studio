/**
 * Pure helpers for the Storyboard Board: reflowing times after an edit, split / merge /
 * reorder, and the local variety check that mirrors the rules the backend enforces
 * (docs/M1-M2-CONTRACT.md section 6) so the reviewer sees warnings while editing.
 */
import {
  MIN_TEXT_SHARE,
  POPUP_MAX_WORDS,
  type SceneLockField,
  type StoryboardDoc,
  type StoryboardScene,
  type StoryboardVariety,
  type TransitionOption,
} from "../../types";

export const DEFAULT_WPM = 150;

/** [min, max] seconds a scene should run */
export type SceneBand = [number, number];

export function countWords(text: string): number {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

/** Seconds of narration at the speaking rate, rounded to a tenth. */
export function estimateDuration(narration: string, wpm: number): number {
  const words = countWords(narration);
  if (words === 0) return 0;
  return Math.round((words / Math.max(wpm, 1)) * 60 * 10) / 10;
}

export function formatSeconds(seconds: number): string {
  if (!Number.isFinite(seconds)) return "-";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds - minutes * 60);
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

/** Renumber scenes and lay their estimated times end to end. */
export function reflowScenes(scenes: StoryboardScene[]): StoryboardScene[] {
  let cursor = 0;
  return scenes.map((scene, index) => {
    const duration = Math.max(0, Math.round(scene.est_duration_s * 10) / 10);
    const next: StoryboardScene = {
      ...scene,
      index,
      est_start_s: Math.round(cursor * 10) / 10,
      est_end_s: Math.round((cursor + duration) * 10) / 10,
      est_duration_s: duration,
    };
    cursor += duration;
    return next;
  });
}

export function updateScene(doc: StoryboardDoc, index: number, patch: Partial<StoryboardScene>): StoryboardDoc {
  return { ...doc, scenes: doc.scenes.map((scene, i) => (i === index ? { ...scene, ...patch } : scene)) };
}

export function toggleLock(doc: StoryboardDoc, index: number, field: SceneLockField, locked: boolean): StoryboardDoc {
  const scene = doc.scenes[index];
  if (!scene) return doc;
  return updateScene(doc, index, { locked: { ...scene.locked, [field]: locked } });
}

export function moveScene(doc: StoryboardDoc, from: number, to: number): StoryboardDoc {
  if (from === to || from < 0 || to < 0 || from >= doc.scenes.length || to >= doc.scenes.length) return doc;
  const scenes = [...doc.scenes];
  const [moved] = scenes.splice(from, 1);
  scenes.splice(to, 0, moved!);
  return { ...doc, scenes: reflowScenes(scenes) };
}

/** Split narration into sentences for the split dialog. Keeps the punctuation with each sentence. */
export function sentenceChunks(text: string): string[] {
  const chunks = text
    .trim()
    .split(/(?<=[.!?…]["'”’)]?)\s+(?=\S)/u)
    .map((chunk) => chunk.trim())
    .filter(Boolean);
  return chunks.length > 0 ? chunks : text.trim() ? [text.trim()] : [];
}

/**
 * Split scene `index` after sentence chunk `afterChunk` (1-based count of chunks that stay
 * in the first half). Sentence ids are shared out by position; when the counts do not line
 * up, both halves keep a fair share and never end up with none while the other has several.
 */
export function splitScene(doc: StoryboardDoc, index: number, afterChunk: number, wpm: number): StoryboardDoc {
  const scene = doc.scenes[index];
  if (!scene) return doc;
  const chunks = sentenceChunks(scene.narration);
  if (chunks.length < 2 || afterChunk < 1 || afterChunk >= chunks.length) return doc;

  const firstText = chunks.slice(0, afterChunk).join(" ");
  const secondText = chunks.slice(afterChunk).join(" ");

  let firstIds: string[];
  let secondIds: string[];
  const ids = scene.sentence_ids;
  // A scene that covers one script sentence cannot be split here: both halves would carry
  // the same sentence id and the server would merge them back on approve.
  if (ids.length < 2) return doc;
  if (ids.length === chunks.length) {
    firstIds = ids.slice(0, afterChunk);
    secondIds = ids.slice(afterChunk);
  } else {
    const cut = Math.min(ids.length - 1, Math.max(1, Math.round((afterChunk / chunks.length) * ids.length)));
    firstIds = ids.slice(0, cut);
    secondIds = ids.slice(cut);
  }

  const totalWords = Math.max(1, countWords(scene.narration));
  const firstShare = countWords(firstText) / totalWords;
  const baseDuration = scene.est_duration_s > 0 ? scene.est_duration_s : estimateDuration(scene.narration, wpm);

  const first: StoryboardScene = {
    ...scene,
    narration: firstText,
    sentence_ids: firstIds,
    est_duration_s: Math.round(baseDuration * firstShare * 10) / 10,
    notes: [...scene.notes, `Split from scene ${scene.index + 1} by the reviewer`],
  };
  // The new half starts from the same prompt as a draft, unlocked, so the reviewer or a
  // scene regenerate can give it its own picture; the variety check flags the duplicate.
  const second: StoryboardScene = {
    ...scene,
    narration: secondText,
    sentence_ids: secondIds,
    est_duration_s: Math.round(baseDuration * (1 - firstShare) * 10) / 10,
    image: { path: null, status: "pending", qa: null },
    popup: { ...scene.popup, text: null },
    on_screen_text: null,
    locked: { ...scene.locked, image_prompt: false, popup: false },
    notes: [`Split from scene ${scene.index + 1} by the reviewer; the image prompt is a copy and needs its own picture`],
  };

  const scenes = [...doc.scenes];
  scenes.splice(index, 1, first, second);
  return { ...doc, scenes: reflowScenes(scenes) };
}

/** Merge scene `index` with the one after it. The first scene's picture and motion win. */
export function mergeWithNext(doc: StoryboardDoc, index: number): StoryboardDoc {
  const first = doc.scenes[index];
  const second = doc.scenes[index + 1];
  if (!first || !second) return doc;

  const merged: StoryboardScene = {
    ...first,
    narration: [first.narration.trim(), second.narration.trim()].filter(Boolean).join(" "),
    sentence_ids: [...first.sentence_ids, ...second.sentence_ids.filter((id) => !first.sentence_ids.includes(id))],
    est_duration_s: Math.round((first.est_duration_s + second.est_duration_s) * 10) / 10,
    image_prompt: first.image_prompt || second.image_prompt,
    negative_prompt: first.negative_prompt || second.negative_prompt,
    popup: first.popup.text ? first.popup : second.popup,
    on_screen_text: first.on_screen_text ?? second.on_screen_text,
    transition_out: second.transition_out,
    image: first.image.path ? first.image : second.image,
    locked: {
      image_prompt: first.locked.image_prompt || second.locked.image_prompt,
      popup: first.locked.popup || second.locked.popup,
      motion: first.locked.motion || second.locked.motion,
      transition_out: first.locked.transition_out || second.locked.transition_out,
      narration: first.locked.narration || second.locked.narration,
    },
    notes: [...first.notes, ...second.notes, `Merged with scene ${second.index + 1} by the reviewer`],
  };

  const scenes = [...doc.scenes];
  scenes.splice(index, 2, merged);
  return { ...doc, scenes: reflowScenes(scenes) };
}

export function deleteScene(doc: StoryboardDoc, index: number): StoryboardDoc {
  if (doc.scenes.length <= 1) return doc;
  return { ...doc, scenes: reflowScenes(doc.scenes.filter((_, i) => i !== index)) };
}

export function hasText(scene: StoryboardScene): boolean {
  return Boolean(scene.popup.text?.trim() || scene.on_screen_text?.trim());
}

function normalise(text: string): string {
  return text.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, "").replace(/\s+/g, " ").trim();
}

const AVOID_MARKER = "Avoid:";

/**
 * The bare picture description: the server writes every prompt as
 * `<style guide>. <body>. Avoid: <negative rules>.`, so checks must look at the body only
 * (mirrors strip_prompt_wrapping in the backend's storyboard stage).
 */
export function stripPromptWrapping(prompt: string, styleGuide: string): string {
  let body = prompt.split(/\s+/).join(" ").trim();
  const guide = styleGuide.split(/\s+/).join(" ").trim().replace(/\.+$/, "");
  if (guide && body.toLowerCase().startsWith(guide.toLowerCase())) {
    body = body.slice(guide.length).replace(/^[\s.,;:]+/, "");
  }
  const marker = body.indexOf(AVOID_MARKER);
  if (marker >= 0) body = body.slice(0, marker).trimEnd();
  return body.trim().replace(/\.+$/, "").trim();
}

/**
 * Same patterns as policy/rules.yaml `storyboard.prompt_text_free`: a prompt fails when it
 * asks for words, signs or logos in the picture; "no text", "text-free" or "without logos"
 * do not match.
 */
const PROMPT_TEXT_PATTERNS: RegExp[] = [
  /\b(with|showing|featuring|including|displaying|holding|has|have)\b[^.;]{0,40}\b(text|words|letters|lettering|caption|captions|subtitle|subtitles|logo|logos|headline|slogan)\b(?!-free)/i,
  /\b(that|which) (says|reads|spells)\b/i,
  /\b(sign|banner|poster|screen|board|label) (that )?(reading|saying|with) /i,
  /\bbrand(ed)? logo\b/i,
];

/** True when the prompt's own description (not the style guide or the negative rules) asks for text or a logo. */
export function promptAsksForText(prompt: string, styleGuide: string): boolean {
  const body = stripPromptWrapping(prompt, styleGuide);
  return PROMPT_TEXT_PATTERNS.some((pattern) => pattern.test(body));
}

/** Scene-level problems, in plain English, keyed by scene position. */
export interface SceneIssue {
  index: number;
  message: string;
}

/**
 * The same rules the backend fixes up after the LLM call, run locally on the draft so the
 * board can warn while the reviewer edits.
 */
export function checkVariety(
  scenes: StoryboardScene[],
  band: SceneBand,
  transitions: TransitionOption[],
  styleGuide = "",
): { variety: StoryboardVariety; issues: SceneIssue[] } {
  const [minS, maxS] = band;
  const issues: SceneIssue[] = [];
  const warnings: string[] = [];
  const allowed = new Set(transitions.map((option) => option.type));

  for (let i = 0; i < scenes.length; i += 1) {
    const scene = scenes[i]!;
    const previous = i > 0 ? scenes[i - 1]! : null;
    const n = i + 1;

    if (!scene.narration.trim()) issues.push({ index: i, message: "Has no narration." });
    if (scene.est_duration_s > 0 && scene.est_duration_s < minS) {
      issues.push({ index: i, message: `Only ${formatSeconds(scene.est_duration_s)} long; aim for ${minS}-${maxS} s. Merge it with a neighbour.` });
    } else if (scene.est_duration_s > maxS) {
      issues.push({ index: i, message: `${formatSeconds(scene.est_duration_s)} long; aim for ${minS}-${maxS} s. Split it.` });
    }
    if (previous && previous.motion.preset === scene.motion.preset) {
      issues.push({ index: i, message: `Same motion as scene ${n - 1}. Two neighbours with the same move look like a slideshow.` });
    }
    if (previous && previous.transition_out.type === scene.transition_out.type) {
      issues.push({ index: i, message: `Same transition as scene ${n - 1}. Vary it.` });
    }
    if (allowed.size > 0 && !allowed.has(scene.transition_out.type)) {
      issues.push({ index: i, message: `"${scene.transition_out.type}" is not in the transitions list; the app will swap it for the default.` });
    }
    const popup = scene.popup.text?.trim() ?? "";
    if (popup) {
      if (countWords(popup) > POPUP_MAX_WORDS) issues.push({ index: i, message: `Popup has ${countWords(popup)} words; keep it to ${POPUP_MAX_WORDS} or fewer.` });
      if (normalise(popup) === normalise(scene.narration)) issues.push({ index: i, message: "Popup repeats the narration word for word; make it a short highlight instead." });
    }
    const bareBody = stripPromptWrapping(scene.image_prompt, styleGuide);
    if (!bareBody) issues.push({ index: i, message: "Has no image prompt, so no picture will be made." });
    else if (previous && normalise(stripPromptWrapping(previous.image_prompt, styleGuide)) === normalise(bareBody)) {
      issues.push({ index: i, message: `Same image prompt as scene ${n - 1}, so both scenes would get the same picture. Change one of them.` });
    }
    if (promptAsksForText(scene.image_prompt, styleGuide)) {
      issues.push({ index: i, message: "The image prompt asks for text or a logo in the picture; prompts must be text-free (the app draws popups itself)." });
    }
  }

  const total = scenes.reduce((sum, scene) => sum + scene.est_duration_s, 0);
  const withText = scenes.filter(hasText).length;
  const popupShare = scenes.length > 0 ? withText / scenes.length : 0;
  const distinctTransitions = new Set(scenes.map((scene) => scene.transition_out.type)).size;
  const distinctMotions = new Set(scenes.map((scene) => scene.motion.preset)).size;

  if (scenes.length > 0 && popupShare < MIN_TEXT_SHARE) {
    warnings.push(`Only ${Math.round(popupShare * 100)}% of scenes have a popup or on-screen text; at least ${Math.round(MIN_TEXT_SHARE * 100)}% should. Static images with no text read as a slideshow.`);
  }
  if (scenes.length >= 4 && distinctTransitions < Math.min(3, scenes.length)) {
    warnings.push(`Only ${distinctTransitions} different transition${distinctTransitions === 1 ? "" : "s"} across ${scenes.length} scenes. Use at least three.`);
  }
  if (scenes.length >= 4 && distinctMotions < 3) {
    warnings.push(`Only ${distinctMotions} different motion preset${distinctMotions === 1 ? "" : "s"}; mix zooms, pans and holds.`);
  }
  const sameMotion = issues.filter((issue) => issue.message.startsWith("Same motion")).length;
  if (sameMotion > 0) warnings.push(`${sameMotion} pair${sameMotion === 1 ? "" : "s"} of neighbouring scenes share a motion preset.`);
  const sameTransition = issues.filter((issue) => issue.message.startsWith("Same transition")).length;
  if (sameTransition > 0) warnings.push(`${sameTransition} transition${sameTransition === 1 ? " is" : "s are"} used twice in a row.`);
  const lengthIssues = issues.filter((issue) => issue.message.includes("aim for")).length;
  if (lengthIssues > 0) warnings.push(`${lengthIssues} scene${lengthIssues === 1 ? " is" : "s are"} outside the ${minS}-${maxS} s band.`);

  return {
    variety: {
      scenes: scenes.length,
      avg_scene_s: scenes.length > 0 ? Math.round((total / scenes.length) * 10) / 10 : 0,
      popup_share: Math.round(popupShare * 100) / 100,
      distinct_transitions: distinctTransitions,
      distinct_motions: distinctMotions,
      warnings,
    },
    issues,
  };
}

/** Where the board loads a scene picture from; null for the placeholder. */
export function sceneImageUrl(projectId: string, scene: StoryboardScene): string | null {
  const { image } = scene;
  if (image.url) return image.url;
  if (!image.path) return null;
  if (/^(https?:)?\/\//i.test(image.path) || image.path.startsWith("data:") || image.path.startsWith("/")) return image.path;
  // A file inside the project folder: the backend serves it relative to the project (M3).
  return `/api/projects/${encodeURIComponent(projectId)}/file?path=${encodeURIComponent(image.path)}`;
}

export function transitionLabel(type: string, transitions: TransitionOption[]): string {
  return transitions.find((option) => option.type === type)?.label ?? type;
}

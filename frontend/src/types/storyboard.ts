/**
 * Storyboard stage shapes. Mirrors backend/cashflow_studio/models/storyboard.py (Pydantic v2,
 * the source of truth); see docs/M1-M2-CONTRACT.md section 6.
 *
 * `04_storyboard/storyboard.json` is the file on disk; the review payload of
 * `GET /api/projects/{id}/stage/storyboard` carries the whole document plus the pickers'
 * option lists. Rectangles are fractions of the frame `[x, y, w, h]` in 0-1.
 */
import type { GateResult, ProjectFormat } from "./project";
import { parseGateResults } from "./script";

export const MOTION_PRESETS = [
  "zoom_in",
  "zoom_out",
  "pan_left",
  "pan_right",
  "pan_up",
  "pan_down",
  "hold",
  "slow_push",
] as const;
export type MotionPreset = (typeof MOTION_PRESETS)[number];

export const MOTION_LABELS: Record<MotionPreset, string> = {
  zoom_in: "Zoom in",
  zoom_out: "Zoom out",
  pan_left: "Pan left",
  pan_right: "Pan right",
  pan_up: "Pan up",
  pan_down: "Pan down",
  hold: "Hold still",
  slow_push: "Slow push in",
};

export const POPUP_POSITIONS = ["top-left", "top-right", "bottom-left", "bottom-right", "center"] as const;
export type PopupPosition = (typeof POPUP_POSITIONS)[number];

export const POPUP_POSITION_LABELS: Record<PopupPosition, string> = {
  "top-left": "Top left",
  "top-right": "Top right",
  "bottom-left": "Bottom left",
  "bottom-right": "Bottom right",
  center: "Centre",
};

export const IMAGE_STATUSES = ["pending", "generated", "approved", "rejected"] as const;
export type ImageStatus = (typeof IMAGE_STATUSES)[number];

export const STORYBOARD_ASPECTS = ["16:9", "9:16"] as const;
export type StoryboardAspect = (typeof STORYBOARD_ASPECTS)[number];

/** [x, y, w, h] as shares of the frame, 0-1 */
export type Rect = [number, number, number, number];

export interface ScenePopup {
  /** at most 6 words; null = no popup */
  text: string | null;
  style: string;
  position: PopupPosition;
  in_offset_s: number;
  out_offset_s: number;
}

export interface SceneMotion {
  preset: MotionPreset;
  start_rect: Rect;
  end_rect: Rect;
}

export interface SceneTransition {
  /** an xfade name from config/transitions.yaml */
  type: string;
  duration_s: number;
}

export interface SceneImage {
  path: string | null;
  status: ImageStatus;
  qa: Record<string, unknown> | null;
  /** optional, when the backend serves the picture over HTTP (M3) */
  url?: string | null;
}

export interface SceneLocks {
  image_prompt: boolean;
  popup: boolean;
  motion: boolean;
  transition_out: boolean;
  narration: boolean;
}
export type SceneLockField = keyof SceneLocks;

export interface StoryboardScene {
  index: number;
  /** never empty: a scene covers at least one sentence */
  sentence_ids: string[];
  narration: string;
  est_start_s: number;
  est_end_s: number;
  est_duration_s: number;
  /** text-free; the stage prefixes the style guide */
  image_prompt: string;
  negative_prompt: string;
  popup: ScenePopup;
  motion: SceneMotion;
  transition_out: SceneTransition;
  on_screen_text: string | null;
  image: SceneImage;
  locked: SceneLocks;
  notes: string[];
}

export interface StoryboardVariety {
  scenes: number;
  avg_scene_s: number;
  /** share of scenes with a popup or on-screen text, 0-1 */
  popup_share: number;
  distinct_transitions: number;
  distinct_motions: number;
  warnings: string[];
}

/** `04_storyboard/storyboard.json` */
export interface StoryboardDoc {
  project_id: string;
  format: ProjectFormat;
  aspect: StoryboardAspect;
  style_guide: string;
  negative_rules: string;
  popup_style: string;
  generated_at: string;
  model: string;
  speaking_rate_wpm: number;
  scenes: StoryboardScene[];
  variety: StoryboardVariety;
  gate_results: GateResult[];
  notes: string[];
  schema_version: number;
}

/** One entry of config/transitions.yaml as the backend exposes it. */
export interface TransitionOption {
  type: string;
  label: string;
  duration_s: number;
}

/** What `GET /api/projects/{id}/stage/storyboard` returns (`StoryboardReviewPayload`). */
export interface StoryboardReviewPayload {
  storyboard: StoryboardDoc;
  /** the allowed transitions; FALLBACK_TRANSITIONS when the backend sends none */
  transitions: TransitionOption[];
  motion_presets: MotionPreset[];
  popup_positions: PopupPosition[];
  /** [min, max] seconds per scene for this format */
  scene_band_s: [number, number];
  /** popup style names to suggest (optional in the payload) */
  popup_styles: string[];
}

/** `edits` on `POST .../stage/storyboard/approve`: the whole edited document. */
export interface StoryboardApproveEdits {
  storyboard: StoryboardDoc;
  /** approve even though a blocking check fails (the server refuses otherwise) */
  override_gates?: boolean;
}

/** Scene length targets in seconds (contract section 6), used when the payload has no band. */
export const SCENE_LENGTH: Record<ProjectFormat, [number, number]> = {
  long: [8, 12],
  shorts: [3, 4],
};

export const POPUP_MAX_WORDS = 6;
/** at least this share of scenes should carry a popup or on-screen text */
export const MIN_TEXT_SHARE = 0.35;

/**
 * config/transitions.yaml as of M2, used only when the payload carries no list. The backend's
 * file is the real source; keep this in step with it.
 */
export const FALLBACK_TRANSITIONS: TransitionOption[] = [
  { type: "fade", label: "Cross fade", duration_s: 0.6 },
  { type: "fadeblack", label: "Fade through black", duration_s: 0.8 },
  { type: "fadewhite", label: "Fade through white", duration_s: 0.8 },
  { type: "dissolve", label: "Dissolve", duration_s: 0.7 },
  { type: "wipeleft", label: "Wipe left", duration_s: 0.5 },
  { type: "wiperight", label: "Wipe right", duration_s: 0.5 },
  { type: "wipeup", label: "Wipe up", duration_s: 0.5 },
  { type: "wipedown", label: "Wipe down", duration_s: 0.5 },
  { type: "slideleft", label: "Slide left", duration_s: 0.5 },
  { type: "slideright", label: "Slide right", duration_s: 0.5 },
  { type: "slideup", label: "Slide up", duration_s: 0.5 },
  { type: "slidedown", label: "Slide down", duration_s: 0.5 },
  { type: "smoothleft", label: "Smooth wipe left", duration_s: 0.6 },
  { type: "smoothright", label: "Smooth wipe right", duration_s: 0.6 },
  { type: "smoothup", label: "Smooth wipe up", duration_s: 0.6 },
  { type: "smoothdown", label: "Smooth wipe down", duration_s: 0.6 },
  { type: "circleopen", label: "Circle open", duration_s: 0.7 },
  { type: "circleclose", label: "Circle close", duration_s: 0.7 },
  { type: "radial", label: "Radial sweep", duration_s: 0.7 },
  { type: "zoomin", label: "Zoom in", duration_s: 0.6 },
  { type: "pixelize", label: "Pixelate", duration_s: 0.5 },
  { type: "hlslice", label: "Horizontal slices", duration_s: 0.5 },
  { type: "vuslice", label: "Vertical slices", duration_s: 0.5 },
  { type: "diagtl", label: "Diagonal (top left)", duration_s: 0.6 },
  { type: "diagbr", label: "Diagonal (bottom right)", duration_s: 0.6 },
  { type: "distance", label: "Distance blur", duration_s: 0.7 },
  { type: "fadegrays", label: "Fade through grey", duration_s: 0.8 },
];

export const DEFAULT_POPUP_STYLES = ["bold", "caption", "highlight", "sticker", "lower-third"];

// ---------------------------------------------------------------------------
// Defaults, identical to the Pydantic defaults.
// ---------------------------------------------------------------------------

export function fullRect(): Rect {
  return [0, 0, 1, 1];
}

export function defaultPopup(): ScenePopup {
  return { text: null, style: "", position: "bottom-left", in_offset_s: 0.4, out_offset_s: 3.5 };
}

export function defaultMotion(preset: MotionPreset = "zoom_in"): SceneMotion {
  return { preset, start_rect: fullRect(), end_rect: [0.1, 0.1, 0.8, 0.8] };
}

export function defaultLocks(): SceneLocks {
  return { image_prompt: false, popup: false, motion: false, transition_out: false, narration: false };
}

export function defaultSceneImage(): SceneImage {
  return { path: null, status: "pending", qa: null };
}

export function defaultScene(index: number): StoryboardScene {
  return {
    index,
    sentence_ids: [],
    narration: "",
    est_start_s: 0,
    est_end_s: 0,
    est_duration_s: 0,
    image_prompt: "",
    negative_prompt: "",
    popup: defaultPopup(),
    motion: defaultMotion(),
    transition_out: { type: "fade", duration_s: 0.6 },
    on_screen_text: null,
    image: defaultSceneImage(),
    locked: defaultLocks(),
    notes: [],
  };
}

export function emptyVariety(): StoryboardVariety {
  return { scenes: 0, avg_scene_s: 0, popup_share: 0, distinct_transitions: 0, distinct_motions: 0, warnings: [] };
}

// ---------------------------------------------------------------------------
// Defensive parsing of the review payload. Unknown fields the backend adds later are kept
// (spread first, then the known fields on top) so an approve sends the whole document back.
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function nullableStr(value: unknown): string | null {
  return typeof value === "string" ? value : null;
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

function listOf<T extends string>(value: unknown, allowed: ReadonlyArray<T>): T[] {
  return strList(value).filter((item): item is T => (allowed as ReadonlyArray<string>).includes(item));
}

function rect(value: unknown, fallback: Rect): Rect {
  if (!Array.isArray(value) || value.length !== 4) return fallback;
  const parts = value.map((item) => (typeof item === "number" && Number.isFinite(item) ? item : NaN));
  return parts.some(Number.isNaN) ? fallback : (parts as Rect);
}

function parsePopup(raw: unknown): ScenePopup {
  const base = defaultPopup();
  if (!isRecord(raw)) return base;
  return {
    ...raw,
    text: nullableStr(raw.text),
    style: str(raw.style, base.style),
    position: oneOf(raw.position, POPUP_POSITIONS, base.position),
    in_offset_s: num(raw.in_offset_s, base.in_offset_s),
    out_offset_s: num(raw.out_offset_s, base.out_offset_s),
  };
}

function parseMotion(raw: unknown): SceneMotion {
  const preset = oneOf(isRecord(raw) ? raw.preset : undefined, MOTION_PRESETS, "zoom_in");
  const base = defaultMotion(preset);
  if (!isRecord(raw)) return base;
  return { ...raw, preset, start_rect: rect(raw.start_rect, base.start_rect), end_rect: rect(raw.end_rect, base.end_rect) };
}

function parseTransition(raw: unknown): SceneTransition {
  if (!isRecord(raw)) return { type: "fade", duration_s: 0.6 };
  return { ...raw, type: str(raw.type, "fade"), duration_s: num(raw.duration_s, 0.6) };
}

function parseImage(raw: unknown): SceneImage {
  if (!isRecord(raw)) return defaultSceneImage();
  const image: SceneImage = {
    ...raw,
    path: nullableStr(raw.path),
    status: oneOf(raw.status, IMAGE_STATUSES, "pending"),
    qa: isRecord(raw.qa) ? raw.qa : null,
  };
  if (typeof raw.url === "string") image.url = raw.url;
  else delete image.url;
  return image;
}

function parseLocks(raw: unknown): SceneLocks {
  if (!isRecord(raw)) return defaultLocks();
  return {
    ...raw,
    image_prompt: bool(raw.image_prompt),
    popup: bool(raw.popup),
    motion: bool(raw.motion),
    transition_out: bool(raw.transition_out),
    narration: bool(raw.narration),
  };
}

export function parseScene(raw: unknown, position: number): StoryboardScene {
  const base = defaultScene(position);
  if (!isRecord(raw)) return base;
  const start = num(raw.est_start_s);
  const end = num(raw.est_end_s);
  return {
    ...raw,
    index: num(raw.index, position),
    sentence_ids: strList(raw.sentence_ids),
    narration: str(raw.narration),
    est_start_s: start,
    est_end_s: end,
    est_duration_s: num(raw.est_duration_s, Math.max(0, end - start)),
    image_prompt: str(raw.image_prompt),
    negative_prompt: str(raw.negative_prompt),
    popup: parsePopup(raw.popup),
    motion: parseMotion(raw.motion),
    transition_out: parseTransition(raw.transition_out),
    on_screen_text: nullableStr(raw.on_screen_text),
    image: parseImage(raw.image),
    locked: parseLocks(raw.locked),
    notes: strList(raw.notes),
  };
}

export function parseVariety(raw: unknown): StoryboardVariety {
  const base = emptyVariety();
  if (!isRecord(raw)) return base;
  return {
    ...raw,
    scenes: num(raw.scenes),
    avg_scene_s: num(raw.avg_scene_s),
    popup_share: num(raw.popup_share),
    distinct_transitions: num(raw.distinct_transitions),
    distinct_motions: num(raw.distinct_motions),
    warnings: strList(raw.warnings),
  };
}

export function parseStoryboardDoc(raw: unknown): StoryboardDoc | null {
  if (!isRecord(raw) || !Array.isArray(raw.scenes)) return null;
  const format: ProjectFormat = raw.format === "shorts" ? "shorts" : "long";
  return {
    ...raw,
    project_id: str(raw.project_id),
    format,
    aspect: oneOf(raw.aspect, STORYBOARD_ASPECTS, format === "shorts" ? "9:16" : "16:9"),
    style_guide: str(raw.style_guide),
    negative_rules: str(raw.negative_rules),
    popup_style: str(raw.popup_style),
    generated_at: str(raw.generated_at),
    model: str(raw.model),
    speaking_rate_wpm: num(raw.speaking_rate_wpm, 150),
    scenes: raw.scenes.map((scene, index) => parseScene(scene, index)),
    variety: parseVariety(raw.variety),
    gate_results: parseGateResults(raw.gate_results),
    notes: strList(raw.notes),
    schema_version: num(raw.schema_version, 1),
  };
}

/** Accepts `[{type,label,duration_s}]`, `["fade", ...]` or `{fade: {label, duration_s}}`. */
export function parseTransitions(raw: unknown): TransitionOption[] {
  const options: TransitionOption[] = [];
  if (Array.isArray(raw)) {
    for (const item of raw) {
      if (typeof item === "string") options.push({ type: item, label: item, duration_s: 0.6 });
      else if (isRecord(item) && typeof item.type === "string") {
        options.push({
          type: item.type,
          label: str(item.label, item.type),
          duration_s: num(item.duration_s, num(item.default_duration_s, 0.6)),
        });
      }
    }
  } else if (isRecord(raw)) {
    for (const [type, value] of Object.entries(raw)) {
      const entry = isRecord(value) ? value : {};
      options.push({ type, label: str(entry.label, type), duration_s: num(entry.duration_s, num(entry.default_duration_s, 0.6)) });
    }
  }
  return options;
}

/**
 * Turn whatever `GET /api/projects/{id}/stage/storyboard` returns into a review payload.
 * Returns null when no storyboard document can be found in it.
 */
export function parseStoryboardPayload(raw: unknown): StoryboardReviewPayload | null {
  if (!isRecord(raw)) return null;
  const storyboard = parseStoryboardDoc(raw.storyboard) ?? parseStoryboardDoc(raw);
  if (!storyboard) return null;
  const transitions = parseTransitions(raw.transitions);
  const presets = listOf(raw.motion_presets, MOTION_PRESETS);
  const positions = listOf(raw.popup_positions, POPUP_POSITIONS);
  const band = Array.isArray(raw.scene_band_s) && raw.scene_band_s.length === 2 ? raw.scene_band_s.map((v) => num(v)) : [];
  const fallbackBand = SCENE_LENGTH[storyboard.format];
  return {
    storyboard,
    transitions: transitions.length > 0 ? transitions : FALLBACK_TRANSITIONS,
    motion_presets: presets.length > 0 ? presets : [...MOTION_PRESETS],
    popup_positions: positions.length > 0 ? positions : [...POPUP_POSITIONS],
    scene_band_s: band.length === 2 && band[0]! > 0 && band[1]! > band[0]! ? [band[0]!, band[1]!] : fallbackBand,
    popup_styles: strList(raw.popup_styles),
  };
}

/**
 * Edit stage shapes. Mirrors backend/cashcow_studio/models/timeline.py (Pydantic v2, the
 * source of truth); see docs/M3-M4-CONTRACT.md section 3.
 *
 * `07_edit/timeline.json` is the file on disk; the review payload of
 * `GET /api/projects/{id}/stage/edit` carries the proxy video, a summary of the timeline,
 * the render status per preset, the music tracks the channel folder offers and warnings.
 * Rectangles in the timeline are pixels of the source image `[x, y, w, h]`.
 */
import type { GateResult, ProjectFormat } from "./project";
import { parseGateResults } from "./script";
import type { StoryboardAspect } from "./storyboard";

// ---- render presets -----------------------------------------------------------------

/** The preset ids of config/render.yaml. "2160p" is shown as 4K. */
export const RENDER_PRESETS = ["720p", "1080p", "2160p"] as const;
export type RenderPreset = (typeof RENDER_PRESETS)[number];

export const RENDER_PRESET_LABELS: Record<RenderPreset, string> = {
  "720p": "720p",
  "1080p": "1080p",
  "2160p": "4K",
};

export const RENDER_PRESET_DESCRIPTIONS: Record<RenderPreset, string> = {
  "720p": "1280 x 720. Quick to render; fine for a check or a Short.",
  "1080p": "1920 x 1080. The normal upload size.",
  "2160p": "3840 x 2160. Four times the pixels of 1080p; slow to render on this computer.",
};

/** Frame size per preset for a 16:9 and a 9:16 video. */
export const RENDER_PRESET_SIZES: Record<RenderPreset, { landscape: [number, number]; portrait: [number, number] }> = {
  "720p": { landscape: [1280, 720], portrait: [720, 1280] },
  "1080p": { landscape: [1920, 1080], portrait: [1080, 1920] },
  "2160p": { landscape: [3840, 2160], portrait: [2160, 3840] },
};

export function isRenderPreset(value: unknown): value is RenderPreset {
  return typeof value === "string" && (RENDER_PRESETS as readonly string[]).includes(value);
}

/** "4K" for 2160p, the id itself for anything the backend adds later. */
export function renderPresetLabel(preset: string): string {
  return isRenderPreset(preset) ? RENDER_PRESET_LABELS[preset] : preset;
}

// ---- timeline.json ------------------------------------------------------------------

export const POPUP_ANIMS = ["slide_left", "fade", "pop"] as const;
export type PopupAnim = (typeof POPUP_ANIMS)[number];

/** [x, y, w, h] in source-image pixels */
export type PixelRect = [number, number, number, number];

export interface TimelineVoice {
  path: string;
  start_s: number;
}

export interface TimelineMusic {
  path: string | null;
  start_s: number;
  gain_db: number;
  duck_db: number;
  duck_attack_s: number;
  duck_release_s: number;
  fade_in_s: number;
  fade_out_s: number;
  license_ok: boolean;
}

export interface TimelineMotion {
  preset: string;
  start_rect: PixelRect;
  end_rect: PixelRect;
}

export interface TimelineTransition {
  type: string;
  duration_s: number;
}

export interface TimelineScene {
  index: number;
  /** image file, relative to the project folder (06_images/scene_01.png) */
  image: string;
  start_s: number;
  end_s: number;
  motion: TimelineMotion;
  transition_out: TimelineTransition;
  locked: boolean;
}

export interface TimelinePopup {
  scene: number;
  text: string;
  style: string;
  position: string;
  start_s: number;
  end_s: number;
  anim: PopupAnim;
  png: string;
}

export interface CaptionStyle {
  font: string;
  size: number;
  primary: string;
  outline: number;
  position: string;
  max_lines: number;
  max_chars_per_line: number;
  rtl: boolean;
}

export interface CaptionCue {
  start_s: number;
  end_s: number;
  text: string;
}

export interface TimelineCaptions {
  enabled: boolean;
  style: CaptionStyle;
  cues: CaptionCue[];
}

export interface TimelineClip {
  path: string | null;
}

export interface TimelineProxy {
  width: number;
  height: number;
}

/** `07_edit/timeline.json` */
export interface Timeline {
  version: number;
  project_id: string;
  format: ProjectFormat;
  aspect: StoryboardAspect;
  fps: number;
  width: number;
  height: number;
  duration_s: number;
  voice: TimelineVoice;
  music: TimelineMusic;
  scenes: TimelineScene[];
  popups: TimelinePopup[];
  captions: TimelineCaptions;
  intro: TimelineClip;
  outro: TimelineClip;
  presets: string[];
  proxy: TimelineProxy;
}

// ---- review payload -----------------------------------------------------------------

export const RENDER_STATUSES = ["not_rendered", "queued", "rendering", "done", "failed"] as const;
export type RenderStatus = (typeof RENDER_STATUSES)[number];

/** One final render (`07_edit/final_<preset>.mp4`) and where it stands. */
export interface RenderOutput {
  preset: string;
  status: RenderStatus;
  /** relative to the project folder when known */
  path: string | null;
  /** a ready http(s) URL when the backend sends one */
  play_url: string | null;
  /** 0-100 while rendering, when the backend reports it in the payload */
  progress: number | null;
  error: string | null;
  duration_s: number | null;
  size_bytes: number | null;
  width: number | null;
  height: number | null;
}

/** A track from the channel's music folder. */
export interface MusicTrack {
  path: string;
  name: string;
  /** a `<track>.license.txt` sidecar or a LICENSE file in the folder was found */
  license_ok: boolean;
  duration_s: number | null;
}

/** The numbers the Edit panel shows above the timeline. */
export interface TimelineSummary {
  scene_count: number;
  duration_s: number;
  /** transition types used, in order of first use */
  transitions: string[];
  popup_count: number;
  caption_cues: number;
  fps: number;
  width: number;
  height: number;
  aspect: StoryboardAspect | null;
}

/** One scene as the scene strip under the player needs it. */
export interface EditSceneView {
  index: number;
  start_s: number;
  end_s: number;
  /** scene image, relative to the project folder when known */
  image_path: string | null;
  /** a ready http(s) URL when the backend sends one */
  image_url: string | null;
  transition: string | null;
  popup_text: string | null;
  locked: boolean;
}

/** What `GET /api/projects/{id}/stage/edit` returns, after `parseEditPayload`. */
export interface EditReviewPayload {
  /** the proxy video (`07_edit/proxy.mp4`), relative to the project folder */
  proxy_path: string | null;
  /** a ready URL for the proxy when the backend sends one */
  proxy_url: string | null;
  /** the whole timeline when the payload carries it */
  timeline: Timeline | null;
  summary: TimelineSummary;
  scenes: EditSceneView[];
  renders: RenderOutput[];
  /** the presets requested for this project (timeline.presets) */
  presets: string[];
  /** the presets the backend offers; RENDER_PRESETS when it sends none */
  available_presets: string[];
  /** null when the payload does not say; Settings > Render decides then */
  enable_4k: boolean | null;
  music_tracks: MusicTrack[];
  music_path: string | null;
  music_license_ok: boolean;
  captions_enabled: boolean;
  popups_enabled: boolean;
  warnings: string[];
  gate_results: GateResult[];
  /** `07_edit/render.log` when the backend names it */
  render_log_path: string | null;
}

/** `edits` on `POST .../stage/edit/approve` (and on a redo that renders more presets). */
export interface EditApproveEdits {
  presets?: string[];
  music_path?: string | null;
  captions_enabled?: boolean;
  popups_enabled?: boolean;
  /** full replacement, re-validated by the server */
  timeline?: Timeline;
  /** approve even though a blocking check fails (the server refuses otherwise) */
  override_gates?: boolean;
}

// ---------------------------------------------------------------------------
// Defensive parsing of the review payload. The backend's shape is the contract's prose,
// so every field accepts the obvious spellings; unknown fields are kept where a document
// is sent back (the timeline) so an approve never drops what the server wrote.
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function nullableStr(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function nullableNum(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function pixelRect(value: unknown, fallback: PixelRect): PixelRect {
  if (!Array.isArray(value) || value.length !== 4) return fallback;
  const parts = value.map((item) => (typeof item === "number" && Number.isFinite(item) ? item : NaN));
  return parts.some(Number.isNaN) ? fallback : (parts as PixelRect);
}

function parseTimelineScene(raw: unknown, position: number): TimelineScene {
  const base: TimelineScene = {
    index: position,
    image: "",
    start_s: 0,
    end_s: 0,
    motion: { preset: "hold", start_rect: [0, 0, 0, 0], end_rect: [0, 0, 0, 0] },
    transition_out: { type: "fade", duration_s: 0.6 },
    locked: false,
  };
  if (!isRecord(raw)) return base;
  const motion = isRecord(raw.motion) ? raw.motion : {};
  const transition = isRecord(raw.transition_out) ? raw.transition_out : {};
  const image = typeof raw.image === "string" ? raw.image : isRecord(raw.image) ? str(raw.image.path) : str(raw.image_path);
  return {
    ...raw,
    index: num(raw.index, position),
    image,
    start_s: num(raw.start_s),
    end_s: num(raw.end_s),
    motion: {
      ...motion,
      preset: str(motion.preset, base.motion.preset),
      start_rect: pixelRect(motion.start_rect, base.motion.start_rect),
      end_rect: pixelRect(motion.end_rect, base.motion.end_rect),
    },
    transition_out: { ...transition, type: str(transition.type, "fade"), duration_s: num(transition.duration_s, 0.6) },
    locked: bool(raw.locked),
  };
}

function parsePopup(raw: unknown): TimelinePopup | null {
  if (!isRecord(raw)) return null;
  const anim = str(raw.anim);
  return {
    ...raw,
    scene: num(raw.scene),
    text: str(raw.text),
    style: str(raw.style),
    position: str(raw.position),
    start_s: num(raw.start_s),
    end_s: num(raw.end_s),
    anim: (POPUP_ANIMS as readonly string[]).includes(anim) ? (anim as PopupAnim) : "fade",
    png: str(raw.png),
  };
}

function parseCaptions(raw: unknown): TimelineCaptions {
  const style: CaptionStyle = {
    font: "Noto Sans",
    size: 48,
    primary: "#FFFFFF",
    outline: 2,
    position: "bottom",
    max_lines: 2,
    max_chars_per_line: 32,
    rtl: false,
  };
  if (!isRecord(raw)) return { enabled: true, style, cues: [] };
  const rawStyle = isRecord(raw.style) ? raw.style : {};
  const cues = Array.isArray(raw.cues)
    ? raw.cues.filter(isRecord).map((cue) => ({ start_s: num(cue.start_s), end_s: num(cue.end_s), text: str(cue.text) }))
    : [];
  return {
    ...raw,
    enabled: bool(raw.enabled, true),
    style: {
      ...rawStyle,
      font: str(rawStyle.font, style.font),
      size: num(rawStyle.size, style.size),
      primary: str(rawStyle.primary, style.primary),
      outline: num(rawStyle.outline, style.outline),
      position: str(rawStyle.position, style.position),
      max_lines: num(rawStyle.max_lines, style.max_lines),
      max_chars_per_line: num(rawStyle.max_chars_per_line, style.max_chars_per_line),
      rtl: bool(rawStyle.rtl),
    },
    cues,
  };
}

function parseMusic(raw: unknown): TimelineMusic {
  const base: TimelineMusic = {
    path: null,
    start_s: 0,
    gain_db: -18,
    duck_db: -12,
    duck_attack_s: 0.3,
    duck_release_s: 0.8,
    fade_in_s: 1,
    fade_out_s: 2,
    license_ok: false,
  };
  if (!isRecord(raw)) return base;
  return {
    ...raw,
    path: nullableStr(raw.path),
    start_s: num(raw.start_s, base.start_s),
    gain_db: num(raw.gain_db, base.gain_db),
    duck_db: num(raw.duck_db, base.duck_db),
    duck_attack_s: num(raw.duck_attack_s, base.duck_attack_s),
    duck_release_s: num(raw.duck_release_s, base.duck_release_s),
    fade_in_s: num(raw.fade_in_s, base.fade_in_s),
    fade_out_s: num(raw.fade_out_s, base.fade_out_s),
    license_ok: bool(raw.license_ok),
  };
}

export function parseTimeline(raw: unknown): Timeline | null {
  if (!isRecord(raw) || !Array.isArray(raw.scenes)) return null;
  const format: ProjectFormat = raw.format === "shorts" ? "shorts" : "long";
  const voice = isRecord(raw.voice) ? raw.voice : {};
  const proxy = isRecord(raw.proxy) ? raw.proxy : {};
  const scenes = raw.scenes.map((scene, index) => parseTimelineScene(scene, index));
  const lastEnd = scenes.length > 0 ? scenes[scenes.length - 1]!.end_s : 0;
  return {
    ...raw,
    version: num(raw.version, 1),
    project_id: str(raw.project_id),
    format,
    aspect: raw.aspect === "9:16" ? "9:16" : "16:9",
    fps: num(raw.fps, 30),
    width: num(raw.width),
    height: num(raw.height),
    duration_s: num(raw.duration_s, lastEnd),
    voice: { ...voice, path: str(voice.path), start_s: num(voice.start_s) },
    music: parseMusic(raw.music),
    scenes,
    popups: Array.isArray(raw.popups) ? raw.popups.map(parsePopup).filter((popup): popup is TimelinePopup => popup !== null) : [],
    captions: parseCaptions(raw.captions),
    intro: { path: nullableStr(isRecord(raw.intro) ? raw.intro.path : null) },
    outro: { path: nullableStr(isRecord(raw.outro) ? raw.outro.path : null) },
    presets: strList(raw.presets),
    proxy: { width: num(proxy.width, format === "shorts" ? 480 : 854), height: num(proxy.height, format === "shorts" ? 854 : 480) },
  };
}

const DONE_WORDS = new Set(["done", "ok", "ready", "rendered", "complete", "completed", "finished", "success"]);
const RUNNING_WORDS = new Set(["rendering", "running", "in_progress", "progress", "started"]);
const QUEUED_WORDS = new Set(["queued", "pending", "requested", "waiting", "planned"]);
const FAILED_WORDS = new Set(["failed", "error", "cancelled", "canceled"]);

function renderStatus(value: unknown, hasFile: boolean): RenderStatus {
  if (typeof value === "boolean") return value ? "done" : "not_rendered";
  if (typeof value === "string") {
    const word = value.trim().toLowerCase();
    if (DONE_WORDS.has(word)) return "done";
    if (RUNNING_WORDS.has(word)) return "rendering";
    if (QUEUED_WORDS.has(word)) return "queued";
    if (FAILED_WORDS.has(word)) return "failed";
  }
  return hasFile ? "done" : "not_rendered";
}

function parseRender(preset: string, raw: unknown): RenderOutput {
  const base: RenderOutput = {
    preset,
    status: "not_rendered",
    path: null,
    play_url: null,
    progress: null,
    error: null,
    duration_s: null,
    size_bytes: null,
    width: null,
    height: null,
  };
  if (typeof raw === "string") {
    // either a status word or a file path
    const looksLikePath = /[\\/.]/.test(raw) && !DONE_WORDS.has(raw.toLowerCase());
    return looksLikePath ? { ...base, status: "done", path: raw } : { ...base, status: renderStatus(raw, false) };
  }
  if (typeof raw === "boolean") return { ...base, status: raw ? "done" : "not_rendered" };
  if (!isRecord(raw)) return base;
  const path = nullableStr(raw.path) ?? nullableStr(raw.file) ?? nullableStr(raw.output);
  const playUrl = nullableStr(raw.play_url) ?? nullableStr(raw.url);
  const status = renderStatus(raw.status ?? raw.state ?? raw.done ?? raw.rendered, Boolean(path || playUrl));
  return {
    preset,
    status,
    path,
    play_url: playUrl,
    progress: nullableNum(raw.progress ?? raw.pct ?? raw.percent),
    error: nullableStr(raw.error) ?? nullableStr(raw.detail),
    duration_s: nullableNum(raw.duration_s ?? raw.duration),
    size_bytes: nullableNum(raw.size_bytes ?? raw.bytes ?? raw.size),
    width: nullableNum(raw.width),
    height: nullableNum(raw.height),
  };
}

/** Accepts `[{preset, status, ...}]`, `{"1080p": {...}}`, `{"1080p": "done"}` or `{"1080p": "07_edit/final_1080p.mp4"}`. */
export function parseRenders(raw: unknown): RenderOutput[] {
  const renders: RenderOutput[] = [];
  if (Array.isArray(raw)) {
    for (const item of raw) {
      if (isRecord(item)) {
        const preset = str(item.preset) || str(item.name) || str(item.id);
        if (preset) renders.push(parseRender(preset, item));
      } else if (typeof item === "string" && item) {
        renders.push(parseRender(item, "done"));
      }
    }
  } else if (isRecord(raw)) {
    for (const [preset, value] of Object.entries(raw)) renders.push(parseRender(preset, value));
  }
  return renders;
}

function parseMusicTrack(raw: unknown): MusicTrack | null {
  if (typeof raw === "string") return raw ? { path: raw, name: fileName(raw), license_ok: false, duration_s: null } : null;
  if (!isRecord(raw)) return null;
  const path = nullableStr(raw.path) ?? nullableStr(raw.file);
  if (!path) return null;
  const license =
    typeof raw.license_ok === "boolean" ? raw.license_ok : typeof raw.licence_ok === "boolean" ? raw.licence_ok : typeof raw.licensed === "boolean" ? raw.licensed : bool(raw.license);
  return {
    path,
    name: str(raw.name) || str(raw.title) || fileName(path),
    license_ok: license,
    duration_s: nullableNum(raw.duration_s),
  };
}

export function parseMusicTracks(raw: unknown): MusicTrack[] {
  const list = Array.isArray(raw) ? raw : isRecord(raw) && Array.isArray(raw.tracks) ? raw.tracks : [];
  return list.map(parseMusicTrack).filter((track): track is MusicTrack => track !== null);
}

/** The last path segment without its extension: "C:\\music\\calm_piano.mp3" -> "calm_piano". */
export function fileName(path: string): string {
  const last = path.split(/[\\/]+/).filter(Boolean).at(-1) ?? path;
  return last.replace(/\.[a-z0-9]{2,5}$/i, "");
}

function uniqueInOrder(values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const value of values) {
    if (!value || seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}

function parseSceneView(raw: unknown, position: number, popupByScene: Map<number, string>): EditSceneView {
  const base: EditSceneView = {
    index: position,
    start_s: 0,
    end_s: 0,
    image_path: null,
    image_url: null,
    transition: null,
    popup_text: null,
    locked: false,
  };
  if (!isRecord(raw)) return base;
  const index = num(raw.index, position);
  const image = raw.image;
  const imagePath =
    typeof image === "string" ? nullableStr(image) : isRecord(image) ? nullableStr(image.path) : (nullableStr(raw.image_path) ?? nullableStr(raw.thumbnail));
  const imageUrl =
    nullableStr(raw.image_url) ?? nullableStr(raw.thumbnail_url) ?? (isRecord(image) ? nullableStr(image.url) : null);
  const transition = isRecord(raw.transition_out) ? nullableStr(raw.transition_out.type) : nullableStr(raw.transition);
  return {
    index,
    start_s: num(raw.start_s),
    end_s: num(raw.end_s),
    image_path: imagePath,
    image_url: imageUrl,
    transition,
    popup_text: nullableStr(raw.popup_text) ?? (isRecord(raw.popup) ? nullableStr(raw.popup.text) : null) ?? popupByScene.get(index) ?? null,
    locked: bool(raw.locked),
  };
}

function summaryFrom(raw: Record<string, unknown> | null, timeline: Timeline | null, scenes: EditSceneView[]): TimelineSummary {
  const lastEnd = scenes.length > 0 ? Math.max(...scenes.map((scene) => scene.end_s)) : 0;
  const transitions = uniqueInOrder(timeline ? timeline.scenes.map((scene) => scene.transition_out.type) : scenes.map((scene) => scene.transition ?? ""));
  const base: TimelineSummary = {
    scene_count: timeline?.scenes.length ?? scenes.length,
    duration_s: timeline?.duration_s ?? lastEnd,
    transitions,
    popup_count: timeline?.popups.length ?? scenes.filter((scene) => scene.popup_text).length,
    caption_cues: timeline?.captions.cues.length ?? 0,
    fps: timeline?.fps ?? 30,
    width: timeline?.width ?? 0,
    height: timeline?.height ?? 0,
    aspect: timeline?.aspect ?? null,
  };
  if (!raw) return base;
  const rawTransitions = Array.isArray(raw.transitions) ? uniqueInOrder(strList(raw.transitions)) : transitions;
  return {
    scene_count: num(raw.scene_count ?? raw.scenes, base.scene_count),
    duration_s: num(raw.duration_s ?? raw.duration, base.duration_s),
    transitions: rawTransitions.length > 0 ? rawTransitions : base.transitions,
    popup_count: num(raw.popup_count ?? raw.popups, base.popup_count),
    caption_cues: num(raw.caption_cues ?? raw.captions, base.caption_cues),
    fps: num(raw.fps, base.fps),
    width: num(raw.width, base.width),
    height: num(raw.height, base.height),
    aspect: raw.aspect === "9:16" || raw.aspect === "16:9" ? raw.aspect : base.aspect,
  };
}

/**
 * Turn whatever `GET /api/projects/{id}/stage/edit` returns into a review payload.
 * Returns null when the stage has not written anything yet (the engine answers `{}`).
 */
export function parseEditPayload(raw: unknown): EditReviewPayload | null {
  if (!isRecord(raw) || Object.keys(raw).length === 0) return null;

  const timeline = parseTimeline(raw.timeline);
  const proxy = raw.proxy;
  const proxyPath =
    (typeof proxy === "string" ? nullableStr(proxy) : isRecord(proxy) ? nullableStr(proxy.path) : null) ?? nullableStr(raw.proxy_path);
  const proxyUrl =
    (isRecord(proxy) ? (nullableStr(proxy.play_url) ?? nullableStr(proxy.url)) : null) ?? nullableStr(raw.proxy_url) ?? nullableStr(raw.play_url);

  const popupByScene = new Map<number, string>();
  for (const popup of timeline?.popups ?? []) if (popup.text && !popupByScene.has(popup.scene)) popupByScene.set(popup.scene, popup.text);
  const rawScenes = Array.isArray(raw.scenes) ? raw.scenes : Array.isArray(raw.timeline_scenes) ? raw.timeline_scenes : null;
  const scenes = rawScenes
    ? rawScenes.map((scene, index) => parseSceneView(scene, index, popupByScene))
    : (timeline?.scenes.map((scene) => parseSceneView(scene, scene.index, popupByScene)) ?? []);

  const summaryRaw = isRecord(raw.summary) ? raw.summary : isRecord(raw.timeline_summary) ? raw.timeline_summary : null;
  const summary = summaryFrom(summaryRaw, timeline, scenes);

  const renders = parseRenders(raw.renders ?? raw.render_status ?? raw.outputs ?? raw.finals);
  // The requested presets: what the backend says, else the timeline's list, else whatever
  // has a render under way or done (a "not_rendered" row is only an offer, not a request).
  const requested = strList(raw.presets);
  const presets = uniqueInOrder(
    requested.length > 0
      ? requested
      : timeline && timeline.presets.length > 0
        ? timeline.presets
        : renders.filter((render) => render.status !== "not_rendered" && render.status !== "failed").map((render) => render.preset),
  );
  const available = uniqueInOrder(strList(raw.available_presets ?? raw.presets_available ?? raw.preset_options));
  const render = isRecord(raw.render) ? raw.render : {};
  const enable4k = typeof raw.enable_4k === "boolean" ? raw.enable_4k : typeof render.enable_4k === "boolean" ? render.enable_4k : null;

  const music = isRecord(raw.music) ? raw.music : {};
  const musicTracks = parseMusicTracks(raw.music_tracks ?? raw.tracks ?? music.tracks);
  const musicPath = nullableStr(raw.music_path) ?? nullableStr(music.path) ?? timeline?.music.path ?? null;
  const musicLicense =
    typeof raw.music_license_ok === "boolean"
      ? raw.music_license_ok
      : typeof music.license_ok === "boolean"
        ? music.license_ok
        : (timeline?.music.license_ok ?? musicTracks.find((track) => track.path === musicPath)?.license_ok ?? false);

  const captions = isRecord(raw.captions) ? raw.captions : {};
  const captionsEnabled =
    typeof raw.captions_enabled === "boolean" ? raw.captions_enabled : typeof captions.enabled === "boolean" ? captions.enabled : (timeline?.captions.enabled ?? true);
  const popups = isRecord(raw.popups) ? raw.popups : {};
  const popupsEnabled = typeof raw.popups_enabled === "boolean" ? raw.popups_enabled : typeof popups.enabled === "boolean" ? popups.enabled : true;

  const gates = parseGateResults(raw.gate_results);
  return {
    proxy_path: proxyPath,
    proxy_url: proxyUrl,
    timeline,
    summary,
    scenes,
    renders,
    presets,
    available_presets: available.length > 0 ? available : [...RENDER_PRESETS],
    enable_4k: enable4k,
    music_tracks: musicTracks,
    music_path: musicPath,
    music_license_ok: musicLicense,
    captions_enabled: captionsEnabled,
    popups_enabled: popupsEnabled,
    warnings: strList(raw.warnings),
    gate_results: gates,
    render_log_path: nullableStr(raw.render_log) ?? nullableStr(raw.render_log_path),
  };
}

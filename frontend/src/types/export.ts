/**
 * Export stage shapes. Mirrors backend/cashcow_studio/models/export.py (Pydantic v2, the
 * source of truth); see docs/M3-M4-CONTRACT.md section 4.
 *
 * The review payload of `GET /api/projects/{id}/stage/export` carries the three thumbnail
 * variants, the SEO pack (title, description, tags, chapters, pinned comment, hashtags),
 * the disclosure flag with its provenance, and the export folder. Approving exports.
 */
import type { GateResult } from "./project";
import { parseGateResults } from "./script";

export const THUMBNAIL_CHOICES = ["v1", "v2", "v3"] as const;
export type ThumbnailChoice = (typeof THUMBNAIL_CHOICES)[number];

/** The files the export stage writes for each variant (08_export/). */
export const THUMBNAIL_FILES: Record<ThumbnailChoice, string> = {
  v1: "08_export/thumbnail.png",
  v2: "08_export/thumbnail_v2.png",
  v3: "08_export/thumbnail_v3.png",
};
export const THUMBNAIL_SHORTS_FILE = "08_export/thumbnail_shorts.png";

/** YouTube's own limits, so the editor can warn before the upload does. */
export const TITLE_MAX_CHARS = 100;
export const DESCRIPTION_MAX_CHARS = 5000;
/** all tags together, as YouTube counts them */
export const TAGS_MAX_CHARS = 500;
export const PINNED_COMMENT_MAX_CHARS = 10_000;
export const HASHTAGS_MAX = 15;

export interface ThumbnailVariant {
  id: ThumbnailChoice;
  /** the headline drawn on the picture */
  headline: string;
  /** relative to the project folder when known */
  path: string | null;
  /** a ready http(s) URL when the backend sends one */
  url: string | null;
  /** the 9:16 version (Shorts), when there is one */
  shorts_path: string | null;
  shorts_url: string | null;
}

export interface Chapter {
  /** "mm:ss" (or "h:mm:ss") */
  time: string;
  title: string;
}

export interface ExportMetadata {
  title: string;
  description: string;
  tags: string[];
  chapters: Chapter[];
  pinned_comment: string;
  hashtags: string[];
}

export interface ProvenanceImage {
  scene: number;
  provider: string;
  model: string;
  synthid: boolean | null;
  c2pa: boolean | null;
}

export interface ProvenanceVoice {
  provider: string;
  model: string;
  consent_ref: string;
}

export interface Provenance {
  images: ProvenanceImage[];
  voice: ProvenanceVoice | null;
}

/** A file in the export folder, once the export ran. */
export interface ExportFile {
  name: string;
  path: string;
  size_bytes: number | null;
}

/** What `GET /api/projects/{id}/stage/export` returns, after `parseExportPayload`. */
export interface ExportReviewPayload {
  thumbnails: ThumbnailVariant[];
  thumbnail_choice: ThumbnailChoice;
  metadata: ExportMetadata;
  altered_or_synthetic: boolean;
  provenance: Provenance | null;
  /** the folder the files go to (or went to); null until the backend decides */
  export_folder: string | null;
  /** true once the files were copied there */
  exported: boolean;
  files: ExportFile[];
  /** the final renders that exist / will be exported */
  presets: string[];
  /** selected presets whose final render is missing (the files_present gate blocks) */
  missing_presets: string[];
  /** the words the headline may have (channel thumbnail config) */
  max_headline_words: number | null;
  /** links to 08_export/provenance.json and provenance.md when the backend sends them */
  provenance_url: string | null;
  provenance_md_url: string | null;
  /** the LLM check that the title's claim appears early in the script; null = not checked */
  title_promise_early: boolean | null;
  title_promise_note: string;
  warnings: string[];
  gate_results: GateResult[];
}

/** `edits` on `POST .../stage/export/approve`. Approving exports. */
export interface ExportApproveEdits {
  metadata?: ExportMetadata;
  thumbnail_choice?: ThumbnailChoice;
  altered_or_synthetic?: boolean;
  /** re-render only the text on the chosen thumbnail */
  headline?: string;
  /** approve even though a blocking check fails (the server refuses otherwise) */
  override_gates?: boolean;
}

export function emptyMetadata(): ExportMetadata {
  return { title: "", description: "", tags: [], chapters: [], pinned_comment: "", hashtags: [] };
}

/** How YouTube counts the tag field: every tag plus a comma between them. */
export function tagsLength(tags: string[]): number {
  return tags.reduce((sum, tag) => sum + tag.length, 0) + Math.max(0, tags.length - 1);
}

/** "mm:ss" or "h:mm:ss" from seconds. */
export function formatChapterTime(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** Seconds from "mm:ss", "m:ss" or "h:mm:ss"; null when the text is not a time. */
export function parseChapterTime(text: string): number | null {
  const parts = text.trim().split(":");
  if (parts.length < 2 || parts.length > 3 || parts.some((part) => !/^\d{1,2}$/.test(part))) return null;
  const numbers = parts.map(Number);
  if (numbers.some((value, index) => index > 0 && value > 59)) return null;
  return numbers.reduce((sum, value) => sum * 60 + value, 0);
}

// ---------------------------------------------------------------------------
// Defensive parsing of the review payload (same approach as the storyboard types).
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

function nullableBool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function strList(value: unknown): string[] {
  if (typeof value === "string") {
    return value
      .split(/[,\n]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean) : [];
}

function isChoice(value: unknown): value is ThumbnailChoice {
  return typeof value === "string" && (THUMBNAIL_CHOICES as readonly string[]).includes(value);
}

/** "v1" from "v1", "1", 1, "thumbnail_v2.png", "variant_3"... */
function choiceFrom(value: unknown, fallback: ThumbnailChoice | null): ThumbnailChoice | null {
  if (isChoice(value)) return value;
  if (typeof value === "number" && value >= 1 && value <= 3) return `v${value}` as ThumbnailChoice;
  if (typeof value === "string") {
    const match = /(?:^|[^0-9])([123])(?:[^0-9]|$)/.exec(value);
    if (match) return `v${match[1]}` as ThumbnailChoice;
  }
  return fallback;
}

/** A bare file name from the stage ("thumbnail_v2.png") lives inside 08_export. */
function exportFilePath(value: string | null): string | null {
  if (!value) return null;
  return /[\\/]/.test(value) ? value : `08_export/${value}`;
}

function parseVariant(raw: unknown, fallbackId: ThumbnailChoice | null): ThumbnailVariant | null {
  if (typeof raw === "string") {
    if (!fallbackId) return null;
    const isUrl = /^(https?:)?\/\//i.test(raw) || raw.startsWith("/") || raw.startsWith("data:");
    return { id: fallbackId, headline: "", path: isUrl ? THUMBNAIL_FILES[fallbackId] : exportFilePath(raw), url: isUrl ? raw : null, shorts_path: null, shorts_url: null };
  }
  if (!isRecord(raw)) return null;
  const id = choiceFrom(raw.id ?? raw.choice ?? raw.variant ?? raw.name, fallbackId);
  if (!id) return null;
  const shorts = isRecord(raw.shorts) ? raw.shorts : {};
  return {
    id,
    headline: str(raw.headline) || str(raw.text) || str(raw.title),
    path: nullableStr(raw.path) ?? exportFilePath(nullableStr(raw.file)) ?? THUMBNAIL_FILES[id],
    url: nullableStr(raw.url) ?? nullableStr(raw.image_url) ?? nullableStr(raw.play_url),
    shorts_path: nullableStr(raw.shorts_path) ?? exportFilePath(nullableStr(raw.file_shorts)) ?? nullableStr(shorts.path) ?? (id === "v1" ? THUMBNAIL_SHORTS_FILE : null),
    shorts_url: nullableStr(raw.shorts_url) ?? nullableStr(raw.url_shorts) ?? nullableStr(shorts.url),
  };
}

/** Accepts `[{id, headline, path, url}]`, `{v1: {...}, v2: "...", v3: "..."}` or nothing (then the three default files). */
export function parseThumbnails(raw: unknown): ThumbnailVariant[] {
  const found = new Map<ThumbnailChoice, ThumbnailVariant>();
  if (Array.isArray(raw)) {
    raw.forEach((item, index) => {
      const fallback = index < 3 ? (THUMBNAIL_CHOICES[index] as ThumbnailChoice) : null;
      const variant = parseVariant(item, fallback);
      if (variant && !found.has(variant.id)) found.set(variant.id, variant);
    });
  } else if (isRecord(raw)) {
    for (const [key, value] of Object.entries(raw)) {
      const id = choiceFrom(key, null);
      if (!id) continue;
      const variant = parseVariant(value, id);
      if (variant && !found.has(variant.id)) found.set(variant.id, variant);
    }
  }
  if (found.size === 0) {
    return THUMBNAIL_CHOICES.map((id) => ({
      id,
      headline: "",
      path: THUMBNAIL_FILES[id],
      url: null,
      shorts_path: id === "v1" ? THUMBNAIL_SHORTS_FILE : null,
      shorts_url: null,
    }));
  }
  return THUMBNAIL_CHOICES.filter((id) => found.has(id)).map((id) => found.get(id)!);
}

function parseChapter(raw: unknown): Chapter | null {
  if (!isRecord(raw)) return null;
  const time = typeof raw.time === "string" ? raw.time.trim() : typeof raw.time_s === "number" ? formatChapterTime(raw.time_s) : typeof raw.start_s === "number" ? formatChapterTime(raw.start_s) : "";
  const title = str(raw.title) || str(raw.name);
  if (!time && !title) return null;
  return { time, title };
}

/**
 * Only the six SEO fields: the backend's metadata.json also carries ids, file names and the
 * disclosure, which the approve sends through their own edits, never inside `metadata`.
 */
export function parseMetadata(raw: unknown): ExportMetadata {
  const base = emptyMetadata();
  if (!isRecord(raw)) return base;
  return {
    title: str(raw.title),
    description: str(raw.description),
    tags: strList(raw.tags),
    chapters: Array.isArray(raw.chapters) ? raw.chapters.map(parseChapter).filter((chapter): chapter is Chapter => chapter !== null) : [],
    pinned_comment: str(raw.pinned_comment),
    hashtags: strList(raw.hashtags).map((tag) => (tag.startsWith("#") ? tag : `#${tag}`)),
  };
}

export function parseProvenance(raw: unknown): Provenance | null {
  if (!isRecord(raw)) return null;
  const images = Array.isArray(raw.images)
    ? raw.images.filter(isRecord).map((image) => ({
        scene: num(image.scene),
        provider: str(image.provider),
        model: str(image.model),
        synthid: nullableBool(image.synthid),
        c2pa: nullableBool(image.c2pa),
      }))
    : [];
  const voice = isRecord(raw.voice) ? { provider: str(raw.voice.provider), model: str(raw.voice.model), consent_ref: str(raw.voice.consent_ref) } : null;
  return { images, voice };
}

function parseFile(raw: unknown): ExportFile | null {
  if (typeof raw === "string") {
    return raw ? { name: raw.split(/[\\/]+/).filter(Boolean).at(-1) ?? raw, path: raw, size_bytes: null } : null;
  }
  if (!isRecord(raw)) return null;
  const path = nullableStr(raw.path) ?? nullableStr(raw.source) ?? nullableStr(raw.file);
  const name = nullableStr(raw.name) ?? (path ? (path.split(/[\\/]+/).filter(Boolean).at(-1) ?? path) : null);
  if (!name) return null;
  return { name, path: path ?? name, size_bytes: nullableNum(raw.size_bytes ?? raw.bytes ?? raw.size) };
}

/**
 * Turn whatever `GET /api/projects/{id}/stage/export` returns into a review payload.
 * Returns null when the stage has not written anything yet (the engine answers `{}`).
 */
export function parseExportPayload(raw: unknown): ExportReviewPayload | null {
  if (!isRecord(raw) || Object.keys(raw).length === 0) return null;
  const thumbnails = parseThumbnails(raw.thumbnails ?? raw.thumbnail_variants ?? raw.thumbnail);
  const choice = choiceFrom(raw.thumbnail_choice ?? raw.chosen_thumbnail ?? raw.choice, null);
  const metadataRaw = isRecord(raw.metadata) ? raw.metadata : isRecord(raw.seo) ? raw.seo : raw;
  const disclosure = isRecord(raw.disclosure) ? raw.disclosure : isRecord(metadataRaw.disclosure) ? metadataRaw.disclosure : {};
  const files = Array.isArray(raw.files) ? raw.files : Array.isArray(raw.exported_files) ? raw.exported_files : [];
  const parsedFiles = files.map(parseFile).filter((file): file is ExportFile => file !== null);
  const exported = typeof raw.exported === "boolean" ? raw.exported : typeof raw.export_done === "boolean" ? raw.export_done : parsedFiles.length > 0;
  const thumbnailConfig = isRecord(raw.thumbnail_config) ? raw.thumbnail_config : {};
  return {
    thumbnails,
    thumbnail_choice: choice && thumbnails.some((variant) => variant.id === choice) ? choice : (thumbnails[0]?.id ?? "v1"),
    metadata: parseMetadata(metadataRaw),
    altered_or_synthetic:
      typeof raw.altered_or_synthetic === "boolean" ? raw.altered_or_synthetic : typeof disclosure.altered_or_synthetic === "boolean" ? disclosure.altered_or_synthetic : true,
    provenance: parseProvenance(raw.provenance ?? disclosure.provenance),
    export_folder: nullableStr(raw.export_folder) ?? nullableStr(raw.folder) ?? nullableStr(raw.export_dir),
    exported,
    files: parsedFiles,
    presets: strList(raw.presets),
    missing_presets: strList(raw.missing_presets),
    max_headline_words: nullableNum(raw.max_headline_words ?? thumbnailConfig.max_headline_words),
    provenance_url: nullableStr(raw.provenance_url),
    provenance_md_url: nullableStr(raw.provenance_md_url),
    title_promise_early: nullableBool(raw.title_promise_early),
    title_promise_note: str(raw.title_promise_note),
    warnings: strList(raw.warnings),
    gate_results: parseGateResults(raw.gate_results),
  };
}

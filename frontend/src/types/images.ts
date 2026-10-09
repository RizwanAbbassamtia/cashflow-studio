/**
 * Images stage shapes. Mirrors backend/cashcow_studio/models/images.py (Pydantic v2, the
 * source of truth); see docs/M3-M4-CONTRACT.md section 2.
 *
 * `06_images/images.json` (`ImagesDoc`) records every scene picture with its provider,
 * model, seed, cost, provenance flags and QA verdict. The review payload of
 * `GET /api/projects/{id}/stage/images` (`ImagesReviewPayload`) is the scene grid:
 * `image_url`, status, QA verdict, attempts and the lock per scene, plus warnings and the
 * monthly budget. Scenes are addressed by the storyboard scene index (0-based).
 *
 * The parsers accept the model's names plus a few plain variants and build missing image
 * URLs from the project file endpoint.
 */
import type { GateResult } from "./project";
import { parseGateResults } from "./script";
import type { StoryboardAspect } from "./storyboard";

/** The QA model's verdict (`LLMClient.analyze_image(task="image_qa")`). */
export interface ImageQA {
  matches_prompt: boolean;
  has_text: boolean;
  has_real_person: boolean;
  has_logo: boolean;
  artifacts: string[];
  /** 0-10; under QA_MIN_SCORE is a reject */
  score: number;
  reason: string;
}

export const QA_MIN_SCORE = 5;
export const QA_MAX_RETRIES = 3;

/** The rule the stage applies: any flag or a low score rejects the picture. */
export function qaPasses(qa: ImageQA): boolean {
  return qa.matches_prompt && !qa.has_text && !qa.has_real_person && !qa.has_logo && qa.score >= QA_MIN_SCORE;
}

/** Plain-English reasons a picture failed QA, in the order the stage checks them. */
export function qaFlags(qa: ImageQA): string[] {
  const flags: string[] = [];
  if (!qa.matches_prompt) flags.push("Does not show what the scene asked for");
  if (qa.has_text) flags.push("Contains text");
  if (qa.has_real_person) flags.push("Shows a real person");
  if (qa.has_logo) flags.push("Contains a logo");
  if (qa.score < QA_MIN_SCORE) flags.push(`Quality score ${qa.score} of 10 (at least ${QA_MIN_SCORE} needed)`);
  for (const artifact of qa.artifacts) flags.push(artifact);
  return flags;
}

export interface ImageProvenance {
  c2pa: boolean | null;
  synthid: boolean | null;
}

/** The backend's status words: no picture, a picture that passed, one a person approved, the last try rejected. */
export const IMAGE_RECORD_STATUSES = ["pending", "generated", "approved", "rejected"] as const;
export type ImageRecordStatus = (typeof IMAGE_RECORD_STATUSES)[number];

export const IMAGE_SOURCES = ["provider", "upload"] as const;
export type ImageSource = (typeof IMAGE_SOURCES)[number];

/** One scene in the review grid (`ImagesReviewScene`) and in `images.json` (`SceneImageRecord`). */
export interface SceneImageRecord {
  /** storyboard scene index, 0-based */
  scene: number;
  /** `scene_NN.png` inside 06_images, if any (relative to the project folder) */
  file: string | null;
  image_url: string | null;
  status: ImageRecordStatus;
  source: ImageSource;
  /** plain English from the stage: "Passed", "Rejected: ...", "Uploaded" */
  verdict: string;
  qa: ImageQA | null;
  /** generation tries so far (QA retries included) */
  attempts: number;
  locked: boolean;
  /** the scene's narration, when the payload carries it */
  narration: string;
  /** the scene description sent to the tool (the review payload leaves the style guide out) */
  prompt: string;
  negative_prompt: string;
  provider: string;
  model: string;
  seed: number | null;
  cost_usd: number;
  provenance: ImageProvenance;
  /** plain note when the accepted picture looks like another scene's */
  duplicate_of: string | null;
  /** why there is no accepted picture */
  error: string | null;
  /** URLs of rejected tries kept in `06_images/rejected/` */
  rejected_urls: string[];
  phash: string | null;
  notes: string[];
}

export interface ImagesBudget {
  /** `images.monthly_budget_images`, or null when unlimited */
  monthly_limit: number | null;
  /** pictures made for this channel this month before this run (all tries) */
  used_this_month: number;
  generated_now: number;
  warning: string | null;
}

/** `06_images/images.json` */
export interface ImagesDoc {
  project_id: string;
  channel_slug: string;
  aspect: StoryboardAspect;
  size: string;
  provider: string;
  model: string;
  style_guide: string;
  /** `style_sheet.png` inside 06_images */
  style_sheet: string | null;
  style_sheet_source: string;
  generated_at: string | null;
  scenes: SceneImageRecord[];
  cost_usd: number;
  qa_cost_usd: number;
  budget: ImagesBudget;
  gate_results: GateResult[];
  warnings: string[];
  notes: string[];
  schema_version: number;
}

/** What `GET /api/projects/{id}/stage/images` returns, normalised. */
export interface ImagesReviewPayload {
  project_id: string;
  aspect: StoryboardAspect;
  size: string;
  provider: string;
  model: string;
  style_sheet_url: string | null;
  style_sheet_source: string;
  scenes: SceneImageRecord[];
  /** scenes with an accepted picture */
  accepted: number;
  total: number;
  cost_usd: number;
  qa_cost_usd: number;
  budget: ImagesBudget;
  warnings: string[];
  gate_results: GateResult[];
  generated_at: string | null;
}

export interface RegenerateEdit {
  scene: number;
  note: string;
}

export interface UploadEdit {
  scene: number;
  /** relative path returned by `POST /api/projects/{id}/upload/images` */
  path: string;
}

/** `edits` on `POST .../stage/images/approve` (and, kept for the next run, on redo). */
export type ImagesApproveEdits = {
  regenerate?: RegenerateEdit[];
  uploads?: UploadEdit[];
  /** the scenes whose picture must survive a regenerate */
  lock?: number[];
  /** approve even though a blocking check fails (the server refuses otherwise) */
  override_gates?: boolean;
};

/** True when the scene has a picture a person can keep: generated and passed, approved, or uploaded. */
export function hasAcceptedImage(scene: SceneImageRecord): boolean {
  return scene.image_url !== null && (scene.status === "generated" || scene.status === "approved");
}

/** Scenes without an accepted picture: what blocks the `images.qa` gate. */
export function scenesWithoutImage(scenes: SceneImageRecord[]): SceneImageRecord[] {
  return scenes.filter((scene) => !hasAcceptedImage(scene));
}

// ---------------------------------------------------------------------------
// Defensive parsing
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

function bool(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function nullableBool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function strList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function oneOf<T extends string>(value: unknown, allowed: ReadonlyArray<T>, fallback: T): T {
  return typeof value === "string" && (allowed as ReadonlyArray<string>).includes(value) ? (value as T) : fallback;
}

function pick(raw: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) {
    if (raw[key] !== undefined && raw[key] !== null) return raw[key];
  }
  return undefined;
}

import type { FileUrlBuilder } from "./timing";

function isAbsoluteUrl(value: string): boolean {
  return /^(https?:)?\/\//i.test(value) || value.startsWith("/") || value.startsWith("blob:") || value.startsWith("data:");
}

/** `C:\...\06_images\scene_01.png`, `06_images/scene_01.png` or `scene_01.png` -> a path inside the project folder. */
export function projectRelativeImagePath(value: string): string {
  const parts = value.split(/[\\/]+/).filter(Boolean);
  const start = parts.findIndex((part) => /^0[1-8]_/.test(part));
  if (start >= 0) return parts.slice(start).join("/");
  // A bare file name belongs to the images folder.
  return parts.length === 1 ? `06_images/${parts[0]}` : parts.join("/");
}

function toUrl(value: unknown, fileUrl: FileUrlBuilder): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  if (isAbsoluteUrl(value)) return value;
  const relative = projectRelativeImagePath(value);
  return relative ? fileUrl(relative) : null;
}

export function parseImageQA(raw: unknown): ImageQA | null {
  if (!isRecord(raw)) return null;
  return {
    matches_prompt: bool(raw.matches_prompt, true),
    has_text: bool(raw.has_text),
    has_real_person: bool(raw.has_real_person),
    has_logo: bool(raw.has_logo),
    artifacts: strList(raw.artifacts),
    score: num(raw.score, 0),
    reason: str(raw.reason),
  };
}

/** The status from the model's word, with a fallback from the QA verdict and the file. */
function parseStatus(raw: Record<string, unknown>, qa: ImageQA | null, hasFile: boolean): ImageRecordStatus {
  const word = str(raw.status).toLowerCase();
  if ((IMAGE_RECORD_STATUSES as readonly string[]).includes(word)) {
    const status = word as ImageRecordStatus;
    return status !== "pending" && status !== "rejected" && !hasFile ? "pending" : status;
  }
  if (word === "accepted" || word === "ok" || word === "passed") return hasFile ? "generated" : "pending";
  if (word === "failed" || word === "fail") return "rejected";
  if (typeof raw.accepted === "boolean") return raw.accepted && hasFile ? "generated" : "rejected";
  if (!hasFile) return "pending";
  return qa && !qaPasses(qa) ? "rejected" : "generated";
}

export function parseSceneImageRecord(raw: unknown, position: number, fileUrl: FileUrlBuilder): SceneImageRecord {
  const record = isRecord(raw) ? raw : {};
  const qa = parseImageQA(record.qa);
  const rawFile = pick(record, ["file", "path", "image_path"]);
  const file = typeof rawFile === "string" && rawFile.trim() ? projectRelativeImagePath(rawFile) : null;
  const imageUrl = toUrl(pick(record, ["image_url", "url"]), fileUrl) ?? (file ? fileUrl(file) : null);
  const provenance = isRecord(record.provenance) ? record.provenance : {};
  const seed = record.seed;
  const rejected = pick(record, ["rejected_urls", "rejected"]);
  const rejectedUrls = Array.isArray(rejected)
    ? rejected
        .map((item) => (isRecord(item) ? toUrl(pick(item, ["image_url", "url", "file", "path"]), fileUrl) : toUrl(item, fileUrl)))
        .filter((url): url is string => url !== null)
    : [];
  const source: ImageSource = oneOf(record.source, IMAGE_SOURCES, bool(record.uploaded) ? "upload" : "provider");
  return {
    scene: Math.max(0, Math.trunc(num(pick(record, ["scene", "index", "scene_index"]), position))),
    file,
    image_url: imageUrl,
    status: parseStatus(record, qa, imageUrl !== null),
    source,
    verdict: str(record.verdict),
    qa,
    attempts: Math.max(0, Math.trunc(num(pick(record, ["attempts", "attempt"]), imageUrl ? 1 : 0))),
    locked: isRecord(record.locked) ? bool(record.locked.image) || bool(record.locked.image_prompt) : bool(record.locked),
    narration: str(record.narration),
    prompt: str(pick(record, ["prompt", "scene_prompt", "image_prompt", "prompt_used"])),
    negative_prompt: str(record.negative_prompt),
    provider: str(record.provider),
    model: str(record.model),
    seed: typeof seed === "number" && Number.isFinite(seed) ? seed : null,
    cost_usd: num(record.cost_usd),
    provenance: { c2pa: nullableBool(provenance.c2pa), synthid: nullableBool(provenance.synthid) },
    duplicate_of: nullableStr(record.duplicate_of),
    error: nullableStr(record.error),
    rejected_urls: rejectedUrls,
    phash: nullableStr(record.phash),
    notes: strList(record.notes),
  };
}

function parseAspect(value: unknown, fallback: StoryboardAspect = "16:9"): StoryboardAspect {
  return value === "9:16" ? "9:16" : value === "16:9" ? "16:9" : fallback;
}

export function parseImagesBudget(raw: unknown): ImagesBudget {
  const record = isRecord(raw) ? raw : {};
  const limit = pick(record, ["monthly_limit", "limit", "monthly_budget_images"]);
  return {
    monthly_limit: typeof limit === "number" && Number.isFinite(limit) && limit > 0 ? limit : null,
    used_this_month: num(pick(record, ["used_this_month", "used", "count"])),
    generated_now: num(record.generated_now),
    warning: nullableStr(record.warning),
  };
}

export function parseImagesDoc(raw: unknown, fileUrl: FileUrlBuilder): ImagesDoc | null {
  if (!isRecord(raw) || !Array.isArray(raw.scenes)) return null;
  const scenes = raw.scenes.map((scene, index) => parseSceneImageRecord(scene, index, fileUrl));
  const styleSheet = pick(raw, ["style_sheet", "style_sheet_path"]);
  return {
    project_id: str(raw.project_id),
    channel_slug: str(raw.channel_slug),
    aspect: parseAspect(raw.aspect),
    size: str(raw.size),
    provider: str(raw.provider),
    model: str(raw.model),
    style_guide: str(raw.style_guide),
    style_sheet: typeof styleSheet === "string" && styleSheet.trim() ? projectRelativeImagePath(styleSheet) : null,
    style_sheet_source: str(raw.style_sheet_source, "none"),
    generated_at: typeof raw.generated_at === "string" ? raw.generated_at : null,
    scenes,
    cost_usd: num(pick(raw, ["cost_usd", "total_cost_usd"]), scenes.reduce((sum, scene) => sum + scene.cost_usd, 0)),
    qa_cost_usd: num(raw.qa_cost_usd),
    budget: parseImagesBudget(raw.budget),
    gate_results: parseGateResults(raw.gate_results),
    warnings: strList(raw.warnings),
    notes: strList(raw.notes),
    schema_version: num(raw.schema_version, 1),
  };
}

/**
 * Turn whatever `GET /api/projects/{id}/stage/images` returns into a review payload.
 * Returns null when no scene list can be found in it.
 */
export function parseImagesPayload(raw: unknown, fileUrl: FileUrlBuilder): ImagesReviewPayload | null {
  if (!isRecord(raw)) return null;
  const doc = parseImagesDoc(raw.images, fileUrl);
  const list = pick(raw, ["scenes", "grid"]);
  const scenes = Array.isArray(list) ? list.map((scene, index) => parseSceneImageRecord(scene, index, fileUrl)) : (doc?.scenes ?? null);
  if (!scenes) return null;
  scenes.sort((a, b) => a.scene - b.scene);
  const styleSheet = toUrl(pick(raw, ["style_sheet_url", "style_sheet"]), fileUrl) ?? (doc?.style_sheet ? fileUrl(doc.style_sheet) : null);
  const budget = raw.budget !== undefined ? parseImagesBudget(raw.budget) : (doc?.budget ?? parseImagesBudget(null));
  const warnings = strList(raw.warnings);
  if (budget.warning && !warnings.includes(budget.warning)) warnings.push(budget.warning);
  return {
    project_id: str(raw.project_id, doc?.project_id ?? ""),
    aspect: parseAspect(raw.aspect, doc?.aspect ?? "16:9"),
    size: str(raw.size, doc?.size ?? ""),
    provider: str(raw.provider, doc?.provider ?? scenes.find((scene) => scene.provider)?.provider ?? ""),
    model: str(raw.model, doc?.model ?? scenes.find((scene) => scene.model)?.model ?? ""),
    style_sheet_url: styleSheet,
    style_sheet_source: str(raw.style_sheet_source, doc?.style_sheet_source ?? "none"),
    scenes,
    accepted: num(raw.accepted, scenes.filter(hasAcceptedImage).length),
    total: num(raw.total, scenes.length),
    cost_usd: num(pick(raw, ["cost_usd", "total_cost_usd"]), doc?.cost_usd ?? scenes.reduce((sum, scene) => sum + scene.cost_usd, 0)),
    qa_cost_usd: num(raw.qa_cost_usd, doc?.qa_cost_usd ?? 0),
    budget,
    warnings,
    gate_results: parseGateResults(pick(raw, ["gate_results", "gates"])),
    generated_at: typeof raw.generated_at === "string" ? raw.generated_at : (doc?.generated_at ?? null),
  };
}

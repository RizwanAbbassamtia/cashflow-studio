// Mirrors backend/cashflow_studio/models/project.py. Extend here when the model grows.
import type { Language, StageMode, StageName, VideoFormat } from "./channel";

export const STAGE_ORDER: StageName[] = [
  "research",
  "title",
  "script",
  "storyboard",
  "voice",
  "images",
  "edit",
  "export",
];

export type StageStatus =
  | "pending"
  | "running"
  | "awaiting_review"
  | "awaiting_manual"
  | "approved"
  | "done"
  | "failed"
  | "skipped";

export type ProjectFormat = "long" | "shorts";
export type SourceKind = "ai_pick" | "manual_pick" | "own_topic";

export interface ProjectSource {
  kind: SourceKind;
  video_id?: string | null;
  video_url?: string | null;
  topic_text?: string | null;
}

export interface StageState {
  status: StageStatus;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  attempts: number;
  approved_by?: string | null;
  approved_at?: string | null;
  /** what the reviewer wrote (approve, redo and skip notes); stages add them to prompts */
  notes: string[];
  /** stage edits sent with a redo (e.g. locked_paragraph_ids), used by the next run */
  pending_edits?: Record<string, unknown>;
  summary: string;
  gate_results: GateResult[];
  /** provenance, one plain-English line per event: started, finished, approved, redone... */
  history?: string[];
}

export interface GateResult {
  id: string;
  title: string;
  severity: "block" | "warn";
  passed: boolean;
  detail: string;
}

export interface ProjectCosts {
  llm_usd: number;
  voice_usd: number;
  images_usd: number;
}

export interface Project {
  id: string;
  channel_slug: string;
  topic_slug: string;
  title: string;
  format: ProjectFormat;
  language: Language;
  created_at: string;
  updated_at: string;
  folder: string;
  source: ProjectSource;
  stage_modes: Record<StageName, StageMode>;
  stages: Record<StageName, StageState>;
  costs: ProjectCosts;
  current_stage: StageName;
  schema_version: number;
}

export interface ProjectSummary {
  id: string;
  channel_slug: string;
  title: string;
  format: ProjectFormat;
  language: Language;
  current_stage: StageName;
  status: StageStatus;
  updated_at: string;
  costs_total_usd: number;
}

export interface ProjectCreate {
  channel_slug: string;
  format: ProjectFormat;
  source: ProjectSource;
  stage_mode_overrides?: Partial<Record<StageName, StageMode>>;
}

export const JOB_STATUSES = ["queued", "running", "done", "failed"] as const;
export type JobState = (typeof JOB_STATUSES)[number];

export interface JobStatus {
  id: string;
  kind: string;
  status: JobState;
  /** 0-100 */
  progress: number;
  message: string;
  result?: unknown;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
}

/** 202 response of POST /api/projects/{id}/run */
export interface RunResponse {
  id: string;
  current_stage: StageName;
  status: StageStatus;
  /** false when nothing could start (waiting for a review, already running, finished) */
  started: boolean;
  message: string;
}

/** Response of POST /api/projects/{id}/open-folder */
export interface OpenFolderResponse {
  ok: boolean;
  folder: string;
}

// Keeps VideoFormat referenced so the import stays meaningful for future fields.
export type ChannelFormat = VideoFormat;

// ---------------------------------------------------------------------------
// Added in M1/M2: stage actions, WebSocket events and small helpers.
// ---------------------------------------------------------------------------

/** Body of the approve / redo / skip stage calls. */
export interface StageActionBody {
  by: string;
  notes?: string;
  edits?: Record<string, unknown>;
}

/** Everything the research page can start production from. */
export type ProjectSourceInput =
  | { kind: "ai_pick" }
  | { kind: "manual_pick"; video_id: string }
  | { kind: "own_topic"; topic_text: string };

/**
 * Events broadcast on /api/ws (pipeline/events.py). Every status change and progress
 * message of a project arrives here; the pages use them to refresh without polling.
 */
export interface ProjectUpdateEvent {
  type: "project.update";
  project_id: string;
  channel_slug?: string;
  title?: string;
  current_stage?: StageName;
  /** status of the current stage */
  status?: StageStatus | null;
  updated_at?: string;
  /** status of every stage */
  stages?: Partial<Record<StageName, StageStatus>>;
  /** the list row for this project (not the full Project) */
  project?: ProjectSummary | null;
  ts?: string | null;
}

export interface StageProgressEvent {
  type: "stage.progress";
  project_id: string;
  stage: StageName;
  message: string;
  /** 0-100, or null when the stage cannot estimate */
  pct?: number | null;
  ts?: string | null;
}

export interface JobLogEvent {
  type: "job.log";
  job_id?: string | null;
  kind?: string | null;
  status?: JobState | string | null;
  /** 0-100 */
  progress?: number | null;
  message: string;
  error?: string | null;
  project_id?: string | null;
  ts?: string | null;
}

export type ProjectEvent = ProjectUpdateEvent | StageProgressEvent | JobLogEvent;

export const PROJECT_EVENT_TYPES = ["project.update", "stage.progress", "job.log"] as const;

export function isProjectEvent(value: unknown): value is ProjectEvent {
  if (typeof value !== "object" || value === null) return false;
  const type = (value as { type?: unknown }).type;
  return typeof type === "string" && (PROJECT_EVENT_TYPES as readonly string[]).includes(type);
}

/** Statuses where the pipeline stopped and a person has to do something. */
export const WAITING_STATUSES: readonly StageStatus[] = ["awaiting_review", "awaiting_manual", "failed"];

/** The project reached the end: the export stage is done. */
export function isProjectFinished(summary: { current_stage: StageName; status: StageStatus }): boolean {
  return summary.current_stage === "export" && (summary.status === "done" || summary.status === "approved");
}

/** Status of the current stage, which is what the list and the queue show. */
export function projectStatus(project: Project): StageStatus {
  return project.stages[project.current_stage]?.status ?? "pending";
}

export function totalCost(costs: ProjectCosts): number {
  return Math.round((costs.llm_usd + costs.voice_usd + costs.images_usd) * 10_000) / 10_000;
}

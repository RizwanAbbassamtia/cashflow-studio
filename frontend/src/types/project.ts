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
  notes: string[];
  summary: string;
  gate_results: GateResult[];
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

export interface JobStatus {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "failed";
  progress: number;
  message: string;
  result?: unknown;
  error?: string | null;
}

// Keeps VideoFormat referenced so the import stays meaningful for future fields.
export type ChannelFormat = VideoFormat;

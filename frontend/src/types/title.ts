/**
 * Mirror of backend/cashcow_studio/models/title.py (docs/M1-M2-CONTRACT.md section 4):
 * the title stage writes 02_title/title.json with seven variants and a recommendation.
 */
import type { GateResult, SourceKind } from "./project";

export const TITLE_MAX_LENGTH = 70;
/** difflib ratio above which a variant is too close to the source or a past title */
export const TITLE_SIMILARITY_LIMIT = 0.8;

export interface TitleVariant {
  index: number;
  title: string;
  /** the title pattern used, e.g. "How X did Y without Z" */
  formula: string;
  emotional_trigger: string;
  curiosity_trigger: string;
  /** what the viewer does not know yet */
  hidden_gap: string;
  /** 1 (weak) to 10 (certain hit) */
  viral_score: number;
  why_it_outperforms: string;
  keywords_kept: string[];
  /** 0-1, difflib ratio against the competitor's title */
  similarity_to_source: number;
  /** 0-1, highest ratio against the channel's recent titles */
  similarity_to_history: number;
  length: number;
  /** plain-English reasons a gate failed; a flagged variant cannot be recommended */
  flags: string[];
}

/** Where the title stage started from: the competitor video, or the typed topic. */
export interface TitleSource {
  kind: SourceKind;
  /** the competitor title, or the typed topic */
  title: string;
  video_id?: string | null;
  url?: string | null;
  channel_name?: string | null;
  views?: number | null;
  outlier_score?: number | null;
}

/** 02_title/title.json */
export interface TitleDoc {
  variants: TitleVariant[];
  /** null only when every option failed a gate (the stage then fails) */
  recommended_index: number | null;
  source_title: string;
  source: TitleSource;
  generated_at: string;
  model: string;
  language: string;
  format: string;
  framework_source: "file" | "default";
  framework_name: string | null;
  history_compared: number;
  chosen_index: number | null;
  chosen_title: string | null;
  gate_results: GateResult[];
  notes: string[];
  schema_version: number;
}

/**
 * GET /api/projects/{id}/stage/title. The engine returns `{}` when the stage has not run,
 * so every field is optional on the wire.
 */
export interface TitleReviewPayload {
  stage?: "title";
  title?: TitleDoc;
  recent_titles?: string[];
  rules?: Record<string, unknown>;
}

/** Approve edits for the title stage: pick a variant, or type the final title. */
export interface TitleApproveEdits {
  chosen_index?: number;
  title_text?: string;
}

/**
 * Mirror of backend/cashflow_studio/models/research.py (docs/M1-M2-CONTRACT.md section 2).
 *
 * A Candidate is one competitor video after the outlier maths. The research stage scans all
 * competitors of a channel, ranks their videos together and the AI pick is one of them.
 */
import type { ProjectFormat } from "./project";

export const OUTLIER_LABELS = ["one-of-ten", "strong", "notable", "normal"] as const;
export type OutlierLabel = (typeof OUTLIER_LABELS)[number];

export const SCAN_TABS = ["videos", "shorts"] as const;
export type ScanTab = (typeof SCAN_TABS)[number];

export interface Candidate {
  video_id: string;
  url: string;
  title: string;
  channel_name: string;
  channel_url: string;
  channel_id: string | null;
  format: ProjectFormat;
  views: number;
  /** true once the exact count was fetched; the flat listing rounds views */
  views_exact?: boolean | null;
  published_at?: string | null;
  age_days: number;
  duration_s?: number | null;
  /** median views of the neighbouring videos on the same channel */
  baseline_views: number;
  /** views / baseline_views */
  outlier_score: number;
  /** views per day */
  vpd: number;
  vpd_ratio: number;
  /** views / channel followers */
  sub_ratio: number;
  label: OutlierLabel;
  thumbnail_url: string | null;
  /** this channel already made a video from it (title_history) */
  used_before: boolean;
  excluded_reason?: string | null;
  /** 1 = best, across all competitors of the channel */
  rank: number;
}

/** One competitor as reported by the last scan. */
export interface ScannedChannel {
  name: string;
  url: string;
  id: string | null;
  videos_found: number | null;
  last_scanned: string | null;
  error?: string | null;
}

/** Response of GET /api/channels/{slug}/research/candidates */
export interface CandidatesResponse {
  scanned_at: string | null;
  format: ProjectFormat;
  channels: ScannedChannel[];
  candidates: Candidate[];
  /** the video the AI would start from (same picker as the research stage); null when nothing is eligible */
  pick?: Candidate | null;
  pick_video_id?: string | null;
}

/** `result` of a finished research.scan job (GET /api/jobs/{id}) */
export interface ScanJobResult {
  channel_slug: string;
  scanned_at: string;
  channels: ScannedChannel[];
  videos_found: number;
  candidates_long: number;
  candidates_shorts: number;
}

/** Body of POST /api/channels/{slug}/research/scan */
export interface ScanRequest {
  tabs?: ScanTab[];
  max_videos_per_channel?: number;
  force?: boolean;
}

/** 202 response of the scan endpoint */
export interface ScanStarted {
  job_id: string;
}

/**
 * GET /api/projects/{id}/stage/research: the top candidates with the pick highlighted.
 * The engine returns `{}` when the stage has not run, so every field is optional on the wire.
 */
export interface ResearchReviewPayload {
  stage?: "research";
  source_kind?: "ai_pick" | "manual_pick" | "own_topic";
  topic_text?: string | null;
  format?: ProjectFormat;
  scanned_at?: string | null;
  channels?: ScannedChannel[];
  candidates?: Candidate[];
  pick?: Candidate | null;
  pick_video_id?: string | null;
  transcript_available?: boolean;
  thumbnail_file?: string | null;
  notes?: string[];
}

/** Approve edits for the research stage: change the pick before the title stage runs. */
export interface ResearchApproveEdits {
  video_id: string;
}

/** A candidate the picker may choose: not used before and not excluded. */
export function isPickable(candidate: Candidate): boolean {
  return !candidate.used_before && !candidate.excluded_reason;
}

/**
 * The AI pick's video id as the server computed it (`pick`, `pick_video_id`). The server
 * runs the real picker (language and freshness preferences, not plain rank), so the page
 * never guesses: without a server pick there is no highlight.
 */
export function findPickVideoId(
  payload: { candidates?: Candidate[]; pick?: Candidate | null; pick_video_id?: string | null } | undefined,
): string | null {
  if (!payload) return null;
  if (payload.pick?.video_id) return payload.pick.video_id;
  if (payload.pick_video_id) return payload.pick_video_id;
  return null;
}

export const OUTLIER_LABEL_TEXT: Record<OutlierLabel, string> = {
  "one-of-ten": "One in ten",
  strong: "Strong",
  notable: "Notable",
  normal: "Normal",
};

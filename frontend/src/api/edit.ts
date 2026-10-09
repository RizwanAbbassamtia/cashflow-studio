/**
 * Edit stage calls for the Edit review panel (docs/M3-M4-CONTRACT.md section 3).
 *
 * The panel reads the stage payload through `useStagePayload` (api/projects.ts), parses it
 * with `parseEditPayload` and plays media through `projectFileUrl` (api/files.ts). Rendering
 * more presets is a redo with `edits` (the engine hands them to the stage as `ctx.edits`);
 * approving sends the same edits and moves the project on to the export stage.
 */
import { useMutation } from "@tanstack/react-query";
import { useMemo } from "react";

import type { Project } from "../types/project";
import { parseEditPayload, type EditApproveEdits, type EditReviewPayload, renderPresetLabel } from "../types/timeline";
import { projectFileUrl } from "./files";
import { approveStage, redoStage, useProjectCacheUpdate, useStagePayload } from "./projects";
import { useStageProgress } from "./ws";

// ---- files inside the project folder ---------------------------------------------------

export const PROXY_FILE = "07_edit/proxy.mp4";
export const RENDER_LOG_FILE = "07_edit/render.log";

/** `07_edit/final_1080p.mp4` */
export function finalFile(preset: string): string {
  return `07_edit/final_${preset}.mp4`;
}

/** `06_images/scene_01.png` for scene index 0 */
export function sceneImageFile(index: number): string {
  return `06_images/scene_${String(index + 1).padStart(2, "0")}.png`;
}

const STAGE_DIR_PATTERN = /(?:^|[\\/])(0[1-8]_[a-z]+(?:[\\/].*)?)$/i;

/**
 * The path of a project file relative to the project folder, with forward slashes:
 * "C:\\...\\project\\06_images\\scene_01.png" -> "06_images/scene_01.png". A relative path
 * passes through; an absolute path outside any stage folder gives null.
 */
export function projectRelativePath(path: string): string | null {
  const trimmed = path.trim();
  if (!trimmed) return null;
  const match = STAGE_DIR_PATTERN.exec(trimmed);
  if (match) return match[1]!.split(/[\\/]+/).filter(Boolean).join("/");
  const absolute = /^[a-z]:[\\/]/i.test(trimmed) || trimmed.startsWith("\\\\") || trimmed.startsWith("/");
  if (absolute) return null;
  return trimmed.split(/[\\/]+/).filter(Boolean).join("/");
}

/**
 * Where the browser loads a project file from: the backend's own URL when it sent one,
 * else the file-serving endpoint for the path; null when the path cannot be served.
 */
export function projectMediaUrl(projectId: string, path: string | null | undefined, url?: string | null): string | null {
  if (url) return url;
  if (!path) return null;
  if (/^(https?:)?\/\//i.test(path) || path.startsWith("data:") || path.startsWith("/api/")) return path;
  const relative = projectRelativePath(path);
  return relative ? projectFileUrl(projectId, relative) : null;
}

// ---- payload --------------------------------------------------------------------------

/** The parsed edit payload; null until the stage has written something. */
export function useEditPayload(projectId: string | undefined, enabled = true): { query: ReturnType<typeof useStagePayload<unknown>>; payload: EditReviewPayload | null } {
  const query = useStagePayload<unknown>(projectId, "edit", enabled);
  const payload = useMemo(() => parseEditPayload(query.data), [query.data]);
  return { query, payload };
}

// ---- live render progress --------------------------------------------------------------

export interface RenderProgress {
  /** the preset named in the progress message, when it names one */
  preset: string | null;
  /** 0-100 or null when the stage cannot estimate */
  pct: number | null;
  message: string;
}

const PRESET_PATTERN = /render(?:ing)?\s+(?:the\s+)?([0-9]{3,4}p|proxy|4k)\b/i;
const PERCENT_PATTERN = /(\d{1,3}(?:\.\d+)?)\s*%/;

/** "Rendering 1080p 45%" -> { preset: "1080p", pct: 45 }. Unknown messages give nulls. */
export function parseRenderProgress(message: string, pct?: number | null): { preset: string | null; pct: number | null } {
  const presetMatch = PRESET_PATTERN.exec(message);
  const preset = presetMatch?.[1] ? (presetMatch[1].toLowerCase() === "4k" ? "2160p" : presetMatch[1].toLowerCase()) : null;
  const percentMatch = PERCENT_PATTERN.exec(message);
  const fromMessage = percentMatch?.[1] ? Math.round(Number(percentMatch[1])) : null;
  const value = typeof pct === "number" && Number.isFinite(pct) ? pct : fromMessage;
  return { preset, pct: value !== null ? Math.max(0, Math.min(100, value)) : null };
}

/** The latest progress line of the edit stage while it runs (renders report "Rendering <preset> <pct>%"). */
export function useRenderProgress(projectId: string | undefined): RenderProgress | null {
  const event = useStageProgress(projectId);
  return useMemo(() => {
    if (!event || event.stage !== "edit") return null;
    const parsed = parseRenderProgress(event.message, event.pct);
    return { preset: parsed.preset, pct: parsed.pct, message: event.message };
  }, [event]);
}

// ---- mutations ------------------------------------------------------------------------

export interface RenderRequest {
  by: string;
  /** the presets to render now */
  presets: string[];
  /** the other choices to render with (music, captions, popups) */
  edits?: Omit<EditApproveEdits, "presets">;
}

/** Plain-English note the redo writes into the project log. */
export function renderNote(presets: string[]): string {
  const names = presets.map(renderPresetLabel);
  return names.length > 0 ? `Render ${names.join(", ")}` : "Render again";
}

/** Runs the edit stage again to render the presets (a redo with `edits.presets`). */
export function useRenderPresets(projectId: string) {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: ({ by, presets, edits }: RenderRequest): Promise<Project> =>
      redoStage(projectId, "edit", { by, notes: renderNote(presets), edits: { ...(edits ?? {}), presets } }),
    onSuccess: update,
  });
}

export interface EditApproveBody {
  by: string;
  notes?: string;
  edits?: EditApproveEdits;
}

/** Approves the edit stage with the chosen presets, music and toggles; export is next. */
export function useApproveEdit(projectId: string) {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: (body: EditApproveBody): Promise<Project> =>
      approveStage(projectId, "edit", body as { by: string; notes?: string; edits?: Record<string, unknown> }),
    onSuccess: update,
  });
}

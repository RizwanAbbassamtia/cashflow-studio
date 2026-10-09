/**
 * Export stage calls for the Export review panel (docs/M3-M4-CONTRACT.md section 4).
 *
 * The panel reads the stage payload through `useStagePayload` (api/projects.ts) and parses
 * it with `parseExportPayload`. Approving is the export: the edits (metadata, thumbnail
 * choice, headline, disclosure) travel with the approval and the files are copied to the
 * export folder.
 */
import { useMutation } from "@tanstack/react-query";
import { useMemo } from "react";

import { parseExportPayload, type ExportApproveEdits, type ExportReviewPayload, type ThumbnailVariant } from "../types/export";
import type { OpenFolderResponse, Project } from "../types/project";
import { api } from "./client";
import { projectMediaUrl } from "./edit";
import { approveStage, redoStage, useProjectCacheUpdate, useStagePayload } from "./projects";

export { projectMediaUrl } from "./edit";

/** The parsed export payload; null until the stage has written something. */
export function useExportPayload(projectId: string | undefined, enabled = true): { query: ReturnType<typeof useStagePayload<unknown>>; payload: ExportReviewPayload | null } {
  const query = useStagePayload<unknown>(projectId, "export", enabled);
  const payload = useMemo(() => parseExportPayload(query.data), [query.data]);
  return { query, payload };
}

/** Where the browser loads a thumbnail variant from (the 16:9 picture, or the Shorts one). */
export function thumbnailUrl(projectId: string, variant: ThumbnailVariant, shorts = false): string | null {
  return shorts ? projectMediaUrl(projectId, variant.shorts_path, variant.shorts_url) : projectMediaUrl(projectId, variant.path, variant.url);
}

export interface ExportApproveBody {
  by: string;
  notes?: string;
  edits?: ExportApproveEdits;
}

/** Approve = export: the files are copied to the export folder with the edited metadata. */
export function useApproveExport(projectId: string) {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: (body: ExportApproveBody): Promise<Project> =>
      approveStage(projectId, "export", body as { by: string; notes?: string; edits?: Record<string, unknown> }),
    onSuccess: update,
  });
}

export interface ExportRedoBody {
  by: string;
  notes: string;
  /** kept for the next run (for example a new headline or thumbnail choice) */
  edits?: ExportApproveEdits;
}

/** Runs the export stage again (new thumbnails and SEO pack) with the reviewer's notes. */
export function useRedoExport(projectId: string) {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: (body: ExportRedoBody): Promise<Project> =>
      redoStage(projectId, "export", { by: body.by, notes: body.notes, edits: body.edits as Record<string, unknown> | undefined }),
    onSuccess: update,
  });
}

/**
 * Asks the backend to open the export folder in Windows Explorer. Uses the project's
 * open-folder endpoint; the body names the export folder so a backend that supports it
 * opens that one, and an older one opens the project folder.
 */
export function openExportFolder(projectId: string, folder?: string | null): Promise<OpenFolderResponse> {
  return api.post<OpenFolderResponse>(`/api/projects/${encodeURIComponent(projectId)}/open-folder`, {
    target: "export",
    folder: folder ?? null,
  });
}

export function useOpenExportFolder() {
  return useMutation({ mutationFn: ({ projectId, folder }: { projectId: string; folder?: string | null }) => openExportFolder(projectId, folder) });
}

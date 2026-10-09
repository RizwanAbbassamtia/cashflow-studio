// Images stage calls for the ImagesReview panel (docs/M3-M4-CONTRACT.md sections 2 and 6).
// Builds on the generic stage calls in ./projects and the file helpers in ./files.
import { useMutation } from "@tanstack/react-query";
import { useMemo } from "react";

import { parseImagesPayload, type ImagesApproveEdits, type ImagesReviewPayload } from "../types/images";
import type { StageActionBody } from "../types/project";
import { projectFileUrl, uploadProjectFile, type UploadResult } from "./files";
import { approveStage, redoStage, useProjectCacheUpdate, useStagePayload } from "./projects";

export interface ImagesApproveBody {
  by: string;
  notes?: string;
  edits?: ImagesApproveEdits;
}

export interface ImagesRedoBody {
  by: string;
  notes: string;
  /** kept for the next run: named scenes are regenerated even when their picture was accepted */
  edits?: ImagesApproveEdits;
}

function asActionBody(body: ImagesApproveBody | ImagesRedoBody): StageActionBody {
  const out: StageActionBody = { by: body.by };
  if (body.notes) out.notes = body.notes;
  if (body.edits) out.edits = body.edits as Record<string, unknown>;
  return out;
}

export function approveImages(projectId: string, body: ImagesApproveBody) {
  return approveStage(projectId, "images", asActionBody(body));
}

export function redoImages(projectId: string, body: ImagesRedoBody) {
  return redoStage(projectId, "images", { by: body.by, notes: body.notes, ...(body.edits ? { edits: body.edits as Record<string, unknown> } : {}) });
}

/** Uploads a person's own picture for one scene into `06_images/`; the path goes into `edits.uploads`. */
export function uploadSceneImage(projectId: string, scene: number, file: File): Promise<UploadResult> {
  return uploadProjectFile(projectId, "images", file, scene);
}

// ---- hooks ----------------------------------------------------------------

/** The images review payload, parsed; `payload` is null until the stage has written one. */
export function useImagesPayload(projectId: string | undefined) {
  const query = useStagePayload<unknown>(projectId, "images");
  const payload = useMemo<ImagesReviewPayload | null>(
    () => (projectId ? parseImagesPayload(query.data, (path) => projectFileUrl(projectId, path)) : null),
    [projectId, query.data],
  );
  return { query, payload };
}

export function useApproveImages(projectId: string) {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: (body: ImagesApproveBody) => approveImages(projectId, body),
    onSuccess: update,
  });
}

export function useRedoImages(projectId: string) {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: (body: ImagesRedoBody) => redoImages(projectId, body),
    onSuccess: update,
  });
}

export function useUploadSceneImage(projectId: string) {
  return useMutation({
    mutationFn: ({ scene, file }: { scene: number; file: File }) => uploadSceneImage(projectId, scene, file),
  });
}

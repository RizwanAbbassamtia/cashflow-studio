// Voice stage calls for the VoiceReview panel (docs/M3-M4-CONTRACT.md sections 1 and 6).
// Builds on the generic stage calls in ./projects and the file helpers in ./files.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import type { StageActionBody } from "../types/project";
import { parseTimingDoc, parseVoicePayload, withWordTimings, type TimingDoc, type VoiceApproveEdits, type VoiceReviewPayload } from "../types/timing";
import { api } from "./client";
import { projectFileUrl, uploadProjectFile, type UploadResult } from "./files";
import { queryKeys } from "./keys";
import { approveStage, redoStage, useProjectCacheUpdate, useStagePayload } from "./projects";

export interface VoiceApproveBody {
  by: string;
  notes?: string;
  edits?: VoiceApproveEdits;
}

export interface VoiceRedoBody {
  by: string;
  notes: string;
  /** kept for the next run: `re_record` or `audio_path` reach the stage as `ctx.edits` */
  edits?: VoiceApproveEdits;
}

function asActionBody(body: VoiceApproveBody | VoiceRedoBody): StageActionBody {
  const out: StageActionBody = { by: body.by };
  if (body.notes) out.notes = body.notes;
  if (body.edits) out.edits = body.edits as Record<string, unknown>;
  return out;
}

export function approveVoice(projectId: string, body: VoiceApproveBody) {
  return approveStage(projectId, "voice", asActionBody(body));
}

export function redoVoice(projectId: string, body: VoiceRedoBody) {
  return redoStage(projectId, "voice", { by: body.by, notes: body.notes, ...(body.edits ? { edits: body.edits as Record<string, unknown> } : {}) });
}

/** Uploads a person's own narration into `05_voice/`; the result path goes into `edits.audio_path`. */
export function uploadRecording(projectId: string, file: File): Promise<UploadResult> {
  return uploadProjectFile(projectId, "voice", file);
}

/** `05_voice/timing.json` through the project file endpoint (the word-level times). */
export function getTimingDoc(timingUrl: string): Promise<TimingDoc | null> {
  return api.get<unknown>(timingUrl).then(parseTimingDoc);
}

// ---- hooks ----------------------------------------------------------------

/**
 * The voice review payload, parsed, with the word times merged in from timing.json once
 * that file has loaded. `payload` is null until the stage has written one.
 */
export function useVoicePayload(projectId: string | undefined) {
  const query = useStagePayload<unknown>(projectId, "voice");
  const base = useMemo<VoiceReviewPayload | null>(
    () => (projectId ? parseVoicePayload(query.data, (path) => projectFileUrl(projectId, path)) : null),
    [projectId, query.data],
  );
  const timingUrl = base?.timing_url ?? "";
  const timing = useQuery({
    // Under the stage payload key, so a project refresh reloads it too.
    queryKey: [...queryKeys.stagePayload(projectId ?? "", "voice"), "timing", timingUrl, query.dataUpdatedAt],
    queryFn: () => getTimingDoc(timingUrl),
    enabled: Boolean(projectId) && timingUrl !== "",
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
  const payload = useMemo<VoiceReviewPayload | null>(
    () => (base ? { ...base, sentences: withWordTimings(base.sentences, timing.data ?? null) } : null),
    [base, timing.data],
  );
  return { query, payload, timing };
}

export function useApproveVoice(projectId: string) {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: (body: VoiceApproveBody) => approveVoice(projectId, body),
    onSuccess: update,
  });
}

export function useRedoVoice(projectId: string) {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: (body: VoiceRedoBody) => redoVoice(projectId, body),
    onSuccess: update,
  });
}

export function useUploadRecording(projectId: string) {
  return useMutation({ mutationFn: (file: File) => uploadRecording(projectId, file) });
}

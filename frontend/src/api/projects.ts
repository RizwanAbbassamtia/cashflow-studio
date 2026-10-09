// Project and stage calls shared by every review panel. The research/projects agent extends
// this file with react-query hooks; the script/storyboard agent imports from it, never edits it.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import type { StageMode, StageName } from "../types/channel";
import type {
  JobStatus,
  OpenFolderResponse,
  Project,
  ProjectCreate,
  ProjectSummary,
  RunResponse,
  StageActionBody,
} from "../types/project";
import { api } from "./client";
import { queryKeys } from "./keys";

// ---- plain functions ------------------------------------------------------

export function listProjects(params: { channel_slug?: string; status?: string } = {}): Promise<ProjectSummary[]> {
  const query = new URLSearchParams();
  if (params.channel_slug) query.set("channel_slug", params.channel_slug);
  if (params.status) query.set("status", params.status);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return api.get<ProjectSummary[]>(`/api/projects${suffix}`);
}

export function getProject(id: string): Promise<Project> {
  return api.get<Project>(`/api/projects/${encodeURIComponent(id)}`);
}

export function createProject(body: ProjectCreate): Promise<Project> {
  return api.post<Project>("/api/projects", body);
}

/** The review payload of one stage: title variants, script, storyboard, candidates. */
export function getStagePayload<T = unknown>(id: string, stage: StageName): Promise<T> {
  return api.get<T>(`/api/projects/${encodeURIComponent(id)}/stage/${stage}`);
}

export function approveStage(
  id: string,
  stage: StageName,
  body: { by: string; notes?: string; edits?: Record<string, unknown> },
): Promise<Project> {
  return api.post<Project>(`/api/projects/${encodeURIComponent(id)}/stage/${stage}/approve`, body);
}

/** Body of a redo: notes for the prompt, plus stage edits kept for that run (for example the
 * script's `locked_paragraph_ids`, so locks toggled in the editor survive a regenerate). */
export interface RedoBody {
  by: string;
  notes: string;
  edits?: Record<string, unknown>;
}

export function redoStage(id: string, stage: StageName, body: RedoBody): Promise<Project> {
  return api.post<Project>(`/api/projects/${encodeURIComponent(id)}/stage/${stage}/redo`, body);
}

export function skipStage(id: string, stage: StageName, body: { by: string; notes: string }): Promise<Project> {
  return api.post<Project>(`/api/projects/${encodeURIComponent(id)}/stage/${stage}/skip`, body);
}

export function setStageMode(id: string, stage: StageName, mode: "auto" | "review" | "manual"): Promise<Project> {
  return api.put<Project>(`/api/projects/${encodeURIComponent(id)}/stage/${stage}/mode`, { mode });
}

/** Starts or resumes the pipeline; `started` is false when it is waiting for a review. */
export function runProject(id: string): Promise<RunResponse> {
  return api.post<RunResponse>(`/api/projects/${encodeURIComponent(id)}/run`, {});
}

export function getJob(jobId: string): Promise<JobStatus> {
  return api.get<JobStatus>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

/** Moves the project folder to <projects_dir>/_archived/; never hard-deletes. */
export function archiveProject(id: string): Promise<void> {
  return api.delete(`/api/projects/${encodeURIComponent(id)}`);
}

/** Asks the backend to open the project folder in Windows Explorer. */
export function openProjectFolder(id: string): Promise<OpenFolderResponse> {
  return api.post<OpenFolderResponse>(`/api/projects/${encodeURIComponent(id)}/open-folder`, {});
}

// ---- hooks ----------------------------------------------------------------

export interface ProjectListFilters {
  channel_slug?: string;
  status?: string;
}

export function useProjects(filters: ProjectListFilters = {}, options: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: queryKeys.projectList(filters),
    queryFn: () => listProjects(filters),
    refetchInterval: options.refetchInterval ?? false,
  });
}

function stageIsRunning(project: Project | undefined): boolean {
  if (!project) return false;
  return Object.values(project.stages).some((stage) => stage.status === "running");
}

/**
 * One project. While a stage runs it also polls every few seconds, so the page keeps moving
 * even when the WebSocket is not connected.
 */
export function useProject(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.project(id ?? ""),
    queryFn: () => getProject(id as string),
    enabled: Boolean(id),
    refetchInterval: (query) => (stageIsRunning(query.state.data) ? 4_000 : false),
  });
}

/** The review payload of a stage; only asked for once the stage has produced something. */
export function useStagePayload<T = unknown>(id: string | undefined, stage: StageName, enabled = true) {
  return useQuery({
    queryKey: queryKeys.stagePayload(id ?? "", stage),
    queryFn: () => getStagePayload<T>(id as string, stage),
    enabled: Boolean(id) && enabled,
  });
}

/** Puts a fresh Project into the cache and refreshes the lists and stage payloads. */
export function useProjectCacheUpdate() {
  const queryClient = useQueryClient();
  return (project: Project) => {
    queryClient.setQueryData(queryKeys.project(project.id), project);
    void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists });
    void queryClient.invalidateQueries({
      queryKey: queryKeys.project(project.id),
      // the detail itself was just set; only the stage payloads underneath need a refetch
      predicate: (query) => query.queryKey.length > queryKeys.project(project.id).length,
    });
  };
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createProject,
    onSuccess: (project) => {
      queryClient.setQueryData(queryKeys.project(project.id), project);
      void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists });
    },
  });
}

export function useApproveStage() {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: ({ id, stage, body }: { id: string; stage: StageName; body: StageActionBody }) =>
      approveStage(id, stage, body),
    onSuccess: update,
  });
}

export function useRedoStage() {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: ({ id, stage, body }: { id: string; stage: StageName; body: RedoBody }) => redoStage(id, stage, body),
    onSuccess: update,
  });
}

export function useSkipStage() {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: ({ id, stage, body }: { id: string; stage: StageName; body: { by: string; notes: string } }) =>
      skipStage(id, stage, body),
    onSuccess: update,
  });
}

export function useSetStageMode() {
  const update = useProjectCacheUpdate();
  return useMutation({
    mutationFn: ({ id, stage, mode }: { id: string; stage: StageName; mode: StageMode }) => setStageMode(id, stage, mode),
    onSuccess: update,
  });
}

export function useRunProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => runProject(id),
    onSuccess: (_result, id) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists });
    },
  });
}

export function useArchiveProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => archiveProject(id),
    onSuccess: (_result, id) => {
      queryClient.removeQueries({ queryKey: queryKeys.project(id) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projectLists });
    },
  });
}

export function useOpenProjectFolder() {
  return useMutation({ mutationFn: (id: string) => openProjectFolder(id) });
}

export function jobIsActive(job: JobStatus | undefined): boolean {
  return job?.status === "queued" || job?.status === "running";
}

/**
 * Polls GET /api/jobs/{id} every second while the job is queued or running, then stops.
 * `onDone` and `onFailed` fire once per job when it reaches that state.
 */
export function useJob(
  jobId: string | null | undefined,
  callbacks: { onDone?: (job: JobStatus) => void; onFailed?: (job: JobStatus) => void } = {},
) {
  const query = useQuery({
    queryKey: queryKeys.job(jobId ?? ""),
    queryFn: () => getJob(jobId as string),
    enabled: Boolean(jobId),
    staleTime: 0,
    refetchInterval: (q) => (jobIsActive(q.state.data) || q.state.data === undefined ? 1_000 : false),
  });

  const handled = useRef<string | null>(null);
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  useEffect(() => {
    const job = query.data;
    if (!job || jobIsActive(job)) return;
    const marker = `${job.id}:${job.status}`;
    if (handled.current === marker) return;
    handled.current = marker;
    if (job.status === "done") callbacksRef.current.onDone?.(job);
    else if (job.status === "failed") callbacksRef.current.onFailed?.(job);
  }, [query.data]);

  return query;
}

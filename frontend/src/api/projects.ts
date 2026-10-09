// Project and stage calls shared by every review panel. The research/projects agent extends
// this file with react-query hooks; the script/storyboard agent imports from it, never edits it.
import type { StageName } from "../types/channel";
import type { JobStatus, Project, ProjectCreate, ProjectSummary } from "../types/project";
import { api } from "./client";

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

export function redoStage(id: string, stage: StageName, body: { by: string; notes: string }): Promise<Project> {
  return api.post<Project>(`/api/projects/${encodeURIComponent(id)}/stage/${stage}/redo`, body);
}

export function skipStage(id: string, stage: StageName, body: { by: string; notes: string }): Promise<Project> {
  return api.post<Project>(`/api/projects/${encodeURIComponent(id)}/stage/${stage}/skip`, body);
}

export function setStageMode(id: string, stage: StageName, mode: "auto" | "review" | "manual"): Promise<Project> {
  return api.put<Project>(`/api/projects/${encodeURIComponent(id)}/stage/${stage}/mode`, { mode });
}

export function runProject(id: string): Promise<void> {
  return api.post<void>(`/api/projects/${encodeURIComponent(id)}/run`, {});
}

export function getJob(jobId: string): Promise<JobStatus> {
  return api.get<JobStatus>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

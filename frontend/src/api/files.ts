// Project file access shared by the voice, images, edit and export review panels.
// Contract: docs/M3-M4-CONTRACT.md section 5. Keep these names; both front-end agents import them.
import { api } from "./client";

/** URL of a file inside the project folder, for <audio>, <video> and <img> elements. */
export function projectFileUrl(projectId: string, relativePath: string): string {
  const safe = relativePath
    .split(/[\\/]+/)
    .filter(Boolean)
    .map(encodeURIComponent)
    .join("/");
  return `/api/projects/${encodeURIComponent(projectId)}/files/${safe}`;
}

export interface UploadResult {
  path: string;
}

/** Uploads a file into a stage folder of the project; returns the saved relative path. */
export async function uploadProjectFile(
  projectId: string,
  stage: "voice" | "images" | "edit" | "export",
  file: File,
  scene?: number,
): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  if (scene !== undefined) form.append("scene", String(scene));
  return api.postForm<UploadResult>(`/api/projects/${encodeURIComponent(projectId)}/upload/${stage}`, form);
}

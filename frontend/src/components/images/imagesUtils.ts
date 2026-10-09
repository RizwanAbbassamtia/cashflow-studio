import type { ImagesApproveEdits } from "../../types/images";

/** What the per-scene file picker accepts; the stage keeps the file as the scene's picture. */
export const IMAGE_FILE_ACCEPT = "image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp";

/** A picture uploaded for a scene, waiting for the approval (or the next run) to take it. */
export interface PendingUpload {
  scene: number;
  /** relative path returned by the upload endpoint */
  path: string;
  name: string;
  /** object URL of the local file, for the preview; revoked when replaced or discarded */
  previewUrl: string;
}

export function sceneLabel(scene: number): string {
  return `Scene ${scene + 1}`;
}

/** "scene 2", "scenes 1 and 3", "scenes 1, 4 and 9" from 0-based scene indexes. */
export function sceneNumbers(scenes: Iterable<number>): string {
  const numbers = [...new Set(scenes)].sort((a, b) => a - b).map((scene) => scene + 1);
  if (numbers.length === 0) return "no scenes";
  if (numbers.length === 1) return `scene ${numbers[0]}`;
  return `scenes ${numbers.slice(0, -1).join(", ")} and ${numbers[numbers.length - 1]}`;
}

export interface EditsInput {
  regenerate: ReadonlyMap<number, string>;
  uploads: ReadonlyMap<number, PendingUpload>;
  /** every scene that is locked after the reviewer's toggles */
  lockedScenes: number[];
  /** true when at least one lock differs from what the stage saved */
  locksChanged: boolean;
  overrideGates: boolean;
}

/** The `edits` body for approve or redo; undefined when there is nothing to send. */
export function buildImagesEdits({ regenerate, uploads, lockedScenes, locksChanged, overrideGates }: EditsInput): ImagesApproveEdits | undefined {
  const edits: ImagesApproveEdits = {};
  if (regenerate.size > 0) {
    edits.regenerate = [...regenerate.entries()].sort((a, b) => a[0] - b[0]).map(([scene, note]) => ({ scene, note }));
  }
  if (uploads.size > 0) {
    edits.uploads = [...uploads.values()].sort((a, b) => a.scene - b.scene).map(({ scene, path }) => ({ scene, path }));
  }
  if (locksChanged || (lockedScenes.length > 0 && (regenerate.size > 0 || uploads.size > 0))) {
    edits.lock = [...lockedScenes].sort((a, b) => a - b);
  }
  if (overrideGates) edits.override_gates = true;
  return Object.keys(edits).length > 0 ? edits : undefined;
}

/** One plain-English line for the project log that says what the changes are. */
export function describeChanges(regenerate: ReadonlyMap<number, string>, uploads: ReadonlyMap<number, PendingUpload>, locksChanged: boolean): string {
  const parts: string[] = [];
  if (regenerate.size > 0) {
    const notes = [...regenerate.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([scene, note]) => `${sceneLabel(scene)}${note ? `: ${note}` : ""}`)
      .join("; ");
    parts.push(`Regenerate ${sceneNumbers(regenerate.keys())} (${notes})`);
  }
  if (uploads.size > 0) parts.push(`Replace ${sceneNumbers(uploads.keys())} with uploaded pictures`);
  if (locksChanged) parts.push("Locks changed");
  return parts.join(". ") + (parts.length > 0 ? "." : "");
}

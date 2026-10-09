/** Small pure helpers for the Edit review panel. */
import type { BadgeTone } from "../ui/Badge";
import type { EditSceneView, RenderStatus } from "../../types/timeline";

/** "0:00", "1:05" or "1:02:03" */
export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

/** "12.3 s" or "1:05" */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return "-";
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return formatClock(seconds);
}

/** "12.4 MB" */
export function formatBytes(bytes: number | null | undefined): string {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(0)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

/** The scene playing at `time`, or the last one once the voice has ended; -1 for none. */
export function sceneIndexAt(scenes: ReadonlyArray<Pick<EditSceneView, "start_s" | "end_s">>, time: number): number {
  if (scenes.length === 0) return -1;
  for (let i = 0; i < scenes.length; i += 1) {
    const scene = scenes[i]!;
    if (time >= scene.start_s && time < scene.end_s) return i;
  }
  const last = scenes[scenes.length - 1]!;
  return time >= last.end_s ? scenes.length - 1 : 0;
}

export const RENDER_STATUS_META: Record<RenderStatus, { label: string; tone: BadgeTone }> = {
  not_rendered: { label: "Not rendered", tone: "neutral" },
  queued: { label: "Queued", tone: "info" },
  rendering: { label: "Rendering", tone: "info" },
  done: { label: "Rendered", tone: "ok" },
  failed: { label: "Failed", tone: "fail" },
};

/** "Cross fade" from "fade", "Wipe left" from "wipeleft" and so on, for the summary badges. */
const TRANSITION_WORDS: Record<string, string> = {
  fade: "Cross fade",
  fadeblack: "Fade through black",
  fadewhite: "Fade through white",
  fadegrays: "Fade through grey",
  dissolve: "Dissolve",
  distance: "Distance blur",
  pixelize: "Pixelate",
  radial: "Radial sweep",
  circleopen: "Circle open",
  circleclose: "Circle close",
  zoomin: "Zoom in",
  hlslice: "Horizontal slices",
  vuslice: "Vertical slices",
  diagtl: "Diagonal (top left)",
  diagbr: "Diagonal (bottom right)",
};

export function transitionLabel(type: string): string {
  if (TRANSITION_WORDS[type]) return TRANSITION_WORDS[type]!;
  const match = /^(wipe|slide|smooth)(left|right|up|down)$/.exec(type);
  if (match) return `${match[1] === "smooth" ? "Smooth wipe" : match[1]![0]!.toUpperCase() + match[1]!.slice(1)} ${match[2]}`;
  return type;
}

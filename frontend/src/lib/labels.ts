/** Plain-English labels for the enum values the user sees in selects, badges and tables. */
import type { ChannelStatus, FrameworkType, VideoFormat } from "../types";

export const FORMAT_LABELS: Record<VideoFormat, string> = {
  long: "Long videos",
  shorts: "Shorts",
  both: "Both",
};

export const STATUS_LABELS: Record<ChannelStatus, string> = {
  setup: "Setting up",
  active: "Active",
  paused: "Paused",
  archived: "Archived",
};

export const FRAMEWORK_TYPE_LABELS: Record<FrameworkType, string> = {
  title: "Titles",
  script_long: "Script (long videos)",
  script_shorts: "Script (Shorts)",
  scene_prompt: "Scene prompts",
  thumbnail: "Thumbnails",
  seo: "SEO (titles, tags, description)",
  style_guide: "Style guide",
  other: "Other",
};

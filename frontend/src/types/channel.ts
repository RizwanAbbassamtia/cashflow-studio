/**
 * Mirror of backend/cashcow_studio/models/channel.py (Pydantic v2).
 *
 * The Python module is the source of truth. Field names, enums and defaults here must stay
 * identical to it. Dates are ISO 8601 strings on the wire; URLs are plain strings.
 */

export const LANGUAGES = [
  "English",
  "Spanish",
  "Hindi",
  "Arabic",
  "Portuguese",
  "Indonesian",
  "Japanese",
  "German",
  "French",
  "Russian",
  "Vietnamese",
  "Turkish",
  "Korean",
  "Urdu",
  "Italian",
  "Other",
] as const;
export type Language = (typeof LANGUAGES)[number];

export const VIDEO_FORMATS = ["long", "shorts", "both"] as const;
export type VideoFormat = (typeof VIDEO_FORMATS)[number];

export const CHANNEL_STATUSES = ["setup", "active", "paused", "archived"] as const;
export type ChannelStatus = (typeof CHANNEL_STATUSES)[number];

export const STAGE_MODES = ["auto", "review", "manual"] as const;
export type StageMode = (typeof STAGE_MODES)[number];

/** 1 main competitor, 2 secondary, 3 watch only */
export const PRIORITIES = [1, 2, 3] as const;
export type Priority = (typeof PRIORITIES)[number];

export const FRAMEWORK_TYPES = [
  "title",
  "script_long",
  "script_shorts",
  "scene_prompt",
  "thumbnail",
  "seo",
  "style_guide",
  "other",
] as const;
export type FrameworkType = (typeof FRAMEWORK_TYPES)[number];

export const VOICE_TOOLS = [
  "minimax",
  "cartesia",
  "inworld",
  "fish_audio",
  "azure",
  "google",
  "local",
  "other",
] as const;
export type VoiceTool = (typeof VOICE_TOOLS)[number];

export const IMAGE_TOOLS = [
  "google_gemini",
  "openai",
  "flux_bfl",
  "flux_fal",
  "flux_replicate",
  "ideogram",
  "recraft",
  "leonardo",
  "local",
  "manual",
  "other",
] as const;
export type ImageTool = (typeof IMAGE_TOOLS)[number];

export const THUMBNAIL_FACES = ["none", "face", "ai_character"] as const;
export type ThumbnailFace = (typeof THUMBNAIL_FACES)[number];

export const ON_IMAGE_TEXT_OPTIONS = ["app_popups", "none"] as const;
export type OnImageText = (typeof ON_IMAGE_TEXT_OPTIONS)[number];

export const STAGE_NAMES = [
  "research",
  "title",
  "script",
  "storyboard",
  "voice",
  "images",
  "edit",
  "export",
] as const;
export type StageName = (typeof STAGE_NAMES)[number];

export interface ChannelIdentity {
  name: string;
  url: string | null;
  /** YouTube channel id, filled by the app */
  id: string | null;
  language: Language;
  secondary_languages: Language[];
  niche: string;
  audience: string;
  formats: VideoFormat;
  long_form_minutes: number;
  shorts_seconds: number;
  videos_per_week: number;
  export_folder: string;
  music_folder: string;
  brand_colors: string[];
  caption_style: string;
  owner: string;
  browser_profile: number | null;
  status: ChannelStatus;
}

export interface Competitor {
  name: string;
  url: string;
  /** filled by the app */
  id: string | null;
  language: Language;
  priority: Priority;
  why: string;
  /** filled by the app */
  videos_found: number | null;
  /** filled by the app */
  last_scanned: string | null;
}

export interface Framework {
  type: FrameworkType;
  name: string;
  /** file path (shared folder) or link */
  path: string;
  version: string;
  formats: VideoFormat;
  notes: string;
}

/**
 * The consent record for a cloned voice (only the team's own enrolled voices may be
 * cloned). Empty for a stock voice; a consent.json next to the voice sample is the other
 * place the same record may live.
 */
export interface VoiceConsent {
  /** whose voice this is */
  owner_name: string;
  /** who recorded the consent */
  consented_by: string;
  /** ISO 8601 date-time, or null */
  consented_at: string | null;
  /** the text the owner agreed to */
  statement: string;
}

export interface VoiceConfig {
  tool: VoiceTool;
  /** clone link or voice id */
  clone_ref: string;
  name: string;
  language: Language;
  model: string;
  speed: number;
  style: string;
  sample_path: string;
  returns_word_timestamps: boolean | null;
  api_key_env: string;
  monthly_budget_characters: number | null;
  consent: VoiceConsent;
}

export interface ImageConfig {
  tool: ImageTool;
  model: string;
  style_guide: string;
  negative_rules: string;
  aspect_long: string;
  aspect_shorts: string;
  resolution: string;
  reference_folder: string;
  on_image_text: OnImageText;
  popup_style: string;
  api_key_env: string;
  monthly_budget_images: number | null;
}

export interface ThumbnailConfig {
  template_videos: string[];
  headline_font: string;
  headline_colors: string;
  max_headline_words: number;
  face: ThumbnailFace;
  must_include: string;
  must_avoid: string;
  sizes: string[];
}

export interface StageModes {
  research: StageMode;
  title: StageMode;
  script: StageMode;
  storyboard: StageMode;
  voice: StageMode;
  images: StageMode;
  edit: StageMode;
  export: StageMode;
}

/** Everything needed to start work on one channel. */
export interface Channel {
  /** folder name, derived from the name; pattern ^[a-z0-9]+(?:-[a-z0-9]+)*$ */
  slug: string;
  channel: ChannelIdentity;
  competitors: Competitor[];
  frameworks: Framework[];
  voice: VoiceConfig;
  images: ImageConfig;
  thumbnail: ThumbnailConfig;
  stage_modes: StageModes;
  reviewer: string;
  created_at: string | null;
  updated_at: string | null;
  schema_version: number;
}

/** Body for POST /api/channels: the slug may be left out so the server derives it. */
export type ChannelCreate = Omit<Channel, "slug"> & { slug?: string };

/** Row for the channel list. */
export interface ChannelSummary {
  slug: string;
  name: string;
  language: Language;
  formats: VideoFormat;
  status: ChannelStatus;
  competitors: number;
  updated_at: string | null;
}

/** Response of GET /api/channels/validate-url */
export interface UrlValidation {
  ok: boolean;
  kind: "channel" | "video" | "unknown";
  normalized: string;
}

export const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export const API_KEY_ENV_PATTERN = /^[A-Z0-9_]*$/;

// ---------------------------------------------------------------------------
// Defaults, identical to the Pydantic defaults.
// ---------------------------------------------------------------------------

export function defaultChannelIdentity(): ChannelIdentity {
  return {
    name: "",
    url: null,
    id: null,
    language: "English",
    secondary_languages: [],
    niche: "",
    audience: "",
    formats: "both",
    long_form_minutes: 10,
    shorts_seconds: 45,
    videos_per_week: 5,
    export_folder: "",
    music_folder: "",
    brand_colors: [],
    caption_style: "",
    owner: "",
    browser_profile: null,
    status: "setup",
  };
}

export function defaultCompetitor(): Competitor {
  return {
    name: "",
    url: "",
    id: null,
    language: "English",
    priority: 1,
    why: "",
    videos_found: null,
    last_scanned: null,
  };
}

export function defaultFramework(): Framework {
  return { type: "other", name: "", path: "", version: "", formats: "both", notes: "" };
}

export function defaultVoiceConsent(): VoiceConsent {
  return { owner_name: "", consented_by: "", consented_at: null, statement: "" };
}

export function defaultVoiceConfig(): VoiceConfig {
  return {
    tool: "other",
    clone_ref: "",
    name: "",
    language: "English",
    model: "",
    speed: 1.0,
    style: "",
    sample_path: "",
    returns_word_timestamps: null,
    api_key_env: "",
    monthly_budget_characters: null,
    consent: defaultVoiceConsent(),
  };
}

export function defaultImageConfig(): ImageConfig {
  return {
    tool: "google_gemini",
    model: "",
    style_guide: "",
    negative_rules: "",
    aspect_long: "16:9",
    aspect_shorts: "9:16",
    resolution: "1920x1080",
    reference_folder: "",
    on_image_text: "app_popups",
    popup_style: "",
    api_key_env: "",
    monthly_budget_images: null,
  };
}

export function defaultThumbnailConfig(): ThumbnailConfig {
  return {
    template_videos: [],
    headline_font: "",
    headline_colors: "",
    max_headline_words: 4,
    face: "none",
    must_include: "",
    must_avoid: "",
    sizes: ["1280x720", "1080x1920"],
  };
}

export function defaultStageModes(): StageModes {
  return {
    research: "review",
    title: "review",
    script: "review",
    storyboard: "review",
    voice: "auto",
    images: "review",
    edit: "review",
    export: "review",
  };
}

export function defaultChannel(): Channel {
  return {
    slug: "",
    channel: defaultChannelIdentity(),
    competitors: [],
    frameworks: [],
    voice: defaultVoiceConfig(),
    images: defaultImageConfig(),
    thumbnail: defaultThumbnailConfig(),
    stage_modes: defaultStageModes(),
    reviewer: "",
    created_at: null,
    updated_at: null,
    schema_version: 1,
  };
}

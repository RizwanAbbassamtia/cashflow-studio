/**
 * Channel Setup form: zod schema mirroring the Pydantic rules in channel.py, conversions
 * between the API shape and the form shape, and mapping of server errors onto fields.
 */
import type { FieldPath, UseFormSetError } from "react-hook-form";
import { z } from "zod";

import type { ApiError } from "../api/client";
import {
  API_KEY_ENV_PATTERN,
  CHANNEL_STATUSES,
  FRAMEWORK_TYPES,
  IMAGE_TOOLS,
  LANGUAGES,
  ON_IMAGE_TEXT_OPTIONS,
  SLUG_PATTERN,
  STAGE_MODES,
  THUMBNAIL_FACES,
  VIDEO_FORMATS,
  VOICE_TOOLS,
  type Channel,
  type ChannelCreate,
} from "../types";

export const HEX_COLOR_PATTERN = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;
const SIZE_PATTERN = /^\d{2,5}x\d{2,5}$/;

/** Same acceptance as Pydantic's HttpUrl for our purposes: http(s) with a host. */
export function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && url.hostname.length > 0;
  } catch {
    return false;
  }
}

const URL_MESSAGE = "Enter a full link that starts with http:// or https://";
const WHOLE_NUMBER = "Enter a whole number";

const requiredUrl = z.string().trim().min(1, "Enter a link").refine(isHttpUrl, URL_MESSAGE);
const optionalUrl = z.string().trim().refine((v) => v === "" || isHttpUrl(v), URL_MESSAGE);
const freeText = z.string();
const shortText = (what: string) =>
  z.string().trim().min(1, `Enter ${what}`).max(120, `Keep ${what} under 120 characters`);
const wholeNumber = (min: number, max: number) =>
  z
    .number({ required_error: WHOLE_NUMBER, invalid_type_error: WHOLE_NUMBER })
    .int(WHOLE_NUMBER)
    .min(min, `Use a number from ${min} to ${max}`)
    .max(max, `Use a number from ${min} to ${max}`);
const optionalWholeNumber = z.number({ invalid_type_error: WHOLE_NUMBER }).int(WHOLE_NUMBER).nullable();
const apiKeyEnv = z
  .string()
  .trim()
  .regex(API_KEY_ENV_PATTERN, "Use capital letters, numbers and underscores only, like FISH_AUDIO_API_KEY");

const languageEnum = z.enum(LANGUAGES);
const videoFormatEnum = z.enum(VIDEO_FORMATS);
const stageModeEnum = z.enum(STAGE_MODES);

export const channelIdentitySchema = z.object({
  name: shortText("a channel name"),
  url: optionalUrl,
  id: z.string().nullable(),
  language: languageEnum,
  secondary_languages: z.array(languageEnum),
  niche: freeText,
  audience: freeText,
  formats: videoFormatEnum,
  long_form_minutes: wholeNumber(1, 60),
  shorts_seconds: wholeNumber(10, 180),
  videos_per_week: wholeNumber(0, 100),
  export_folder: freeText,
  music_folder: freeText,
  brand_colors: z.array(z.string().trim().regex(HEX_COLOR_PATTERN, "Use a hex colour like #1F3864")),
  caption_style: freeText,
  owner: freeText,
  browser_profile: optionalWholeNumber,
  status: z.enum(CHANNEL_STATUSES),
});

export const competitorSchema = z.object({
  name: shortText("a name"),
  url: requiredUrl,
  id: z.string().nullable(),
  language: languageEnum,
  priority: z.union([z.literal(1), z.literal(2), z.literal(3)]),
  why: freeText,
  videos_found: z.number().int().nullable(),
  last_scanned: z.string().nullable(),
});

export const frameworkSchema = z.object({
  type: z.enum(FRAMEWORK_TYPES),
  name: shortText("a name"),
  path: freeText,
  version: freeText,
  formats: videoFormatEnum,
  notes: freeText,
});

export const voiceSchema = z.object({
  tool: z.enum(VOICE_TOOLS),
  clone_ref: freeText,
  name: freeText,
  language: languageEnum,
  model: freeText,
  speed: z
    .number({ required_error: "Enter a speed", invalid_type_error: "Enter a speed" })
    .min(0.5, "Speed goes from 0.5 to 2.0")
    .max(2, "Speed goes from 0.5 to 2.0"),
  style: freeText,
  sample_path: freeText,
  returns_word_timestamps: z.boolean().nullable(),
  api_key_env: apiKeyEnv,
  monthly_budget_characters: optionalWholeNumber,
});

export const imagesSchema = z.object({
  tool: z.enum(IMAGE_TOOLS),
  model: freeText,
  style_guide: freeText,
  negative_rules: freeText,
  aspect_long: freeText,
  aspect_shorts: freeText,
  resolution: freeText,
  reference_folder: freeText,
  on_image_text: z.enum(ON_IMAGE_TEXT_OPTIONS),
  popup_style: freeText,
  api_key_env: apiKeyEnv,
  monthly_budget_images: optionalWholeNumber,
});

export const thumbnailSchema = z.object({
  template_videos: z.array(requiredUrl),
  headline_font: freeText,
  headline_colors: freeText,
  max_headline_words: wholeNumber(1, 12),
  face: z.enum(THUMBNAIL_FACES),
  must_include: freeText,
  must_avoid: freeText,
  sizes: z.array(z.string().trim().regex(SIZE_PATTERN, "Use width x height in pixels, like 1280x720")),
});

export const stageModesSchema = z.object({
  research: stageModeEnum,
  title: stageModeEnum,
  script: stageModeEnum,
  storyboard: stageModeEnum,
  voice: stageModeEnum,
  images: stageModeEnum,
  edit: stageModeEnum,
  export: stageModeEnum,
});

export const channelFormSchema = z.object({
  slug: z.union([
    z.literal(""),
    z.string().regex(SLUG_PATTERN, "Folder names use lowercase letters, numbers and single dashes, like kind-ledger"),
  ]),
  channel: channelIdentitySchema,
  competitors: z.array(competitorSchema),
  frameworks: z.array(frameworkSchema),
  voice: voiceSchema,
  images: imagesSchema,
  thumbnail: thumbnailSchema,
  stage_modes: stageModesSchema,
  reviewer: freeText,
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
  schema_version: z.number().int(),
});

/** Form state: the API shape, except that the optional channel link is "" instead of null. */
export type ChannelFormValues = z.infer<typeof channelFormSchema>;

export function channelToForm(channel: Channel): ChannelFormValues {
  return {
    ...channel,
    channel: { ...channel.channel, url: channel.channel.url ?? "" },
    competitors: channel.competitors.map((c) => ({ ...c })),
    frameworks: channel.frameworks.map((f) => ({ ...f })),
    voice: { ...channel.voice },
    images: { ...channel.images },
    thumbnail: {
      ...channel.thumbnail,
      template_videos: [...channel.thumbnail.template_videos],
      sizes: [...channel.thumbnail.sizes],
    },
    stage_modes: { ...channel.stage_modes },
  };
}

/** Body for POST/PUT. An empty slug is left out so the server derives it from the name. */
export function formToChannel(values: ChannelFormValues): ChannelCreate {
  const { slug, ...rest } = values;
  const body: ChannelCreate = {
    ...rest,
    channel: {
      ...values.channel,
      url: values.channel.url === "" ? null : values.channel.url,
      brand_colors: values.channel.brand_colors.map((c) => c.toUpperCase()),
    },
  };
  if (slug) body.slug = slug;
  return body;
}

// ---- tabs -----------------------------------------------------------------

export const CHANNEL_TAB_IDS = [
  "channel",
  "competitors",
  "frameworks",
  "voice",
  "images",
  "thumbnail",
  "stage_modes",
] as const;
export type ChannelTabId = (typeof CHANNEL_TAB_IDS)[number];

export const CHANNEL_TABS: ReadonlyArray<{ id: ChannelTabId; label: string }> = [
  { id: "channel", label: "Channel" },
  { id: "competitors", label: "Competitors" },
  { id: "frameworks", label: "Frameworks" },
  { id: "voice", label: "Voice" },
  { id: "images", label: "Images" },
  { id: "thumbnail", label: "Thumbnail" },
  { id: "stage_modes", label: "Stage Modes" },
];

/** Which tab shows the field at a dotted path such as "channel.name" or "competitors.0.url". */
export function tabForPath(path: string): ChannelTabId | null {
  const head = path.split(".")[0];
  switch (head) {
    case "slug":
    case "channel":
      return "channel";
    case "competitors":
      return "competitors";
    case "frameworks":
      return "frameworks";
    case "voice":
      return "voice";
    case "images":
      return "images";
    case "thumbnail":
      return "thumbnail";
    case "stage_modes":
    case "reviewer":
      return "stage_modes";
    default:
      return null;
  }
}

/**
 * Put a failed save's errors on the form. Returns the first tab with an error and the
 * messages that belong to no field (shown in a banner).
 */
export function applyServerErrors(
  error: ApiError,
  setError: UseFormSetError<ChannelFormValues>,
): { firstTab: ChannelTabId | null; unassigned: string[] } {
  if (error.status === 409) {
    setError("slug", { type: "server", message: error.message });
    return { firstTab: "channel", unassigned: [] };
  }

  const unassigned: string[] = [];
  let firstTab: ChannelTabId | null = null;
  for (const fieldError of error.fieldErrors) {
    const tab = fieldError.path ? tabForPath(fieldError.path) : null;
    if (tab) {
      setError(fieldError.path as FieldPath<ChannelFormValues>, { type: "server", message: fieldError.message });
      firstTab ??= tab;
    } else {
      unassigned.push(fieldError.path ? `${fieldError.path}: ${fieldError.message}` : fieldError.message);
    }
  }
  if (error.fieldErrors.length === 0) {
    unassigned.push(error.message);
  }
  return { firstTab, unassigned };
}

/** Shapes for /api/settings, from docs/M0-CONTRACT.md. Raw key values never reach the browser. */

export interface KeyStatus {
  set: boolean;
  /** first 3 + "..." + last 4 characters, or "" when the key is not set */
  masked: string;
}

export interface Settings {
  shared_dir: string;
  /** true when no shared folder was chosen and the app falls back to <app_data_dir>/shared */
  shared_dir_is_default: boolean;
  projects_dir: string;
  exports_dir: string;
  keys: Record<string, KeyStatus>;
}

/**
 * Body for PUT /api/settings. Only the fields present are changed.
 * For keys, `null` deletes the key. For a path, `null` asks the backend to go back to its default.
 */
export interface SettingsUpdate {
  shared_dir?: string | null;
  projects_dir?: string | null;
  exports_dir?: string | null;
  keys?: Record<string, string | null>;
}

/** Key names the settings page always shows as rows; other names can be added. */
export const KNOWN_KEY_NAMES = [
  "ANTHROPIC_API_KEY",
  "GEMINI_API_KEY",
  "OPENAI_API_KEY",
  "FAL_KEY",
  "REPLICATE_API_TOKEN",
  "IDEOGRAM_API_KEY",
  "MINIMAX_API_KEY",
  "MINIMAX_GROUP_ID",
  "CARTESIA_API_KEY",
  "INWORLD_API_KEY",
  "FISH_AUDIO_API_KEY",
  "AZURE_SPEECH_KEY",
  "AZURE_SPEECH_REGION",
] as const;

export const KEY_NAME_PATTERN = /^[A-Z][A-Z0-9_]*$/;

/** Plain-English purpose for each known key, shown next to its name. */
export const KEY_PURPOSE: Record<string, string> = {
  ANTHROPIC_API_KEY: "Claude: scripts, titles, storyboards and checks",
  GEMINI_API_KEY: "Google Gemini: images",
  OPENAI_API_KEY: "OpenAI: images",
  FAL_KEY: "fal.ai: FLUX images",
  REPLICATE_API_TOKEN: "Replicate: FLUX images",
  IDEOGRAM_API_KEY: "Ideogram: images and thumbnails",
  MINIMAX_API_KEY: "MiniMax: voice",
  MINIMAX_GROUP_ID: "MiniMax: account group id (goes with the MiniMax key)",
  CARTESIA_API_KEY: "Cartesia: voice",
  INWORLD_API_KEY: "Inworld: voice",
  FISH_AUDIO_API_KEY: "Fish Audio: voice",
  AZURE_SPEECH_KEY: "Microsoft Azure: voice",
  AZURE_SPEECH_REGION: "Microsoft Azure: region, for example westeurope",
};

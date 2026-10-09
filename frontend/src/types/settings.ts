/**
 * Shapes for /api/settings, from docs/M0-CONTRACT.md plus the M2 additions in
 * docs/M1-M2-CONTRACT.md section 11 (nested `llm`, `research`, `pipeline`, `voice` keys and a
 * read-only provider status list). Raw key values never reach the browser.
 */

export interface KeyStatus {
  set: boolean;
  /** first 3 + "..." + last 4 characters, or "" when the key is not set */
  masked: string;
}

// ---- Models and providers (M2, additive) ----------------------------------------------

export const LLM_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"] as const;
export type LlmModel = (typeof LLM_MODELS)[number];

export const LLM_MODEL_LABELS: Record<LlmModel, string> = {
  "claude-opus-5-5": "Claude Opus 5.5 (best writing, slowest)",
  "claude-sonnet-5-5": "Claude Sonnet 5.5 (good and quick)",
  "claude-haiku-4-5": "Claude Haiku 4.5 (cheapest, simple jobs)",
};

export const LLM_EFFORTS = ["low", "medium", "high"] as const;
export type LlmEffort = (typeof LLM_EFFORTS)[number];

export const LLM_EFFORT_LABELS: Record<LlmEffort, string> = {
  low: "Low (fastest)",
  medium: "Medium",
  high: "High (thinks longest)",
};

/** The tasks in config/llm.yaml (`models` and `effort` keys). */
export const LLM_TASKS = [
  "title",
  "script",
  "speech_normalize",
  "storyboard",
  "transcript_summary",
  "policy_check",
  "summary",
  "classification",
  "image_qa",
] as const;
export type LlmTask = (typeof LLM_TASKS)[number];

export const LLM_TASK_LABELS: Record<LlmTask, { label: string; description: string }> = {
  title: { label: "Titles", description: "Writes the 7 title variants" },
  script: { label: "Script", description: "Writes the full script" },
  speech_normalize: { label: "Speech text", description: "Writes out numbers and dates for the voice" },
  storyboard: { label: "Storyboard", description: "Splits the script into scenes and image prompts" },
  transcript_summary: { label: "Competitor summary", description: "Summarises the competitor video's structure" },
  policy_check: { label: "Policy check", description: "Checks the script for YouTube policy problems" },
  summary: { label: "Summaries", description: "Short summaries shown in the app" },
  classification: { label: "Sorting", description: "Quick yes/no and category decisions" },
  image_qa: { label: "Image check", description: "Looks at each generated picture for problems" },
};

/** The defaults in config/llm.yaml: Opus for writing, Sonnet for the rest. */
export const DEFAULT_LLM_MODELS: Record<LlmTask, LlmModel> = {
  title: "claude-opus-5-5",
  script: "claude-opus-5-5",
  speech_normalize: "claude-sonnet-5-5",
  storyboard: "claude-sonnet-5-5",
  transcript_summary: "claude-sonnet-5-5",
  policy_check: "claude-sonnet-5-5",
  summary: "claude-sonnet-5-5",
  classification: "claude-sonnet-5-5",
  image_qa: "claude-sonnet-5-5",
};

export const DEFAULT_LLM_EFFORT: Record<LlmTask, LlmEffort> = {
  title: "high",
  script: "high",
  speech_normalize: "medium",
  storyboard: "medium",
  transcript_summary: "medium",
  policy_check: "low",
  summary: "medium",
  classification: "low",
  image_qa: "low",
};

export interface LlmSettings {
  /** model id per task; tasks the backend adds later are kept as they come */
  models: Record<string, string>;
  effort: Record<string, LlmEffort>;
}

export const RESEARCH_PROVIDERS = ["yt-dlp", "mock"] as const;
export type ResearchProvider = (typeof RESEARCH_PROVIDERS)[number];

export const RESEARCH_PROVIDER_LABELS: Record<ResearchProvider, string> = {
  "yt-dlp": "YouTube (yt-dlp)",
  mock: "Sample data (offline, for testing)",
};

export interface ResearchSettings {
  provider: ResearchProvider;
  /** rerank the top 10 candidates with Claude by fit to the niche */
  llm_rerank: boolean;
}

export interface PipelineSettings {
  /** projects the engine runs at the same time */
  max_parallel_projects: number;
}

export interface VoiceSettings {
  /** words per minute used to size scripts and estimate scene lengths */
  speaking_rate_wpm: number;
}

export const PROVIDER_KINDS = ["llm", "research", "image", "voice"] as const;
export type ProviderKind = (typeof PROVIDER_KINDS)[number];

export const PROVIDER_STATUSES = ["ready", "mock", "missing_key", "not_configured", "error"] as const;
export type ProviderStatusValue = (typeof PROVIDER_STATUSES)[number];

/** One row of the provider status list the backend builds from providers/registry.py. */
export interface ProviderStatus {
  id: string;
  kind: ProviderKind;
  name: string;
  status: ProviderStatusValue;
  detail: string;
}

export function defaultLlmSettings(): LlmSettings {
  return { models: { ...DEFAULT_LLM_MODELS }, effort: { ...DEFAULT_LLM_EFFORT } };
}

export function defaultResearchSettings(): ResearchSettings {
  return { provider: "yt-dlp", llm_rerank: false };
}

export function defaultPipelineSettings(): PipelineSettings {
  return { max_parallel_projects: 2 };
}

export function defaultVoiceSettings(): VoiceSettings {
  return { speaking_rate_wpm: 150 };
}

export interface Settings {
  shared_dir: string;
  /** true when no shared folder was chosen and the app falls back to <app_data_dir>/shared */
  shared_dir_is_default: boolean;
  projects_dir: string;
  exports_dir: string;
  keys: Record<string, KeyStatus>;
  /** M2 additions; missing on an older backend */
  llm?: Partial<LlmSettings> | null;
  research?: Partial<ResearchSettings> | null;
  pipeline?: Partial<PipelineSettings> | null;
  voice?: Partial<VoiceSettings> | null;
  providers?: ProviderStatus[] | null;
}

/**
 * Body for PUT /api/settings. Only the fields present are changed.
 * For keys, `null` deletes the key. For a path, `null` asks the backend to go back to its default.
 * The nested objects replace the matching settings.json keys as a whole.
 */
export interface SettingsUpdate {
  shared_dir?: string | null;
  projects_dir?: string | null;
  exports_dir?: string | null;
  keys?: Record<string, string | null>;
  llm?: LlmSettings;
  research?: ResearchSettings;
  pipeline?: PipelineSettings;
  voice?: VoiceSettings;
}

/** The nested settings with every gap filled from the defaults, plus what the backend sent. */
export interface ModelSettings {
  llm: LlmSettings;
  research: ResearchSettings;
  pipeline: PipelineSettings;
  voice: VoiceSettings;
  providers: ProviderStatus[];
  /** false when the backend answered without the nested keys (older app version) */
  supported: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function oneOf<T extends string>(value: unknown, allowed: ReadonlyArray<T>, fallback: T): T {
  return typeof value === "string" && (allowed as ReadonlyArray<string>).includes(value) ? (value as T) : fallback;
}

function stringMap(value: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!isRecord(value)) return out;
  for (const [key, item] of Object.entries(value)) if (typeof item === "string") out[key] = item;
  return out;
}

/** Fill the nested settings from the response, tolerating an older backend without them. */
export function resolveModelSettings(settings: Settings | undefined): ModelSettings {
  const llm = defaultLlmSettings();
  const research = defaultResearchSettings();
  const pipeline = defaultPipelineSettings();
  const voice = defaultVoiceSettings();
  const supported = Boolean(settings && (settings.llm || settings.research || settings.pipeline || settings.voice));

  if (settings?.llm) {
    Object.assign(llm.models, stringMap(settings.llm.models));
    for (const [task, effort] of Object.entries(stringMap(settings.llm.effort))) {
      llm.effort[task] = oneOf(effort, LLM_EFFORTS, "medium");
    }
  }
  if (settings?.research) {
    research.provider = oneOf(settings.research.provider, RESEARCH_PROVIDERS, research.provider);
    if (typeof settings.research.llm_rerank === "boolean") research.llm_rerank = settings.research.llm_rerank;
  }
  if (settings?.pipeline && typeof settings.pipeline.max_parallel_projects === "number") {
    pipeline.max_parallel_projects = settings.pipeline.max_parallel_projects;
  }
  if (settings?.voice && typeof settings.voice.speaking_rate_wpm === "number") {
    voice.speaking_rate_wpm = settings.voice.speaking_rate_wpm;
  }

  const providers: ProviderStatus[] = Array.isArray(settings?.providers)
    ? settings.providers.filter(isRecord).map((row) => ({
        id: typeof row.id === "string" ? row.id : "",
        kind: oneOf(row.kind, PROVIDER_KINDS, "llm"),
        name: typeof row.name === "string" ? row.name : String(row.id ?? ""),
        status: oneOf(row.status, PROVIDER_STATUSES, "not_configured"),
        detail: typeof row.detail === "string" ? row.detail : "",
      }))
    : [];

  return { llm, research, pipeline, voice, providers, supported };
}

// ---- API keys (M0) -------------------------------------------------------------------

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

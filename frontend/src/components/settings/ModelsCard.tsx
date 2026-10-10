import { Cpu, Save } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { errorMessage } from "../../api/client";
import { useModelSettings, useUpdateSettings } from "../../api/settings";
import {
  LLM_EFFORT_LABELS,
  LLM_EFFORTS,
  LLM_MODEL_LABELS,
  LLM_MODELS,
  LLM_TASK_LABELS,
  LLM_TASKS,
  RESEARCH_PROVIDER_LABELS,
  RESEARCH_PROVIDERS,
  type LlmEffort,
  type LlmTask,
  type ModelSettings,
  type ProviderKind,
  type ProviderStatus,
  type ProviderStatusValue,
  type ResearchProvider,
  type Settings,
  type SettingsUpdate,
} from "../../types";
import { Badge, type BadgeTone } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Input, Select } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { ErrorState, Notice } from "../ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../ui/Table";
import { useToast } from "../ui/Toast";

type Draft = Pick<ModelSettings, "llm" | "research" | "pipeline" | "voice">;

const PROVIDER_STATUS: Record<ProviderStatusValue, { label: string; tone: BadgeTone }> = {
  ready: { label: "Ready", tone: "ok" },
  mock: { label: "Sample data", tone: "info" },
  missing_key: { label: "Key missing", tone: "warn" },
  not_configured: { label: "Not set up", tone: "neutral" },
  error: { label: "Problem", tone: "fail" },
};

const KIND_LABELS: Record<ProviderKind, string> = {
  llm: "Writing (Claude)",
  research: "Research",
  image: "Images",
  voice: "Voice",
};

function pickDraft(resolved: ModelSettings): Draft {
  return {
    llm: { models: { ...resolved.llm.models }, effort: { ...resolved.llm.effort } },
    research: { ...resolved.research },
    pipeline: { ...resolved.pipeline },
    voice: { ...resolved.voice },
  };
}

/**
 * When the backend sends no provider list, read what the API keys say: the status page still
 * tells the user which services have a key and which do not.
 */
function providersFromKeys(settings: Settings | undefined, research: ResearchProvider): ProviderStatus[] {
  const has = (name: string) => Boolean(settings?.keys?.[name]?.set);
  const key = (name: string, label: string): ProviderStatus => ({
    id: name.toLowerCase(),
    kind: "llm",
    name: label,
    status: has(name) ? "ready" : "missing_key",
    detail: has(name) ? `${name} is set.` : `Add ${name} under API keys.`,
  });
  const imageKeys = ["GEMINI_API_KEY", "OPENAI_API_KEY", "FAL_KEY", "REPLICATE_API_TOKEN", "IDEOGRAM_API_KEY"];
  const voiceKeys = ["AI33_API_KEY", "MINIMAX_API_KEY", "CARTESIA_API_KEY", "INWORLD_API_KEY", "FISH_AUDIO_API_KEY", "AZURE_SPEECH_KEY"];
  const imageSet = imageKeys.filter(has);
  const voiceSet = voiceKeys.filter(has);
  return [
    key("ANTHROPIC_API_KEY", "Claude"),
    {
      id: "research",
      kind: "research",
      name: RESEARCH_PROVIDER_LABELS[research],
      status: research === "mock" ? "mock" : "ready",
      detail: research === "mock" ? "Research uses built-in sample videos; nothing is fetched from YouTube." : "Uses yt-dlp on this computer; no key needed.",
    },
    {
      id: "images",
      kind: "image",
      name: imageSet.length > 0 ? `Images (${imageSet.length} key${imageSet.length === 1 ? "" : "s"} set)` : "Images",
      status: imageSet.length > 0 ? "ready" : "missing_key",
      detail: imageSet.length > 0 ? `Keys set: ${imageSet.join(", ")}.` : "No image service key is set yet. Images arrive in a later milestone.",
    },
    {
      id: "voice",
      kind: "voice",
      name: voiceSet.length > 0 ? `Voice (${voiceSet.length} key${voiceSet.length === 1 ? "" : "s"} set)` : "Voice",
      status: voiceSet.length > 0 ? "ready" : "missing_key",
      detail: voiceSet.length > 0 ? `Keys set: ${voiceSet.join(", ")}.` : "No voice service key is set yet. Voice arrives in a later milestone.",
    },
  ];
}

/** Settings > Models and providers: which Claude model does each job, research source, parallel projects, speaking rate. */
export function ModelsCard() {
  const { query, resolved } = useModelSettings();
  const update = useUpdateSettings();
  const { toast } = useToast();

  const [draft, setDraft] = useState<Draft>(() => pickDraft(resolved));
  const resolvedKey = useMemo(() => JSON.stringify(pickDraft(resolved)), [resolved]);
  const lastResolvedKey = useRef("");
  useEffect(() => {
    if (resolvedKey !== lastResolvedKey.current) {
      lastResolvedKey.current = resolvedKey;
      setDraft(pickDraft(resolved));
    }
  }, [resolved, resolvedKey]);

  const changed = useMemo(() => {
    const base = pickDraft(resolved);
    return (Object.keys(draft) as Array<keyof Draft>).filter((key) => JSON.stringify(draft[key]) !== JSON.stringify(base[key]));
  }, [draft, resolved]);

  const tasks = useMemo(() => {
    const extra = Object.keys(draft.llm.models).filter((task) => !(LLM_TASKS as ReadonlyArray<string>).includes(task)).sort();
    return [...LLM_TASKS, ...extra];
  }, [draft.llm.models]);

  const providers = resolved.providers.length > 0 ? resolved.providers : providersFromKeys(query.data, draft.research.provider);

  const parallelOk = Number.isInteger(draft.pipeline.max_parallel_projects) && draft.pipeline.max_parallel_projects >= 1 && draft.pipeline.max_parallel_projects <= 8;
  const rateOk = Number.isFinite(draft.voice.speaking_rate_wpm) && draft.voice.speaking_rate_wpm >= 80 && draft.voice.speaking_rate_wpm <= 260;

  const save = async () => {
    if (changed.length === 0 || !parallelOk || !rateOk) return;
    const body: SettingsUpdate = {};
    if (changed.includes("llm")) body.llm = draft.llm;
    if (changed.includes("research")) body.research = draft.research;
    if (changed.includes("pipeline")) body.pipeline = draft.pipeline;
    if (changed.includes("voice")) body.voice = draft.voice;
    try {
      const saved = await update.mutateAsync(body);
      const kept = Boolean(saved.llm || saved.research || saved.pipeline || saved.voice);
      if (kept) toast({ tone: "success", title: "Model settings saved" });
      else
        toast({
          tone: "error",
          title: "The server did not keep these settings",
          description: "This app version saves folders and keys only. Update the app to save model settings.",
        });
    } catch (error) {
      toast({ tone: "error", title: "Could not save the model settings", description: errorMessage(error) });
    }
  };

  const setModel = (task: string, model: string) => setDraft((current) => ({ ...current, llm: { ...current.llm, models: { ...current.llm.models, [task]: model } } }));
  const setEffort = (task: string, effort: LlmEffort) => setDraft((current) => ({ ...current, llm: { ...current.llm, effort: { ...current.llm.effort, [task]: effort } } }));

  return (
    <Card
      id="models"
      title="Models and providers"
      description="Which Claude model does each writing job, where research comes from, how many videos run at once, and the narration pace."
      actions={
        <Button variant="primary" size="sm" icon={<Save />} disabled={changed.length === 0 || !parallelOk || !rateOk} loading={update.isPending} onClick={() => void save()}>
          Save models
        </Button>
      }
    >
      {query.isPending ? (
        <LoadingBlock label="Loading model settings..." />
      ) : query.isError ? (
        <ErrorState error={query.error} title="Could not load the model settings" onRetry={() => void query.refetch()} />
      ) : (
        <div className="flex flex-col gap-7">
          {!resolved.supported ? (
            <Notice tone="info" title="Showing the built-in defaults">
              This app version has not started saving model settings yet, so the values below are what the pipeline uses out of the box.
            </Notice>
          ) : null}

          <section className="flex flex-col gap-3">
            <div>
              <h3 className="text-sm font-semibold text-ink">Claude model per job</h3>
              <p className="mt-0.5 text-xs text-ink-muted">
                Opus writes best and costs most; Sonnet is quick and good for planning; Haiku is for simple, bulk jobs. Effort is how long the model may think.
              </p>
            </div>
            <div className="overflow-hidden rounded-card border border-line">
              <Table>
                <THead>
                  <TR>
                    <TH>Job</TH>
                    <TH className="w-[300px]">Model</TH>
                    <TH className="w-[200px]">Effort</TH>
                  </TR>
                </THead>
                <TBody>
                  {tasks.map((task) => {
                    const label = LLM_TASK_LABELS[task as LlmTask];
                    const model = draft.llm.models[task] ?? "";
                    const known = (LLM_MODELS as ReadonlyArray<string>).includes(model);
                    return (
                      <TR key={task}>
                        <TD>
                          <div className="flex items-center gap-2">
                            <Cpu className="size-4 text-ink-faint" aria-hidden />
                            <span className="font-medium text-ink">{label?.label ?? task.replace(/_/g, " ")}</span>
                          </div>
                          {label ? <p className="mt-0.5 pl-6 text-xs text-ink-muted">{label.description}</p> : null}
                        </TD>
                        <TD>
                          <Select value={model} aria-label={`Model for ${label?.label ?? task}`} onChange={(event) => setModel(task, event.target.value)}>
                            {!known && model ? <option value={model}>{model}</option> : null}
                            {LLM_MODELS.map((id) => (
                              <option key={id} value={id}>
                                {LLM_MODEL_LABELS[id]}
                              </option>
                            ))}
                          </Select>
                        </TD>
                        <TD>
                          <Select
                            value={draft.llm.effort[task] ?? "medium"}
                            aria-label={`Effort for ${label?.label ?? task}`}
                            onChange={(event) => setEffort(task, event.target.value as LlmEffort)}
                          >
                            {LLM_EFFORTS.map((effort) => (
                              <option key={effort} value={effort}>
                                {LLM_EFFORT_LABELS[effort]}
                              </option>
                            ))}
                          </Select>
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            </div>
          </section>

          <section className="grid gap-x-6 gap-y-5 md:grid-cols-3">
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-muted">Research source</span>
              <Select
                value={draft.research.provider}
                onChange={(event) => setDraft((current) => ({ ...current, research: { ...current.research, provider: event.target.value as ResearchProvider } }))}
              >
                {RESEARCH_PROVIDERS.map((provider) => (
                  <option key={provider} value={provider}>
                    {RESEARCH_PROVIDER_LABELS[provider]}
                  </option>
                ))}
              </Select>
              <span className="text-xs text-ink-faint">Sample data runs fully offline, for trying the app out.</span>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-muted">Videos worked on at once</span>
              <Input
                type="number"
                min={1}
                max={8}
                step={1}
                invalid={!parallelOk}
                value={Number.isFinite(draft.pipeline.max_parallel_projects) ? draft.pipeline.max_parallel_projects : ""}
                onChange={(event) => setDraft((current) => ({ ...current, pipeline: { max_parallel_projects: Number(event.target.value) } }))}
              />
              <span className={parallelOk ? "text-xs text-ink-faint" : "text-xs text-fail"}>
                {parallelOk ? "1 to 8. More runs faster but spends money faster too." : "Use a whole number from 1 to 8."}
              </span>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-muted">Speaking rate (words per minute)</span>
              <Input
                type="number"
                min={80}
                max={260}
                step={5}
                invalid={!rateOk}
                value={Number.isFinite(draft.voice.speaking_rate_wpm) ? draft.voice.speaking_rate_wpm : ""}
                onChange={(event) => setDraft((current) => ({ ...current, voice: { speaking_rate_wpm: Number(event.target.value) } }))}
              />
              <span className={rateOk ? "text-xs text-ink-faint" : "text-xs text-fail"}>
                {rateOk ? "150 is a normal narration pace. Sets how long scripts are and how long each scene runs." : "Use a number from 80 to 260."}
              </span>
            </label>
            <label className="flex items-center gap-2 md:col-span-3">
              <input
                type="checkbox"
                className="size-4 accent-accent"
                checked={draft.research.llm_rerank}
                onChange={(event) => setDraft((current) => ({ ...current, research: { ...current.research, llm_rerank: event.target.checked } }))}
              />
              <span className="text-[13px] text-ink">Let Claude re-rank the top 10 research candidates by fit to the channel</span>
              <span className="text-xs text-ink-faint">(one small call per scan)</span>
            </label>
          </section>

          <section className="flex flex-col gap-3">
            <div>
              <h3 className="text-sm font-semibold text-ink">Provider status</h3>
              <p className="mt-0.5 text-xs text-ink-muted">What each part of the pipeline will use right now.</p>
            </div>
            <div className="overflow-hidden rounded-card border border-line">
              <Table>
                <THead>
                  <TR>
                    <TH className="w-[160px]">Part</TH>
                    <TH>Provider</TH>
                    <TH className="w-[130px]">Status</TH>
                  </TR>
                </THead>
                <TBody>
                  {providers.map((provider) => {
                    const status = PROVIDER_STATUS[provider.status];
                    return (
                      <TR key={provider.id || provider.name}>
                        <TD className="text-ink-muted">{KIND_LABELS[provider.kind]}</TD>
                        <TD>
                          <div className="font-medium text-ink">{provider.name}</div>
                          {provider.detail ? <p className="mt-0.5 text-xs text-ink-muted">{provider.detail}</p> : null}
                        </TD>
                        <TD>
                          <Badge tone={status.tone} dot>
                            {status.label}
                          </Badge>
                        </TD>
                      </TR>
                    );
                  })}
                </TBody>
              </Table>
            </div>
          </section>
        </div>
      )}
    </Card>
  );
}

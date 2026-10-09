import { Clapperboard, Save } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { errorMessage } from "../../api/client";
import { useRenderSettings, useUpdateSettings } from "../../api/settings";
import {
  CAPTION_STYLE_LABELS,
  CAPTION_STYLES,
  type CaptionsSettings,
  type CaptionStyleName,
  type RenderSettings,
  type RenderSettingsResolved,
  type SettingsUpdate,
  X264_PRESET_LABELS,
  X264_PRESETS,
  type X264Preset,
} from "../../types/settings";
import { RENDER_PRESET_DESCRIPTIONS, RENDER_PRESET_LABELS, RENDER_PRESETS, type RenderPreset } from "../../types/timeline";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Select } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { ErrorState, Notice } from "../ui/States";
import { useToast } from "../ui/Toast";

interface Draft {
  render: RenderSettings;
  captions: CaptionsSettings;
}

function pickDraft(resolved: RenderSettingsResolved): Draft {
  return {
    render: { ...resolved.render, default_presets: [...resolved.render.default_presets] },
    captions: { ...resolved.captions },
  };
}

/** Settings > Render: which sizes every video gets, the x264 speed, 4K on or off, captions on or off. */
export function RenderCard() {
  const { query, resolved } = useRenderSettings();
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

  const presetsOk = draft.render.default_presets.length > 0;

  const setRender = (patch: Partial<RenderSettings>) => setDraft((current) => ({ ...current, render: { ...current.render, ...patch } }));
  const setCaptions = (patch: Partial<CaptionsSettings>) => setDraft((current) => ({ ...current, captions: { ...current.captions, ...patch } }));

  const togglePreset = (preset: RenderPreset, checked: boolean) => {
    const next = RENDER_PRESETS.filter((id) => (id === preset ? checked : draft.render.default_presets.includes(id)));
    setRender({ default_presets: next });
  };

  const toggle4k = (enabled: boolean) => {
    setRender({
      enable_4k: enabled,
      // 4K off also takes it out of the defaults, so no video renders it by accident.
      default_presets: enabled ? draft.render.default_presets : draft.render.default_presets.filter((id) => id !== "2160p"),
    });
  };

  const save = async () => {
    if (changed.length === 0 || !presetsOk) return;
    const body: SettingsUpdate = {};
    if (changed.includes("render")) body.render = draft.render;
    if (changed.includes("captions")) body.captions = draft.captions;
    try {
      const saved = await update.mutateAsync(body);
      if (saved.render || saved.captions) toast({ tone: "success", title: "Render settings saved" });
      else
        toast({
          tone: "error",
          title: "The server did not keep these settings",
          description: "This app version does not save render settings yet. Update the app, then try again.",
        });
    } catch (error) {
      toast({ tone: "error", title: "Could not save the render settings", description: errorMessage(error) });
    }
  };

  const styleKnown = (CAPTION_STYLES as readonly string[]).includes(draft.captions.style);

  return (
    <Card
      id="render"
      title="Render"
      description="Which sizes every finished video gets, how hard the encoder works, and whether captions are burnt in."
      actions={
        <Button variant="primary" size="sm" icon={<Save />} disabled={changed.length === 0 || !presetsOk} loading={update.isPending} onClick={() => void save()}>
          Save render settings
        </Button>
      }
    >
      {query.isPending ? (
        <LoadingBlock label="Loading render settings..." />
      ) : query.isError ? (
        <ErrorState error={query.error} title="Could not load the render settings" onRetry={() => void query.refetch()} />
      ) : (
        <div className="flex flex-col gap-7">
          {!resolved.supported ? (
            <Notice tone="info" title="Showing the built-in defaults">
              This app version has not started saving render settings yet, so the values below are what the renderer uses out of the box.
            </Notice>
          ) : null}

          <section className="grid gap-x-6 gap-y-5 md:grid-cols-[260px_1fr]">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium text-ink">
                <Clapperboard className="size-4 text-ink-faint" aria-hidden />
                Sizes rendered by default
              </div>
              <p className="mt-1 text-xs text-ink-muted">Every video gets these when it reaches the edit stage. The Edit panel can add or drop sizes per video.</p>
            </div>
            <div className="flex flex-col gap-2">
              {RENDER_PRESETS.map((preset) => {
                const blocked = preset === "2160p" && !draft.render.enable_4k;
                return (
                  <label key={preset} className={blocked ? "flex items-start gap-3 opacity-60" : "flex items-start gap-3"}>
                    <input
                      type="checkbox"
                      className="mt-0.5 size-4 accent-accent"
                      checked={draft.render.default_presets.includes(preset)}
                      disabled={blocked}
                      onChange={(event) => togglePreset(preset, event.target.checked)}
                    />
                    <span>
                      <span className="text-[13px] font-medium text-ink">{RENDER_PRESET_LABELS[preset]}</span>
                      <span className="block text-xs text-ink-muted">{blocked ? "Turn on 4K below first." : RENDER_PRESET_DESCRIPTIONS[preset]}</span>
                    </span>
                  </label>
                );
              })}
              {!presetsOk ? <p className="text-xs text-fail">Tick at least one size, or no final video is made.</p> : null}
            </div>
          </section>

          <section className="grid gap-x-6 gap-y-5 md:grid-cols-2">
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-muted">Encoder speed (x264 preset)</span>
              <Select value={draft.render.x264_preset} onChange={(event) => setRender({ x264_preset: event.target.value as X264Preset })}>
                {X264_PRESETS.map((preset) => (
                  <option key={preset} value={preset}>
                    {X264_PRESET_LABELS[preset]}
                  </option>
                ))}
              </Select>
              <span className="text-xs text-ink-faint">Slower presets take longer and make smaller files at the same quality. Medium is a good balance on this computer.</span>
            </label>
            <div className="flex flex-col gap-3">
              <label className="flex items-start gap-3">
                <input type="checkbox" className="mt-0.5 size-4 accent-accent" checked={draft.render.enable_4k} onChange={(event) => toggle4k(event.target.checked)} />
                <span>
                  <span className="text-[13px] font-medium text-ink">Allow 4K (2160p)</span>
                  <span className="block text-xs text-ink-muted">Four times the pixels of 1080p. Rendering takes a long time and a lot of memory; leave it off unless a channel needs it.</span>
                </span>
              </label>
            </div>
          </section>

          <section className="grid gap-x-6 gap-y-5 md:grid-cols-2">
            <label className="flex items-start gap-3">
              <input type="checkbox" className="mt-0.5 size-4 accent-accent" checked={draft.captions.enabled} onChange={(event) => setCaptions({ enabled: event.target.checked })} />
              <span>
                <span className="text-[13px] font-medium text-ink">Burn captions into the video</span>
                <span className="block text-xs text-ink-muted">Word-timed captions from the voice stage, drawn at the bottom of the picture. The Edit panel can switch them off per video.</span>
              </span>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[13px] font-medium text-ink-muted">Caption style</span>
              <Select value={draft.captions.style} disabled={!draft.captions.enabled} onChange={(event) => setCaptions({ style: event.target.value })}>
                {!styleKnown && draft.captions.style ? <option value={draft.captions.style}>{draft.captions.style}</option> : null}
                {CAPTION_STYLES.map((style) => (
                  <option key={style} value={style}>
                    {CAPTION_STYLE_LABELS[style as CaptionStyleName]}
                  </option>
                ))}
              </Select>
              <span className="text-xs text-ink-faint">Fonts per language (Arabic, Hindi, Japanese, Korean) come from config/captions.yaml.</span>
            </label>
          </section>
        </div>
      )}
    </Card>
  );
}

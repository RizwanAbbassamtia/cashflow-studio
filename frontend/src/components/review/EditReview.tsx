import { AlertTriangle, Check, Clapperboard, FileText, MessageSquare, RotateCcw, Subtitles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useChannel } from "../../api/channels";
import { ApiError, errorMessage } from "../../api/client";
import {
  PROXY_FILE,
  projectMediaUrl,
  RENDER_LOG_FILE,
  sceneImageFile,
  useApproveEdit,
  useEditPayload,
  useRenderPresets,
  useRenderProgress,
  type EditApproveBody,
} from "../../api/edit";
import { useProject } from "../../api/projects";
import { useRenderSettings } from "../../api/settings";
import { renderPresetLabel, type EditApproveEdits, type EditReviewPayload } from "../../types/timeline";
import { formatClock, transitionLabel } from "../edit/editUtils";
import { MusicSelector } from "../edit/MusicSelector";
import { ProxyPlayer } from "../edit/ProxyPlayer";
import { RenderPresets } from "../edit/RenderPresets";
import { SceneStrip, type SceneStripItem } from "../edit/SceneStrip";
import { useUnsavedGuard } from "../edit/useUnsavedGuard";
import { Panel } from "../script/Panel";
import { useReviewer } from "../script/useReviewer";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/Dialog";
import { Input, Textarea } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../ui/States";
import { useToast } from "../ui/Toast";

export interface EditReviewProps {
  projectId: string;
}

/** The choices a reviewer makes on this panel; sent as `edits` on approve or on a render. */
interface EditDraft {
  presets: string[];
  music_path: string | null;
  captions_enabled: boolean;
  popups_enabled: boolean;
}

function draftFrom(payload: EditReviewPayload, defaultPresets: string[]): EditDraft {
  const presets = payload.presets.filter((preset) => payload.available_presets.includes(preset));
  return {
    presets: presets.length > 0 ? presets : defaultPresets.filter((preset) => payload.available_presets.includes(preset)),
    music_path: payload.music_path,
    captions_enabled: payload.captions_enabled,
    popups_enabled: payload.popups_enabled,
  };
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

/** Only what differs from the saved state, so an approve without changes sends no edits. */
function editsBetween(draft: EditDraft, saved: EditDraft): EditApproveEdits {
  const edits: EditApproveEdits = {};
  if (!sameList(draft.presets, saved.presets)) edits.presets = draft.presets;
  if (draft.music_path !== saved.music_path) edits.music_path = draft.music_path;
  if (draft.captions_enabled !== saved.captions_enabled) edits.captions_enabled = draft.captions_enabled;
  if (draft.popups_enabled !== saved.popups_enabled) edits.popups_enabled = draft.popups_enabled;
  return edits;
}

/**
 * The Edit review: the proxy preview with a scene strip, the popup and caption toggles,
 * the music pick, the render sizes with live progress, the checks and Approve. Renders are
 * a redo of the stage with `edits.presets`; approving sends the final choices and moves
 * the project on to export. Rendered inside the project page's stage card, which shows
 * status, failed gates and notes above it.
 */
export function EditReview({ projectId }: EditReviewProps) {
  const { toast } = useToast();
  const reviewer = useReviewer(projectId);
  const project = useProject(projectId);
  const channel = useChannel(project.data?.channel_slug);
  const { resolved: renderSettings } = useRenderSettings();
  const { query, payload } = useEditPayload(projectId);
  const progress = useRenderProgress(projectId);
  const render = useRenderPresets(projectId);
  const approve = useApproveEdit(projectId);

  const status = project.data?.stages.edit?.status ?? "pending";
  const running = status === "running";
  const canApprove = status === "awaiting_review" || status === "failed";
  const canRender = status === "awaiting_review" || status === "failed" || status === "done";

  // The draft follows the saved payload whenever the server sends a different one.
  const defaultPresets = renderSettings.render.default_presets;
  const savedDraft = useMemo(() => (payload ? draftFrom(payload, defaultPresets) : null), [payload, defaultPresets]);
  const savedKey = useMemo(() => (savedDraft ? JSON.stringify(savedDraft) : ""), [savedDraft]);
  const [draft, setDraft] = useState<EditDraft | null>(null);
  const lastSavedKey = useRef("");
  useEffect(() => {
    if (savedKey !== lastSavedKey.current) {
      lastSavedKey.current = savedKey;
      setDraft(savedDraft);
    }
  }, [savedDraft, savedKey]);
  const dirty = Boolean(draft && savedDraft && JSON.stringify(draft) !== savedKey);
  const blocker = useUnsavedGuard(dirty);

  // Player state shared with the scene strip.
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState<number | null>(null);

  const [approveNotes, setApproveNotes] = useState("");
  const [overrideGates, setOverrideGates] = useState(false);
  const [blockedMessage, setBlockedMessage] = useState<string | null>(null);

  const stripScenes = useMemo<SceneStripItem[]>(
    () =>
      (payload?.scenes ?? []).map((scene) => ({
        index: scene.index,
        start_s: scene.start_s,
        end_s: scene.end_s,
        thumbUrl: projectMediaUrl(projectId, scene.image_path ?? sceneImageFile(scene.index), scene.image_url),
        transition: scene.transition,
        popupText: scene.popup_text,
        locked: scene.locked,
      })),
    [payload, projectId],
  );

  if (query.isPending) {
    return <LoadingBlock label="Loading the timeline..." />;
  }

  if (query.isError) {
    return <ErrorState error={query.error} title="Could not load the timeline" onRetry={() => void query.refetch()} />;
  }

  if (!payload || !draft || !savedDraft) {
    return (
      <EmptyState
        icon={Clapperboard}
        title={running ? "Building the timeline..." : "No timeline to review yet"}
        description={
          running
            ? "The edit stage is laying the scenes on the voice timing and rendering the preview. The player appears here when it is done."
            : "The edit stage has not written 07_edit/timeline.json for this project. When it finishes, the preview, scene strip and render sizes appear here."
        }
        action={
          <Button variant="secondary" size="sm" icon={<RotateCcw />} onClick={() => void query.refetch()}>
            Check again
          </Button>
        }
      />
    );
  }

  const busy = render.isPending || approve.isPending;
  const summary = payload.summary;
  const proxySrc = projectMediaUrl(projectId, payload.proxy_path ?? PROXY_FILE, payload.proxy_url);
  const renderLogUrl = projectMediaUrl(projectId, payload.render_log_path ?? RENDER_LOG_FILE);
  const enable4k = payload.enable_4k ?? renderSettings.render.enable_4k;
  const renderedPresets = new Set(payload.renders.filter((render) => render.status === "done").map((render) => render.preset));
  const missingPresets = draft.presets.filter((preset) => !renderedPresets.has(preset));
  const failingGates = payload.gate_results.filter((gate) => !gate.passed);
  const savedBlocking = failingGates.some((gate) => gate.severity === "block");
  const showOverride = savedBlocking || blockedMessage !== null || status === "failed";
  const musicTrack = draft.music_path ? payload.music_tracks.find((track) => track.path === draft.music_path) : undefined;
  const musicLicenseOk = draft.music_path === payload.music_path ? payload.music_license_ok : (musicTrack?.license_ok ?? false);
  const musicChanged = draft.music_path !== savedDraft.music_path;
  const togglesChanged = draft.captions_enabled !== savedDraft.captions_enabled || draft.popups_enabled !== savedDraft.popups_enabled;

  const patch = (changes: Partial<EditDraft>) => setDraft((current) => (current ? { ...current, ...changes } : current));

  const seekTo = (scene: SceneStripItem) => {
    const video = videoRef.current;
    const target = Math.max(0, scene.start_s + 0.05);
    if (video) {
      video.currentTime = target;
      void video.play().catch(() => {
        /* autoplay refused: the frame still changes */
      });
    }
    setCurrentTime(target);
  };

  const onRender = (presets: string[]) => {
    if (presets.length === 0) return;
    const edits = editsBetween(draft, savedDraft);
    delete edits.presets;
    const names = presets.map(renderPresetLabel).join(", ");
    render.mutate(
      { by: reviewer.effective, presets, edits },
      {
        onSuccess: () => {
          setBlockedMessage(null);
          toast({ tone: "info", title: `Rendering ${names}`, description: "Progress shows on each size. The panel refreshes when the render is done." });
        },
        onError: (error) => toast({ tone: "error", title: `Could not start rendering ${names}`, description: errorMessage(error) }),
      },
    );
  };

  const onApprove = () => {
    const body: EditApproveBody = { by: reviewer.effective };
    if (approveNotes.trim()) body.notes = approveNotes.trim();
    const edits = editsBetween(draft, savedDraft);
    // A ticked size that has no file yet is rendered as part of the approval.
    if (missingPresets.length > 0) edits.presets = draft.presets;
    if (overrideGates && showOverride) edits.override_gates = true;
    if (Object.keys(edits).length > 0) body.edits = edits;
    approve.mutate(body, {
      onSuccess: () => {
        setApproveNotes("");
        setOverrideGates(false);
        setBlockedMessage(null);
        toast({ tone: "success", title: "Edit approved", description: missingPresets.length > 0 ? "The missing sizes render first, then export is next." : "The export stage is next." });
      },
      onError: (error) => {
        if (error instanceof ApiError && error.status === 409) {
          setBlockedMessage(errorMessage(error));
          void query.refetch();
        }
        toast({ tone: "error", title: "Could not approve the edit", description: errorMessage(error) });
      },
    });
  };

  const videoDuration = duration ?? summary.duration_s;

  return (
    <div className="flex flex-col gap-4">
      <Panel
        title="Preview"
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">
              {summary.scene_count} scene{summary.scene_count === 1 ? "" : "s"}
            </Badge>
            <Badge tone="neutral">{formatClock(summary.duration_s)}</Badge>
            {summary.width && summary.height ? (
              <Badge tone="neutral">
                {summary.width} x {summary.height}
              </Badge>
            ) : null}
            {summary.aspect ? <Badge tone="neutral">{summary.aspect}</Badge> : null}
            <Badge tone="neutral">{summary.fps} fps</Badge>
            {dirty ? <Badge tone="warn">Unsaved changes</Badge> : null}
          </span>
        }
        actions={
          <>
            {dirty ? (
              <Button variant="ghost" size="sm" icon={<RotateCcw />} onClick={() => setDraft(savedDraft)} disabled={busy}>
                Discard changes
              </Button>
            ) : null}
            {canApprove ? (
              <Button variant="primary" size="sm" icon={<Check />} loading={approve.isPending} disabled={busy || running || draft.presets.length === 0} onClick={onApprove}>
                {dirty ? "Approve with my changes" : "Approve"}
              </Button>
            ) : null}
          </>
        }
      >
        <div className="flex flex-col gap-3">
          <ProxyPlayer src={proxySrc} aspect={summary.aspect} videoRef={videoRef} onTimeUpdate={setCurrentTime} onDurationChange={(value) => setDuration(Number.isFinite(value) ? value : null)} />
          <div className="flex items-center justify-between gap-3 text-xs text-ink-muted">
            <span>
              Low-resolution preview ({payload.timeline ? `${payload.timeline.proxy.width} x ${payload.timeline.proxy.height}` : "proxy"}). The final sizes below are sharper.
            </span>
            <span className="tabular-nums">
              {formatClock(currentTime)} / {formatClock(videoDuration)}
            </span>
          </div>
          <SceneStrip scenes={stripScenes} currentTime={currentTime} aspect={summary.aspect} onSeek={seekTo} />
        </div>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex flex-col gap-4">
          <Panel title="Timeline" description="Popups, captions and music. The scene order and timing come from the storyboard and the voice stage.">
            <div className="flex flex-col gap-5">
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-[13px] sm:grid-cols-4">
                <div>
                  <dt className="text-xs text-ink-muted">Popups</dt>
                  <dd className="font-medium text-ink">{summary.popup_count}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-muted">Caption cues</dt>
                  <dd className="font-medium text-ink">{summary.caption_cues}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-muted">Average scene</dt>
                  <dd className="font-medium text-ink">{summary.scene_count > 0 ? `${(summary.duration_s / summary.scene_count).toFixed(1)} s` : "-"}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-muted">Transitions</dt>
                  <dd className="font-medium text-ink">{summary.transitions.length}</dd>
                </div>
              </dl>
              {summary.transitions.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {summary.transitions.map((type) => (
                    <Badge key={type} tone="neutral">
                      {transitionLabel(type)}
                    </Badge>
                  ))}
                </div>
              ) : null}

              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex items-start gap-3 rounded-md border border-line px-3 py-2.5">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4 accent-accent"
                    checked={draft.popups_enabled}
                    disabled={running || busy}
                    onChange={(event) => patch({ popups_enabled: event.target.checked })}
                  />
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
                      <MessageSquare className="size-3.5 text-ink-faint" aria-hidden />
                      Show popups
                    </span>
                    <span className="block text-xs text-ink-muted">
                      {summary.popup_count > 0 ? `${summary.popup_count} short text pops from the storyboard.` : "The storyboard has no popups for this video."}
                    </span>
                  </span>
                </label>
                <label className="flex items-start gap-3 rounded-md border border-line px-3 py-2.5">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4 accent-accent"
                    checked={draft.captions_enabled}
                    disabled={running || busy}
                    onChange={(event) => patch({ captions_enabled: event.target.checked })}
                  />
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
                      <Subtitles className="size-3.5 text-ink-faint" aria-hidden />
                      Burn in captions
                    </span>
                    <span className="block text-xs text-ink-muted">
                      {summary.caption_cues > 0 ? `${summary.caption_cues} cues timed to the voice.` : "Word-timed captions at the bottom of the picture."}
                    </span>
                  </span>
                </label>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium text-ink-muted">Background music</span>
                <MusicSelector
                  tracks={payload.music_tracks}
                  value={draft.music_path}
                  licenseOk={musicLicenseOk}
                  onChange={(path) => patch({ music_path: path })}
                  folder={channel.data?.channel.music_folder || null}
                  disabled={running || busy}
                />
              </div>

              {musicChanged || togglesChanged ? (
                <Notice tone="info">
                  The preview still shows the old choices. Press a Render button to see and hear the change; approving renders the final sizes with these choices anyway.
                </Notice>
              ) : null}
            </div>
          </Panel>

          <Panel
            title="Render sizes"
            description="Tick the sizes the export should include, render them here, and watch the progress."
            actions={
              renderLogUrl && (payload.renders.length > 0 || payload.proxy_path || payload.proxy_url) ? (
                <a href={renderLogUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-medium text-accent-text hover:underline">
                  <FileText className="size-3.5" aria-hidden />
                  Render log
                </a>
              ) : null
            }
          >
            <RenderPresets
              available={payload.available_presets}
              selected={draft.presets}
              renders={payload.renders}
              aspect={summary.aspect}
              enable4k={enable4k}
              running={running}
              progress={progress}
              busy={busy}
              canRender={canRender}
              mediaUrl={(render) => projectMediaUrl(projectId, render.path ?? `07_edit/final_${render.preset}.mp4`, render.play_url)}
              onToggle={(preset, checked) => patch({ presets: payload.available_presets.filter((id) => (id === preset ? checked : draft.presets.includes(id))) })}
              onRender={onRender}
            />
          </Panel>
        </div>

        <div className="flex flex-col gap-4 xl:sticky xl:top-0 xl:self-start">
          <Panel title="Checks" description="Music licence, length and loudness, as the renderer found them.">
            {payload.warnings.length === 0 && failingGates.length === 0 ? (
              <p className="flex items-center gap-2 text-[13px] text-ok">
                <Check className="size-4" aria-hidden />
                Nothing to flag.
              </p>
            ) : (
              <ul className="flex flex-col gap-2 text-[13px]">
                {failingGates.map((gate) => (
                  <li key={gate.id} className="flex items-start gap-2">
                    <AlertTriangle className={gate.severity === "block" ? "mt-0.5 size-4 shrink-0 text-fail" : "mt-0.5 size-4 shrink-0 text-warn"} aria-hidden />
                    <span>
                      <span className="font-medium text-ink">{gate.title}</span>
                      {gate.detail ? <span className="text-ink-muted">: {gate.detail}</span> : null}
                      {gate.severity === "warn" ? <span className="text-ink-faint"> (warning)</span> : null}
                    </span>
                  </li>
                ))}
                {payload.warnings.map((warning) => (
                  <li key={warning} className="flex items-start gap-2 text-ink-muted">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
                    <span>{warning}</span>
                  </li>
                ))}
              </ul>
            )}
            {draft.music_path && !musicLicenseOk ? (
              <p className="mt-3 text-xs text-warn">The chosen music has no licence file; the licence check will warn until one is added.</p>
            ) : null}
          </Panel>

          {canApprove ? (
            <Panel title="Approve" description="Approving saves your choices and moves the project on to the export stage.">
              <div className="flex flex-col gap-4">
                <label className="flex flex-col gap-1.5">
                  <span className="text-[13px] font-medium text-ink-muted">Your name</span>
                  <Input value={reviewer.name} onChange={(event) => reviewer.setName(event.target.value)} placeholder={reviewer.effective} autoComplete="name" />
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="text-[13px] font-medium text-ink-muted">Note for the project log (optional)</span>
                  <Textarea rows={2} value={approveNotes} onChange={(event) => setApproveNotes(event.target.value)} placeholder="Anything the team should know" />
                </label>
                <ul className="list-disc space-y-1 pl-4 text-xs text-ink-muted">
                  <li>
                    Export includes {draft.presets.length > 0 ? draft.presets.map(renderPresetLabel).join(", ") : "no final size (tick one above)"}.
                  </li>
                  {missingPresets.length > 0 ? (
                    <li className="text-warn">
                      {missingPresets.map(renderPresetLabel).join(", ")} {missingPresets.length === 1 ? "is" : "are"} not rendered yet; approving renders {missingPresets.length === 1 ? "it" : "them"} first.
                    </li>
                  ) : null}
                  <li>{dirty ? "Your changes to popups, captions, music or sizes are saved into the timeline." : "The timeline is approved as the app built it."}</li>
                  {blockedMessage ? <li className="text-fail">{blockedMessage}</li> : null}
                </ul>
                {showOverride ? (
                  <label className="flex items-start gap-2 text-[13px] text-ink">
                    <input type="checkbox" className="mt-0.5 accent-accent" checked={overrideGates} onChange={(event) => setOverrideGates(event.target.checked)} />
                    <span>
                      Approve anyway: override the blocking checks.
                      <span className="block text-xs text-ink-muted">Use this only when you have read the reasons. The override is written into the project log.</span>
                    </span>
                  </label>
                ) : null}
                <Button variant="primary" icon={<Check />} loading={approve.isPending} disabled={busy || running || draft.presets.length === 0} onClick={onApprove}>
                  {dirty ? "Approve with my changes" : "Approve edit"}
                </Button>
              </div>
            </Panel>
          ) : status === "done" || status === "approved" ? (
            <Notice tone="ok" title="This edit is approved">
              The final sizes above are what the export stage used. Render again to make a new version.
            </Notice>
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={blocker.state === "blocked"}
        title="Leave without saving?"
        description="Your changes to the timeline are not approved yet and will be lost."
        confirmLabel="Leave without saving"
        cancelLabel="Keep editing"
        tone="danger"
        onConfirm={() => {
          if (blocker.state === "blocked") blocker.proceed();
        }}
        onCancel={() => {
          if (blocker.state === "blocked") blocker.reset();
        }}
      />
    </div>
  );
}

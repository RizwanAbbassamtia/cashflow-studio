import { Check, Images, RefreshCw, RotateCcw, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, errorMessage } from "../../api/client";
import { useApproveImages, useImagesPayload, useRedoImages, useUploadSceneImage } from "../../api/images";
import { useProject } from "../../api/projects";
import { useStageProgress } from "../../api/ws";
import { hasAcceptedImage, scenesWithoutImage, type SceneImageRecord } from "../../types/images";
import { ImageSceneCard } from "../images/ImageSceneCard";
import { buildImagesEdits, describeChanges, sceneLabel, sceneNumbers, type PendingUpload } from "../images/imagesUtils";
import { ProgressBar } from "../projects/ProgressBar";
import { formatUsd } from "../projects/stageMeta";
import { Panel } from "../script/Panel";
import { RegenerateDialog } from "../script/RegenerateDialog";
import { useReviewer } from "../script/useReviewer";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Input, Textarea } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../ui/States";
import { useToast } from "../ui/Toast";

export interface ImagesReviewProps {
  projectId: string;
}

/**
 * The images review: a grid of scene cards (picture, QA verdict, attempts, lock) with
 * per-scene "Regenerate with a note" and "Upload image", a tick-all bulk regenerate,
 * progress while the pictures are made, and Approve. Queued changes travel as
 * `edits.regenerate`, `edits.uploads` and `edits.lock`: with Approve they are applied and
 * the project moves on; with "Apply and review again" the stage runs again first. Rendered
 * inside the project page's stage card, which shows status, failed gates and notes above it.
 */
export function ImagesReview({ projectId }: ImagesReviewProps) {
  const { toast } = useToast();
  const reviewer = useReviewer(projectId);
  const project = useProject(projectId);
  const progress = useStageProgress(projectId);
  const { query, payload } = useImagesPayload(projectId);
  const approve = useApproveImages(projectId);
  const redo = useRedoImages(projectId);
  const upload = useUploadSceneImage(projectId);

  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  // Lock toggles made here, on top of what the stage saved.
  const [lockOverrides, setLockOverrides] = useState<ReadonlyMap<number, boolean>>(new Map());
  const [regenerate, setRegenerate] = useState<ReadonlyMap<number, string>>(new Map());
  const [uploads, setUploads] = useState<ReadonlyMap<number, PendingUpload>>(new Map());
  const [regenerateTargets, setRegenerateTargets] = useState<number[] | null>(null);
  const [regenerateAllOpen, setRegenerateAllOpen] = useState(false);
  const [uploadingScene, setUploadingScene] = useState<number | null>(null);
  const [approveNotes, setApproveNotes] = useState("");
  // The server refuses an approval while a blocking check fails; "Approve anyway" overrides.
  const [overrideGates, setOverrideGates] = useState(false);
  const [blockedMessage, setBlockedMessage] = useState<string | null>(null);

  const scenes = useMemo(() => payload?.scenes ?? [], [payload]);

  // Object URLs for upload previews; revoked when dropped and on unmount.
  const previewUrls = useRef(new Set<string>());
  const revoke = useCallback((url: string) => {
    if (previewUrls.current.delete(url)) URL.revokeObjectURL(url);
  }, []);
  useEffect(() => {
    const urls = previewUrls.current;
    return () => {
      for (const url of urls) URL.revokeObjectURL(url);
      urls.clear();
    };
  }, []);

  const clearPending = useCallback(() => {
    setUploads((current) => {
      for (const item of current.values()) revoke(item.previewUrl);
      return new Map();
    });
    setRegenerate(new Map());
    setLockOverrides(new Map());
    setSelected(new Set());
  }, [revoke]);

  // Queued changes belong to the pictures they were made for: a new run clears them.
  const payloadKey = useMemo(() => JSON.stringify(query.data ?? null), [query.data]);
  const lastPayloadKey = useRef(payloadKey);
  useEffect(() => {
    if (payloadKey !== lastPayloadKey.current) {
      lastPayloadKey.current = payloadKey;
      clearPending();
    }
  }, [payloadKey, clearPending]);

  const stageStatus = project.data?.stages.images?.status ?? "pending";
  const running = stageStatus === "running";
  const liveProgress = progress?.stage === "images" ? progress : null;
  const busy = approve.isPending || redo.isPending || upload.isPending;

  const isLocked = useCallback((scene: SceneImageRecord) => lockOverrides.get(scene.scene) ?? scene.locked, [lockOverrides]);
  const lockedScenes = useMemo(() => scenes.filter(isLocked).map((scene) => scene.scene), [scenes, isLocked]);
  const locksChanged = useMemo(() => scenes.some((scene) => lockOverrides.has(scene.scene) && lockOverrides.get(scene.scene) !== scene.locked), [scenes, lockOverrides]);
  const tally = useMemo(
    () => ({
      passed: scenes.filter((scene) => hasAcceptedImage(scene) && scene.source !== "upload").length,
      yours: scenes.filter((scene) => hasAcceptedImage(scene) && scene.source === "upload").length,
      failed: scenes.filter((scene) => scene.status === "rejected").length,
      missing: scenes.filter((scene) => scene.status !== "rejected" && !hasAcceptedImage(scene)).length,
    }),
    [scenes],
  );
  const missing = useMemo(() => scenesWithoutImage(scenes).filter((scene) => !uploads.has(scene.scene) && !regenerate.has(scene.scene)), [scenes, uploads, regenerate]);
  const hasChanges = regenerate.size > 0 || uploads.size > 0 || locksChanged;
  const savedBlocking = payload?.gate_results.some((gate) => gate.severity === "block" && !gate.passed) ?? false;
  const showOverride = savedBlocking || blockedMessage !== null || missing.length > 0;

  const fail = (title: string, error: unknown) => toast({ tone: "error", title, description: errorMessage(error) });
  const by = reviewer.effective;

  const edits = (withOverride: boolean) =>
    buildImagesEdits({ regenerate, uploads, lockedScenes, locksChanged, overrideGates: withOverride && overrideGates && showOverride });

  const onApprove = () => {
    const regenerated = regenerate.size;
    const replaced = uploads.size;
    approve.mutate(
      { by, notes: approveNotes.trim() || undefined, edits: edits(true) },
      {
        onSuccess: () => {
          const parts: string[] = [];
          if (regenerated > 0) parts.push(`${regenerated} scene${regenerated === 1 ? "" : "s"} made again`);
          if (replaced > 0) parts.push(`${replaced} picture${replaced === 1 ? "" : "s"} replaced`);
          toast({ tone: "success", title: "Images approved", description: `${parts.length > 0 ? `${parts.join(", ")} first. ` : ""}The edit stage is next.` });
          setApproveNotes("");
          setOverrideGates(false);
          setBlockedMessage(null);
          clearPending();
        },
        onError: (error) => {
          if (error instanceof ApiError && error.status === 409) {
            setBlockedMessage(errorMessage(error));
            void query.refetch();
          }
          fail("Could not approve the images", error);
        },
      },
    );
  };

  const onApplyAndReview = () => {
    const body = edits(false);
    if (!body) return;
    redo.mutate(
      { by, notes: describeChanges(regenerate, uploads, locksChanged) || "Apply the queued changes.", edits: body },
      {
        onSuccess: () => {
          toast({ tone: "info", title: "Making the pictures again", description: "The grid updates when the images stage is done; locked scenes keep their picture." });
          clearPending();
        },
        onError: (error) => fail("Could not start the regenerate", error),
      },
    );
  };

  const onRegenerateAll = (note: string) => {
    const targets = scenes.filter((scene) => !isLocked(scene)).map((scene) => ({ scene: scene.scene, note }));
    redo.mutate(
      { by, notes: note, edits: { regenerate: targets, lock: lockedScenes } },
      {
        onSuccess: () => {
          setRegenerateAllOpen(false);
          toast({ tone: "info", title: "Making every unlocked picture again", description: `${targets.length} scene${targets.length === 1 ? "" : "s"} go back to the image tool with your note.` });
          clearPending();
        },
        onError: (error) => fail("Could not start the regenerate", error),
      },
    );
  };

  const onRetryMissing = () => {
    const targets = scenesWithoutImage(scenes).map((scene) => scene.scene);
    redo.mutate(
      { by, notes: `Try again for ${sceneNumbers(targets)}, which have no accepted picture.` },
      {
        onSuccess: () => toast({ tone: "info", title: "Trying the missing pictures again", description: "Scenes with an accepted picture are kept." }),
        onError: (error) => fail("Could not start the retry", error),
      },
    );
  };

  const queueRegenerate = (note: string) => {
    if (!regenerateTargets) return;
    setRegenerate((current) => {
      const next = new Map(current);
      for (const scene of regenerateTargets) next.set(scene, note);
      return next;
    });
    setUploads((current) => {
      if (!regenerateTargets.some((scene) => current.has(scene))) return current;
      const next = new Map(current);
      for (const scene of regenerateTargets) {
        const item = next.get(scene);
        if (item) {
          revoke(item.previewUrl);
          next.delete(scene);
        }
      }
      return next;
    });
    setRegenerateTargets(null);
    setSelected(new Set());
  };

  const onUpload = (scene: number, file: File) => {
    setUploadingScene(scene);
    upload.mutate(
      { scene, file },
      {
        onSuccess: ({ path }) => {
          const previewUrl = URL.createObjectURL(file);
          previewUrls.current.add(previewUrl);
          setUploads((current) => {
            const next = new Map(current);
            const previous = next.get(scene);
            if (previous) revoke(previous.previewUrl);
            next.set(scene, { scene, path, name: file.name, previewUrl });
            return next;
          });
          setRegenerate((current) => {
            if (!current.has(scene)) return current;
            const next = new Map(current);
            next.delete(scene);
            return next;
          });
          toast({ tone: "success", title: "Picture uploaded", description: `It replaces ${sceneLabel(scene).toLowerCase()} when you approve or apply the changes.` });
        },
        onError: (error) => fail("Could not upload the picture", error),
        onSettled: () => setUploadingScene(null),
      },
    );
  };

  const clearUpload = (scene: number) => {
    setUploads((current) => {
      const item = current.get(scene);
      if (!item) return current;
      revoke(item.previewUrl);
      const next = new Map(current);
      next.delete(scene);
      return next;
    });
  };

  const clearRegenerate = (scene: number) => {
    setRegenerate((current) => {
      if (!current.has(scene)) return current;
      const next = new Map(current);
      next.delete(scene);
      return next;
    });
  };

  const setLock = (scene: number, locked: boolean) => {
    setLockOverrides((current) => new Map(current).set(scene, locked));
  };

  const toggleSelected = (scene: number, value: boolean) => {
    setSelected((current) => {
      const next = new Set(current);
      if (value) next.add(scene);
      else next.delete(scene);
      return next;
    });
  };

  const allSelected = scenes.length > 0 && selected.size === scenes.length;
  const disabled = busy || running;

  // ---- states --------------------------------------------------------------

  const progressBlock = running ? (
    <Panel title="Making the pictures">
      <ProgressBar value={liveProgress?.pct ?? null} label={liveProgress?.message || "The image tool is working..."} />
    </Panel>
  ) : approve.isPending || redo.isPending ? (
    <Panel title="Working on the pictures">
      <ProgressBar value={liveProgress?.pct ?? null} label={liveProgress?.message || "Please wait..."} />
    </Panel>
  ) : null;

  if (!payload) {
    if (query.isPending) return <LoadingBlock label="Loading the pictures..." />;
    if (query.isError) return <ErrorState error={query.error} title="Could not load the pictures" onRetry={() => void query.refetch()} />;
    return (
      <div className="flex flex-col gap-4">
        {progressBlock}
        {!running ? (
          <EmptyState
            icon={Images}
            title="No pictures to review yet"
            description="The images stage has not written its scene pictures for this project. When it finishes, the scene grid appears here."
            action={
              <Button variant="secondary" size="sm" icon={<RotateCcw />} onClick={() => void query.refetch()}>
                Check again
              </Button>
            }
          />
        ) : null}
      </div>
    );
  }

  const gridClass = payload.aspect === "9:16" ? "grid grid-cols-2 gap-4 lg:grid-cols-3 2xl:grid-cols-4" : "grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3";
  const regenerateTargetLabel = regenerateTargets ? (regenerateTargets.length === 1 ? sceneLabel(regenerateTargets[0] ?? 0) : `${regenerateTargets.length} scenes`) : "";

  return (
    <div className="flex flex-col gap-4">
      {progressBlock}

      <Panel
        title="Scene pictures"
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">
              {scenes.length} scene{scenes.length === 1 ? "" : "s"}
            </Badge>
            <Badge tone="neutral">
              {payload.aspect}
              {payload.size ? ` ${payload.size}` : ""}
            </Badge>
            {tally.passed > 0 ? <Badge tone="ok">{tally.passed} passed</Badge> : null}
            {tally.yours > 0 ? <Badge tone="info">{tally.yours} yours</Badge> : null}
            {tally.failed > 0 ? <Badge tone="fail">{tally.failed} failed</Badge> : null}
            {tally.missing > 0 ? <Badge tone="warn">{tally.missing} missing</Badge> : null}
            {lockedScenes.length > 0 ? <Badge tone="accent">{lockedScenes.length} locked</Badge> : null}
            {payload.provider || payload.model ? (
              <span className="text-xs text-ink-faint">
                Made by {payload.provider || "the image tool"}
                {payload.model ? ` (${payload.model})` : ""}
              </span>
            ) : null}
            {payload.cost_usd + payload.qa_cost_usd > 0 ? (
              <span className="text-xs text-ink-faint">
                {formatUsd(payload.cost_usd)}
                {payload.qa_cost_usd > 0 ? ` + ${formatUsd(payload.qa_cost_usd)} for the check` : ""}
              </span>
            ) : null}
            {payload.budget.monthly_limit !== null || payload.budget.used_this_month + payload.budget.generated_now > 0 ? (
              <span
                className={
                  payload.budget.monthly_limit !== null && payload.budget.used_this_month + payload.budget.generated_now >= payload.budget.monthly_limit
                    ? "text-xs text-warn"
                    : "text-xs text-ink-faint"
                }
              >
                {payload.budget.used_this_month + payload.budget.generated_now}
                {payload.budget.monthly_limit !== null ? ` of ${payload.budget.monthly_limit}` : ""} pictures this month
              </span>
            ) : null}
          </span>
        }
        actions={
          <>
            {scenesWithoutImage(scenes).length > 0 ? (
              <Button variant="secondary" size="sm" icon={<RotateCcw />} onClick={onRetryMissing} disabled={disabled}>
                Retry missing ({scenesWithoutImage(scenes).length})
              </Button>
            ) : null}
            <Button variant="secondary" size="sm" icon={<RefreshCw />} onClick={() => setRegenerateAllOpen(true)} disabled={disabled}>
              Regenerate all
            </Button>
            <Button variant="primary" size="sm" icon={<Check />} loading={approve.isPending} disabled={disabled} onClick={onApprove}>
              {hasChanges ? "Approve with changes" : "Approve"}
            </Button>
          </>
        }
      >
        <p className="text-[13px] text-ink-muted">
          Every scene gets a text-free picture that an AI check compares with the prompt. Regenerate a scene with a note, upload your own
          picture, or lock a picture so a regenerate keeps it. Queued changes are applied when you approve, or run first with &quot;Apply and
          review again&quot;.
        </p>
        {payload.warnings.length > 0 ? (
          <Notice tone="warn" title="Things to check" className="mt-3">
            <ul className="list-disc pl-4">
              {payload.warnings.map((warning, index) => (
                <li key={`${index}-${warning.slice(0, 24)}`}>{warning}</li>
              ))}
            </ul>
          </Notice>
        ) : null}
      </Panel>

      {hasChanges ? (
        <Notice
          tone="info"
          title="Changes waiting"
          action={
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="ghost" size="sm" onClick={clearPending} disabled={disabled}>
                Discard
              </Button>
              {regenerate.size > 0 || uploads.size > 0 ? (
                <Button variant="secondary" size="sm" icon={<Sparkles />} loading={redo.isPending} disabled={disabled} onClick={onApplyAndReview}>
                  Apply and review again
                </Button>
              ) : null}
            </div>
          }
        >
          <ul className="list-disc pl-4">
            {regenerate.size > 0 ? <li>{sceneNumbers(regenerate.keys())} will be made again with your notes.</li> : null}
            {uploads.size > 0 ? <li>Your uploaded picture replaces {sceneNumbers(uploads.keys())}.</li> : null}
            {locksChanged ? <li>Lock changes are saved with the approval.</li> : null}
          </ul>
        </Notice>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-ink-muted">Tick scenes to regenerate several with one note.</p>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="ghost" size="sm" onClick={() => setSelected(allSelected ? new Set() : new Set(scenes.map((scene) => scene.scene)))} disabled={disabled || scenes.length === 0}>
                {allSelected ? "Clear ticks" : "Tick all"}
              </Button>
              <Button variant="secondary" size="sm" icon={<RefreshCw />} onClick={() => setRegenerateTargets([...selected])} disabled={disabled || selected.size === 0}>
                Regenerate ticked{selected.size > 0 ? ` (${selected.size})` : ""}
              </Button>
            </div>
          </div>
          {scenes.length > 0 ? (
            <div className={gridClass}>
              {scenes.map((scene) => (
                <ImageSceneCard
                  key={scene.scene}
                  scene={scene}
                  aspect={payload.aspect}
                  selected={selected.has(scene.scene)}
                  onSelect={(value) => toggleSelected(scene.scene, value)}
                  locked={isLocked(scene)}
                  onLock={(value) => setLock(scene.scene, value)}
                  pendingNote={regenerate.has(scene.scene) ? (regenerate.get(scene.scene) ?? "") : null}
                  pendingUpload={uploads.get(scene.scene) ?? null}
                  uploading={uploadingScene === scene.scene}
                  disabled={disabled}
                  onRegenerate={() => setRegenerateTargets([scene.scene])}
                  onUpload={(file) => onUpload(scene.scene, file)}
                  onClearRegenerate={() => clearRegenerate(scene.scene)}
                  onClearUpload={() => clearUpload(scene.scene)}
                />
              ))}
            </div>
          ) : (
            <p className="text-[13px] text-ink-muted">The images file lists no scenes. Redo the stage after the storyboard is approved.</p>
          )}
        </div>

        <div className="flex flex-col gap-4 xl:sticky xl:top-0 xl:self-start">
          {payload.style_sheet_url ? (
            <Panel title="Style sheet" description="Sent to the image tool with every scene so the pictures match.">
              <img src={payload.style_sheet_url} alt="Style sheet" className="w-full rounded-md border border-line bg-canvas object-cover" loading="lazy" />
            </Panel>
          ) : null}

          <Panel title="Approve" description="Approving applies the queued changes, saves the pictures and moves the project on to the edit stage.">
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
                {missing.length === 0 ? (
                  <li>Every scene has a picture that passed the check or that you supplied.</li>
                ) : (
                  <li className="text-warn">
                    {sceneNumbers(missing.map((scene) => scene.scene))} {missing.length === 1 ? "has" : "have"} no accepted picture; the quality check blocks approval until you regenerate, upload or approve anyway.
                  </li>
                )}
                {regenerate.size > 0 ? (
                  <li>
                    {regenerate.size} scene{regenerate.size === 1 ? " is" : "s are"} made again before the edit stage.
                  </li>
                ) : null}
                {uploads.size > 0 ? (
                  <li>
                    {uploads.size} uploaded picture{uploads.size === 1 ? "" : "s"} replace{uploads.size === 1 ? "s" : ""} the generated one{uploads.size === 1 ? "" : "s"}.
                  </li>
                ) : null}
                {lockedScenes.length > 0 ? (
                  <li>
                    {lockedScenes.length} locked scene{lockedScenes.length === 1 ? " keeps its" : "s keep their"} picture through any regenerate.
                  </li>
                ) : null}
                {selected.size > 0 ? <li className="text-warn">Ticked scenes are only a selection; press &quot;Regenerate ticked&quot; to queue them.</li> : null}
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
              <Button variant="primary" icon={<Check />} loading={approve.isPending} disabled={disabled} onClick={onApprove}>
                {hasChanges ? "Approve with changes" : "Approve images"}
              </Button>
            </div>
          </Panel>
        </div>
      </div>

      <RegenerateDialog
        open={regenerateTargets !== null}
        title={`Regenerate ${regenerateTargetLabel}`}
        description={
          <>
            Your note is added to the prompt when the picture is made again. The change is queued: it runs when you approve, or at once
            with &quot;Apply and review again&quot;.
            {regenerateTargets?.length === 1 && scenes.find((scene) => scene.scene === regenerateTargets[0])?.narration ? (
              <span className="mt-2 block text-xs italic text-ink-faint">&quot;{scenes.find((scene) => scene.scene === regenerateTargets[0])?.narration}&quot;</span>
            ) : null}
          </>
        }
        placeholder="What should be different? For example: wider shot, daylight, no people, warmer colours."
        confirmLabel="Queue regenerate"
        notesOptional
        onConfirm={queueRegenerate}
        onCancel={() => setRegenerateTargets(null)}
      />

      <RegenerateDialog
        open={regenerateAllOpen}
        title="Regenerate every unlocked picture"
        description={
          <>
            Every scene that is not locked goes back to the image tool with your note; locked scenes keep their picture. The stage runs
            again and waits for your review.
            {hasChanges ? <span className="mt-2 block text-warn">The changes waiting below are dropped; apply them first if they matter.</span> : null}
          </>
        }
        placeholder="For example: more muted colours, fewer close-ups, show the city from above."
        confirmLabel="Regenerate all"
        loading={redo.isPending}
        onConfirm={onRegenerateAll}
        onCancel={() => setRegenerateAllOpen(false)}
      />
    </div>
  );
}

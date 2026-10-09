import { useMutation } from "@tanstack/react-query";
import { Check, LayoutGrid, RefreshCw, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { useBlocker, type BlockerFunction } from "react-router";

import { ApiError, errorMessage } from "../../api/client";
import { approveStage, redoStage, useProjectCacheUpdate, useStagePayload } from "../../api/projects";
import { DEFAULT_POPUP_STYLES, parseStoryboardPayload, type SceneLockField, type StoryboardApproveEdits, type StoryboardDoc, type StoryboardScene } from "../../types";
import { Panel } from "../script/Panel";
import { RegenerateDialog } from "../script/RegenerateDialog";
import { useReviewer } from "../script/useReviewer";
import { SceneCard } from "../storyboard/SceneCard";
import { SplitSceneDialog } from "../storyboard/SplitSceneDialog";
import {
  checkVariety,
  deleteScene,
  formatSeconds,
  mergeWithNext,
  moveScene,
  reflowScenes,
  splitScene,
  toggleLock,
  updateScene,
} from "../storyboard/storyboardUtils";
import { VarietyPanel } from "../storyboard/VarietyPanel";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/Dialog";
import { Input, Textarea } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { EmptyState, ErrorState } from "../ui/States";
import { useToast } from "../ui/Toast";

export interface StoryboardBoardProps {
  projectId: string;
}

const FORMAT_LABEL = { long: "Long video", shorts: "Short" } as const;

function withReflow(doc: StoryboardDoc): StoryboardDoc {
  return { ...doc, scenes: reflowScenes(doc.scenes) };
}

/**
 * The Storyboard Board: a grid of scene cards edited in place (narration, image prompt,
 * popup, motion, transition, on-screen text, locks, notes), with split / merge / reorder,
 * per-scene regenerate, a variety check that updates as you edit, and Approve with the
 * edited document as `edits.storyboard`. Edits live in the browser until approved. Rendered
 * inside the project page's stage card, which shows status, failed gates and notes above it.
 */
export function StoryboardBoard({ projectId }: StoryboardBoardProps) {
  const { toast } = useToast();
  const reviewer = useReviewer(projectId);
  const updateCache = useProjectCacheUpdate();

  const payloadQuery = useStagePayload<unknown>(projectId, "storyboard");
  const payload = useMemo(() => parseStoryboardPayload(payloadQuery.data), [payloadQuery.data]);
  const saved = payload?.storyboard ?? null;

  // The draft follows the saved document whenever the server sends a different one.
  const [draft, setDraft] = useState<StoryboardDoc | null>(null);
  const savedKey = useMemo(() => (saved ? JSON.stringify(withReflow(saved)) : ""), [saved]);
  const lastSavedKey = useRef("");
  useEffect(() => {
    if (savedKey !== lastSavedKey.current) {
      lastSavedKey.current = savedKey;
      setDraft(saved ? withReflow(saved) : null);
    }
  }, [saved, savedKey]);

  const draftKey = useMemo(() => (draft ? JSON.stringify(draft) : ""), [draft]);
  const dirty = Boolean(draft && draftKey !== savedKey);

  // Unsaved-changes guard: in-app navigation and closing the window.
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  const blocker = useBlocker(
    useCallback<BlockerFunction>(
      ({ currentLocation, nextLocation }) => dirtyRef.current && currentLocation.pathname !== nextLocation.pathname,
      [],
    ),
  );
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  // Dialogs and transient UI state.
  const [splitIndex, setSplitIndex] = useState<number | null>(null);
  const [regenerateIndex, setRegenerateIndex] = useState<number | null>(null);
  const [regenerateAllOpen, setRegenerateAllOpen] = useState(false);
  const [deleteIndex, setDeleteIndex] = useState<number | null>(null);
  const [highlighted, setHighlighted] = useState<number | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);
  const [approveNotes, setApproveNotes] = useState("");
  // The server refuses an approval whose edits fail a blocking rule; "Approve anyway" overrides.
  const [overrideGates, setOverrideGates] = useState(false);
  const [blockedMessage, setBlockedMessage] = useState<string | null>(null);

  const transitions = payload?.transitions ?? [];
  const band = payload?.scene_band_s ?? [8, 12];
  const wpm = draft?.speaking_rate_wpm || 150;
  const popupStyles = useMemo(() => {
    const styles = new Set<string>();
    if (draft?.popup_style) styles.add(draft.popup_style);
    for (const style of payload?.popup_styles ?? []) styles.add(style);
    for (const scene of draft?.scenes ?? []) if (scene.popup.style) styles.add(scene.popup.style);
    for (const style of DEFAULT_POPUP_STYLES) styles.add(style);
    return [...styles];
  }, [payload?.popup_styles, draft?.popup_style, draft?.scenes]);

  const check = useMemo(() => (draft ? checkVariety(draft.scenes, band, transitions, draft.style_guide) : null), [draft, band, transitions]);
  const issuesByScene = useMemo(() => {
    const map = new Map<number, string[]>();
    for (const issue of check?.issues ?? []) map.set(issue.index, [...(map.get(issue.index) ?? []), issue.message]);
    return map;
  }, [check]);

  const approve = useMutation({
    mutationFn: (body: { by: string; notes?: string; edits?: StoryboardApproveEdits }) =>
      approveStage(projectId, "storyboard", body as { by: string; notes?: string; edits?: Record<string, unknown> }),
    onSuccess: (project) => {
      toast({ tone: "success", title: "Storyboard approved", description: "The voice stage is next." });
      setApproveNotes("");
      setOverrideGates(false);
      setBlockedMessage(null);
      updateCache(project);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        setBlockedMessage(errorMessage(error));
        void payloadQuery.refetch();
      }
      toast({ tone: "error", title: "Could not approve the storyboard", description: errorMessage(error) });
    },
  });

  const redo = useMutation({
    mutationFn: (notes: string) => redoStage(projectId, "storyboard", { by: reviewer.effective, notes }),
    onSuccess: (project) => {
      setRegenerateIndex(null);
      setRegenerateAllOpen(false);
      toast({
        tone: "info",
        title: "Redoing the storyboard",
        description: "Locked fields on every scene are kept; the rest is planned again with your note. The board updates when the AI is done.",
      });
      updateCache(project);
    },
    onError: (error) => toast({ tone: "error", title: "Could not start the redo", description: errorMessage(error) }),
  });

  const edit = useCallback((updater: (doc: StoryboardDoc) => StoryboardDoc) => {
    setDraft((current) => (current ? updater(current) : current));
  }, []);

  const jumpToScene = (index: number) => {
    document.getElementById(`scene-${index}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlighted(index);
    window.setTimeout(() => setHighlighted((value) => (value === index ? null : value)), 1600);
  };

  if (payloadQuery.isPending) {
    return <LoadingBlock label="Loading the storyboard..." />;
  }

  if (payloadQuery.isError) {
    return <ErrorState error={payloadQuery.error} title="Could not load the storyboard" onRetry={() => void payloadQuery.refetch()} />;
  }

  if (!payload || !draft || !saved || !check) {
    return (
      <EmptyState
        icon={LayoutGrid}
        title="No storyboard to review yet"
        description="The storyboard stage has not written its scenes for this project. When it finishes, the scene cards appear here."
        action={
          <Button variant="secondary" size="sm" icon={<RotateCcw />} onClick={() => void payloadQuery.refetch()}>
            Check again
          </Button>
        }
      />
    );
  }

  const busy = approve.isPending || redo.isPending;
  const totalSeconds = draft.scenes.reduce((sum, scene) => sum + scene.est_duration_s, 0);
  const savedBlocking = saved.gate_results.some((gate) => gate.severity === "block" && !gate.passed);
  const showOverride = savedBlocking || blockedMessage !== null;

  const onApprove = () => {
    const body: { by: string; notes?: string; edits?: StoryboardApproveEdits } = { by: reviewer.effective };
    if (approveNotes.trim()) body.notes = approveNotes.trim();
    if (dirty || (overrideGates && showOverride)) {
      body.edits = { storyboard: { ...withReflow(draft), variety: check.variety } };
      if (overrideGates && showOverride) body.edits.override_gates = true;
    }
    approve.mutate(body);
  };

  const onRegenerateScene = (notes: string) => {
    if (regenerateIndex === null) return;
    const scene = draft.scenes[regenerateIndex];
    const what = notes || "Redo this scene with a fresh image prompt, popup and motion that fit the narration.";
    // The whole storyboard is planned again; the note names the scene (and quotes its
    // narration) so the model knows which one to change. Locked fields everywhere are kept.
    const context = scene ? `\nNarration: ${scene.narration}` : "";
    redo.mutate(`Scene ${regenerateIndex + 1}: ${what}${context}`);
  };

  const onDragOver = (event: DragEvent, index: number) => {
    if (dragIndex === null) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    if (dropIndex !== index) setDropIndex(index);
  };

  const onDrop = (index: number) => {
    if (dragIndex !== null && dragIndex !== index) edit((doc) => moveScene(doc, dragIndex, index));
    setDragIndex(null);
    setDropIndex(null);
  };

  const cards = draft.scenes.map((scene: StoryboardScene, index: number) => (
    <SceneCard
      key={`${scene.sentence_ids.join("+") || "scene"}-${index}`}
      projectId={projectId}
      scene={scene}
      position={index}
      total={draft.scenes.length}
      aspect={draft.aspect}
      band={band}
      transitions={transitions}
      motionPresets={payload.motion_presets}
      popupPositions={payload.popup_positions}
      popupStyles={popupStyles}
      issues={issuesByScene.get(index) ?? []}
      highlighted={highlighted === index}
      dragging={dragIndex === index}
      dropTarget={dropIndex === index && dragIndex !== index}
      onChange={(patch) => edit((doc) => updateScene(doc, index, patch))}
      onLock={(field: SceneLockField, locked) => edit((doc) => toggleLock(doc, index, field, locked))}
      onMove={(to) => edit((doc) => moveScene(doc, index, to))}
      onSplit={() => setSplitIndex(index)}
      onMerge={() => edit((doc) => mergeWithNext(doc, index))}
      onDelete={() => setDeleteIndex(index)}
      onRegenerate={() => setRegenerateIndex(index)}
      onDragStart={() => setDragIndex(index)}
      onDragOver={(event) => onDragOver(event, index)}
      onDrop={() => onDrop(index)}
      onDragEnd={() => {
        setDragIndex(null);
        setDropIndex(null);
      }}
    />
  ));

  const splitTarget = splitIndex !== null ? draft.scenes[splitIndex] : undefined;
  const regenerateTarget = regenerateIndex !== null ? draft.scenes[regenerateIndex] : undefined;

  return (
    <div className="flex flex-col gap-4">
      <Panel
        title="Storyboard"
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">{FORMAT_LABEL[draft.format]}</Badge>
            <Badge tone="neutral">{draft.aspect}</Badge>
            <Badge tone="neutral">
              {draft.scenes.length} scene{draft.scenes.length === 1 ? "" : "s"}
            </Badge>
            <Badge tone="neutral">about {formatSeconds(totalSeconds)}</Badge>
            {draft.model ? <span className="text-xs text-ink-faint">Planned by {draft.model}</span> : null}
            {dirty ? <Badge tone="warn">Unsaved changes</Badge> : null}
          </span>
        }
        actions={
          <>
            {dirty ? (
              <Button variant="ghost" size="sm" icon={<RotateCcw />} onClick={() => setDraft(withReflow(saved))}>
                Discard changes
              </Button>
            ) : null}
            <Button variant="secondary" size="sm" icon={<RefreshCw />} onClick={() => setRegenerateAllOpen(true)} disabled={busy}>
              Regenerate storyboard
            </Button>
            <Button variant="primary" size="sm" icon={<Check />} loading={approve.isPending} disabled={busy} onClick={onApprove}>
              {dirty ? "Approve with my edits" : "Approve"}
            </Button>
          </>
        }
      >
        <p className="text-[13px] text-ink-muted">
          Edit the picture, popup, motion and transition right on the card; the words come from the script stage. Lock a field, then
          approve, to keep it through a regenerate. Drag the handle, or use the arrows, to reorder; split long scenes and merge short
          ones so each runs {band[0]}-{band[1]} seconds. Nothing is saved until you approve.
        </p>
        {draft.style_guide ? (
          <p className="mt-2 text-xs text-ink-faint">
            <span className="font-medium text-ink-muted">Style guide added to every prompt: </span>
            {draft.style_guide}
          </p>
        ) : null}
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">{cards}</div>

        <div className="flex flex-col gap-4 xl:sticky xl:top-0 xl:self-start">
          <Panel title="Variety check" description="Updates as you edit. The app fixes most of these when it writes the storyboard; your edits can bring them back.">
            <VarietyPanel variety={check.variety} savedWarnings={saved.variety.warnings} issues={check.issues} band={band} onJumpToScene={jumpToScene} />
          </Panel>

          <Panel title="Approve" description="Approving saves your edits into storyboard.json and moves the project on to the voice stage.">
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
                <li>{dirty ? "Your edited scenes replace the saved storyboard." : "The storyboard is approved as the AI wrote it."}</li>
                <li>The app re-checks the rules on save and fixes unlocked fields that break them.</li>
                {check.issues.length > 0 ? <li className="text-warn">{check.issues.length} scene warning{check.issues.length === 1 ? "" : "s"} are still open; see the variety check.</li> : null}
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
              <Button variant="primary" icon={<Check />} loading={approve.isPending} disabled={busy} onClick={onApprove}>
                {dirty ? "Approve with my edits" : "Approve storyboard"}
              </Button>
            </div>
          </Panel>
        </div>
      </div>

      <SplitSceneDialog
        open={splitIndex !== null}
        sceneNumber={(splitIndex ?? 0) + 1}
        narration={splitTarget?.narration ?? ""}
        sentenceCount={splitTarget?.sentence_ids.length ?? 0}
        onConfirm={(afterChunk) => {
          if (splitIndex !== null) edit((doc) => splitScene(doc, splitIndex, afterChunk, wpm));
          setSplitIndex(null);
        }}
        onCancel={() => setSplitIndex(null)}
      />

      <RegenerateDialog
        open={regenerateIndex !== null}
        title={`Regenerate scene ${(regenerateIndex ?? 0) + 1}`}
        description={
          <>
            The storyboard is planned again with your note about this scene. Locked fields on every scene stay exactly as they are;
            unlocked fields on the other scenes may change too, so lock (and approve) what you want to keep first.
            {regenerateTarget ? <span className="mt-2 block text-xs italic text-ink-faint">"{regenerateTarget.narration}"</span> : null}
            {dirty ? <span className="mt-2 block text-warn">Your unsaved edits are not sent along and will be replaced. Approve them first if they matter.</span> : null}
          </>
        }
        placeholder="What should be different? For example: show the city at night, no people, calmer movement."
        notesOptional
        loading={redo.isPending}
        onConfirm={onRegenerateScene}
        onCancel={() => setRegenerateIndex(null)}
      />

      <RegenerateDialog
        open={regenerateAllOpen}
        title="Regenerate the whole storyboard"
        description={
          <>
            Every unlocked field on every scene is planned again with your notes. Locked fields stay as they are (lock, then approve,
            to make a lock count).
            {dirty ? <span className="mt-2 block text-warn">Your unsaved edits are not sent along and will be replaced.</span> : null}
          </>
        }
        placeholder="For example: fewer close-ups, more maps and diagrams, popups on every third scene."
        loading={redo.isPending}
        onConfirm={(notes) => redo.mutate(notes)}
        onCancel={() => setRegenerateAllOpen(false)}
      />

      <ConfirmDialog
        open={deleteIndex !== null}
        title={`Remove scene ${(deleteIndex ?? 0) + 1}?`}
        description="The card goes away. Its sentences are still in the script, so when you approve, the app puts them back into a neighbouring scene. To choose where they go, merge instead."
        confirmLabel="Remove scene"
        tone="danger"
        onConfirm={() => {
          if (deleteIndex !== null) edit((doc) => deleteScene(doc, deleteIndex));
          setDeleteIndex(null);
        }}
        onCancel={() => setDeleteIndex(null)}
      />

      <ConfirmDialog
        open={blocker.state === "blocked"}
        title="Leave without saving?"
        description="Your edits to this storyboard are not approved yet and will be lost."
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

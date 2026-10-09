import { useMutation } from "@tanstack/react-query";
import { Check, FileText, Lock, LockOpen, RefreshCw, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useBlocker, type BlockerFunction } from "react-router";

import { ApiError, errorMessage } from "../../api/client";
import { approveStage, redoStage, useProjectCacheUpdate, useStagePayload } from "../../api/projects";
import { parseScriptPayload, type ScriptApproveEdits, type ScriptDoc } from "../../types";
import { OriginalityPanel } from "../script/OriginalityPanel";
import { Panel } from "../script/Panel";
import { RegenerateDialog } from "../script/RegenerateDialog";
import { ScriptSectionEditor } from "../script/ScriptSectionEditor";
import {
  allParagraphs,
  lockedParagraphIds,
  sameLocks,
  sameText,
  scriptToMarkdown,
  scriptWordCount,
  setAllLocks,
  speechIndex,
  updateParagraph,
  wordCountStatus,
} from "../script/scriptUtils";
import { useReviewer } from "../script/useReviewer";
import { WordCountMeter } from "../script/WordCountMeter";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/Dialog";
import { Input, Textarea } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { EmptyState, ErrorState } from "../ui/States";
import { useToast } from "../ui/Toast";

export interface ScriptReviewProps {
  projectId: string;
}

const FORMAT_LABEL = { long: "Long video", shorts: "Short" } as const;

/**
 * Review panel for the script stage: edit paragraphs, lock the ones to keep, read the
 * originality and policy results, regenerate the unlocked paragraphs with notes, or approve
 * (with the edited text and the locks as `edits`). Rendered inside the project page's stage
 * card, which shows the stage status, failed gates and reviewer notes above it.
 */
export function ScriptReview({ projectId }: ScriptReviewProps) {
  const { toast } = useToast();
  const reviewer = useReviewer(projectId);
  const updateCache = useProjectCacheUpdate();

  const payloadQuery = useStagePayload<unknown>(projectId, "script");
  const payload = useMemo(() => parseScriptPayload(payloadQuery.data), [payloadQuery.data]);
  const saved = payload?.script ?? null;

  // The draft follows the saved script whenever the server sends a different one (after a
  // regenerate); edits in progress are kept while the content is unchanged.
  const [draft, setDraft] = useState<ScriptDoc | null>(null);
  const savedKey = useMemo(() => (saved ? JSON.stringify(saved) : ""), [saved]);
  const lastSavedKey = useRef("");
  useEffect(() => {
    if (savedKey !== lastSavedKey.current) {
      lastSavedKey.current = savedKey;
      setDraft(saved);
    }
  }, [saved, savedKey]);

  const textChanged = Boolean(draft && saved && !sameText(draft, saved));
  const locksChanged = Boolean(draft && saved && !sameLocks(draft, saved));
  const dirty = textChanged || locksChanged;

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

  const [regenerateOpen, setRegenerateOpen] = useState(false);
  const [approveNotes, setApproveNotes] = useState("");
  // The server refuses an approval whose edits fail a blocking check (copied text, wrong
  // length...). The reviewer can tick "Approve anyway" to override; that is written to the log.
  const [overrideGates, setOverrideGates] = useState(false);
  const [blockedMessage, setBlockedMessage] = useState<string | null>(null);

  const approve = useMutation({
    mutationFn: (body: { by: string; notes?: string; edits?: ScriptApproveEdits }) =>
      approveStage(projectId, "script", body as { by: string; notes?: string; edits?: Record<string, unknown> }),
    onSuccess: (project) => {
      toast({ tone: "success", title: "Script approved", description: "The storyboard stage starts from this script." });
      setApproveNotes("");
      setOverrideGates(false);
      setBlockedMessage(null);
      updateCache(project);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        // The edits were saved; the check results came back with them.
        setBlockedMessage(errorMessage(error));
        void payloadQuery.refetch();
      }
      toast({ tone: "error", title: "Could not approve the script", description: errorMessage(error) });
    },
  });

  const redo = useMutation({
    mutationFn: ({ notes, edits }: { notes: string; edits?: ScriptApproveEdits }) =>
      redoStage(projectId, "script", { by: reviewer.effective, notes, edits: edits as Record<string, unknown> | undefined }),
    onSuccess: (project, variables) => {
      setRegenerateOpen(false);
      const locked = variables.edits?.locked_paragraph_ids?.length ?? 0;
      toast({
        tone: "info",
        title: "Rewriting the unlocked paragraphs",
        description:
          (locked > 0 ? `${locked} locked paragraph${locked === 1 ? " stays" : "s stay"} word for word. ` : "") +
          "This takes a minute or two. The script here updates when it is done.",
      });
      updateCache(project);
    },
    onError: (error) => toast({ tone: "error", title: "Could not start the rewrite", description: errorMessage(error) }),
  });

  const speech = useMemo(() => speechIndex(payload?.speech ?? null), [payload?.speech]);
  const savedTexts = useMemo(() => {
    const map = new Map<string, string>();
    if (saved) for (const paragraph of allParagraphs(saved)) map.set(paragraph.id, paragraph.text);
    return map;
  }, [saved]);

  if (payloadQuery.isPending) {
    return <LoadingBlock label="Loading the script..." />;
  }

  if (payloadQuery.isError) {
    return <ErrorState error={payloadQuery.error} title="Could not load the script" onRetry={() => void payloadQuery.refetch()} />;
  }

  if (!payload || !draft || !saved) {
    return (
      <EmptyState
        icon={FileText}
        title="No script to review yet"
        description="The script stage has not written a script for this project. When it finishes, the sections appear here."
        action={
          <Button variant="secondary" size="sm" icon={<RotateCcw />} onClick={() => void payloadQuery.refetch()}>
            Check again
          </Button>
        }
      />
    );
  }

  const words = scriptWordCount(draft);
  const wordStatus = wordCountStatus(words, draft.target_words);
  const paragraphs = allParagraphs(draft);
  const lockedIds = lockedParagraphIds(draft);
  const unlockedCount = paragraphs.length - lockedIds.length;
  const busy = approve.isPending || redo.isPending;
  const summary = payload.transcript_summary;
  const savedBlocking = payload.gate_results.some((gate) => gate.severity === "block" && !gate.passed) || payload.originality?.passed === false;
  const showOverride = savedBlocking || blockedMessage !== null;

  const onApprove = () => {
    const edits: ScriptApproveEdits = {};
    if (textChanged) edits.script_md = scriptToMarkdown(draft);
    if (locksChanged || lockedIds.length > 0) edits.locked_paragraph_ids = lockedIds;
    if (overrideGates && showOverride) edits.override_gates = true;
    const body: { by: string; notes?: string; edits?: ScriptApproveEdits } = { by: reviewer.effective };
    if (approveNotes.trim()) body.notes = approveNotes.trim();
    if (Object.keys(edits).length > 0) body.edits = edits;
    approve.mutate(body);
  };

  const onRegenerate = (notes: string) => {
    // The locks travel as stage edits of the redo: the stage keeps exactly these paragraphs
    // word for word and rewrites the rest.
    redo.mutate({ notes, edits: lockedIds.length > 0 ? { locked_paragraph_ids: lockedIds } : undefined });
  };

  return (
    <div className="flex flex-col gap-4">
      <Panel
        title={draft.title || "Script"}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">{FORMAT_LABEL[draft.format]}</Badge>
            {draft.language ? <Badge tone="neutral">{draft.language}</Badge> : null}
            {draft.model ? <span className="text-xs text-ink-faint">Written by {draft.model}</span> : null}
            {draft.framework_source === "file" && draft.framework_name ? <span className="text-xs text-ink-faint">Framework: {draft.framework_name}</span> : null}
            {dirty ? <Badge tone="warn">Unsaved changes</Badge> : null}
          </span>
        }
        actions={
          <>
            {dirty ? (
              <Button variant="ghost" size="sm" icon={<RotateCcw />} onClick={() => setDraft(saved)}>
                Discard changes
              </Button>
            ) : null}
            <Button variant="secondary" size="sm" icon={<RefreshCw />} onClick={() => setRegenerateOpen(true)} disabled={busy}>
              Regenerate unlocked
            </Button>
            <Button variant="primary" size="sm" icon={<Check />} loading={approve.isPending} disabled={busy} onClick={onApprove}>
              {dirty ? "Approve with my edits" : "Approve"}
            </Button>
          </>
        }
      >
        <div className="grid gap-6 md:grid-cols-[1fr_auto]">
          <WordCountMeter status={wordStatus} />
          <dl className="grid grid-cols-3 gap-x-6 text-center md:border-l md:border-line md:pl-6">
            <div>
              <dt className="text-xs text-ink-muted">Sections</dt>
              <dd className="text-lg font-semibold tabular-nums text-ink">{draft.sections.length}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">Paragraphs</dt>
              <dd className="text-lg font-semibold tabular-nums text-ink">{paragraphs.length}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">Locked</dt>
              <dd className="text-lg font-semibold tabular-nums text-accent-text">{lockedIds.length}</dd>
            </div>
          </dl>
        </div>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Panel
          title="Sections"
          description="Edit the text in place. Lock the paragraphs that must stay; a regenerate rewrites only the unlocked ones."
          actions={
            <>
              <Button variant="ghost" size="sm" icon={<Lock />} onClick={() => setDraft(setAllLocks(draft, true))} disabled={unlockedCount === 0}>
                Lock all
              </Button>
              <Button variant="ghost" size="sm" icon={<LockOpen />} onClick={() => setDraft(setAllLocks(draft, false))} disabled={lockedIds.length === 0}>
                Unlock all
              </Button>
            </>
          }
        >
          <div className="flex flex-col gap-8">
            {draft.sections.map((section) => (
              <ScriptSectionEditor
                key={section.id}
                section={section}
                savedTexts={savedTexts}
                speech={speech}
                onTextChange={(paragraphId, text) => setDraft((current) => (current ? updateParagraph(current, paragraphId, { text }) : current))}
                onLockChange={(paragraphId, locked) => setDraft((current) => (current ? updateParagraph(current, paragraphId, { locked }) : current))}
              />
            ))}
          </div>
        </Panel>

        <div className="flex flex-col gap-4 xl:sticky xl:top-0 xl:self-start">
          <Panel title="Originality and policy" description="What the checks found, and what to do about it.">
            <OriginalityPanel originality={payload.originality} gateResults={payload.gate_results} />
          </Panel>

          {summary && summary.beats.length > 0 ? (
            <Panel title="How the competitor video is built" description="Structure only: the AI never reuses its sentences.">
              <ol className="flex flex-col gap-1.5 text-[13px]">
                {summary.beats.map((beat, index) => (
                  <li key={index} className="flex items-start gap-2">
                    <span className="mt-px w-10 shrink-0 text-right text-xs tabular-nums text-ink-faint">{beat.share_percent}%</span>
                    <span>
                      <span className="font-medium text-ink">{beat.name}</span>
                      {beat.purpose ? <span className="text-ink-muted"> - {beat.purpose}</span> : null}
                    </span>
                  </li>
                ))}
              </ol>
              {summary.hook_style || summary.pacing || summary.ending_style ? (
                <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-ink-muted">
                  {summary.hook_style ? (
                    <>
                      <dt className="font-medium text-ink">Hook</dt>
                      <dd>{summary.hook_style}</dd>
                    </>
                  ) : null}
                  {summary.pacing ? (
                    <>
                      <dt className="font-medium text-ink">Pacing</dt>
                      <dd>{summary.pacing}</dd>
                    </>
                  ) : null}
                  {summary.ending_style ? (
                    <>
                      <dt className="font-medium text-ink">Ending</dt>
                      <dd>{summary.ending_style}</dd>
                    </>
                  ) : null}
                </dl>
              ) : null}
            </Panel>
          ) : null}

          <Panel title="Approve" description="Approving moves the project on to the storyboard.">
            <div className="flex flex-col gap-4">
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium text-ink-muted">Your name</span>
                <Input value={reviewer.name} onChange={(event) => reviewer.setName(event.target.value)} placeholder={reviewer.effective} autoComplete="name" />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium text-ink-muted">Note for the project log (optional)</span>
                <Textarea rows={2} value={approveNotes} onChange={(event) => setApproveNotes(event.target.value)} placeholder="Why this version is good to go" />
              </label>
              <ul className="list-disc space-y-1 pl-4 text-xs text-ink-muted">
                <li>{textChanged ? "Your text edits are saved into the script." : "The script text is saved as it is."}</li>
                <li>{lockedIds.length > 0 ? `${lockedIds.length} locked paragraph${lockedIds.length === 1 ? "" : "s"} stay marked as locked.` : "No paragraphs are locked."}</li>
                {wordStatus.position !== 0 && draft.target_words > 0 ? (
                  <li className="text-warn">The length is outside the target band; the app will not approve edited text until it fits, or until you tick "Approve anyway".</li>
                ) : null}
                {blockedMessage ? <li className="text-fail">{blockedMessage}</li> : null}
              </ul>
              {showOverride ? (
                <label className="flex items-start gap-2 text-[13px] text-ink">
                  <input type="checkbox" className="mt-0.5 accent-accent" checked={overrideGates} onChange={(event) => setOverrideGates(event.target.checked)} />
                  <span>
                    Approve anyway: override the blocking checks.
                    <span className="block text-xs text-ink-muted">Use this only when you have read the reasons above. The override is written into the project log.</span>
                  </span>
                </label>
              ) : null}
              <Button variant="primary" icon={<Check />} loading={approve.isPending} disabled={busy} onClick={onApprove}>
                {dirty ? "Approve with my edits" : "Approve script"}
              </Button>
            </div>
          </Panel>
        </div>
      </div>

      <RegenerateDialog
        open={regenerateOpen}
        title="Regenerate the unlocked paragraphs"
        description={
          <>
            {lockedIds.length > 0
              ? `${lockedIds.length} locked paragraph${lockedIds.length === 1 ? " stays" : "s stay"} as ${lockedIds.length === 1 ? "it is" : "they are"}; the other ${unlockedCount} ${unlockedCount === 1 ? "is" : "are"} rewritten with your notes.`
              : "Nothing is locked, so the whole script is rewritten with your notes. Lock the paragraphs you like first if you want to keep them."}
            {textChanged ? " Text you typed into unlocked paragraphs is replaced by the rewrite." : null}
          </>
        }
        loading={redo.isPending}
        onConfirm={onRegenerate}
        onCancel={() => setRegenerateOpen(false)}
      />

      <ConfirmDialog
        open={blocker.state === "blocked"}
        title="Leave without saving?"
        description="Your edits to this script are not approved yet and will be lost."
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

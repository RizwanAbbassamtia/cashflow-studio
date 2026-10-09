import { AlertTriangle, Check, PackageCheck, RefreshCw, RotateCcw, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useChannel } from "../../api/channels";
import { ApiError, errorMessage } from "../../api/client";
import { useEditPayload } from "../../api/edit";
import { useApproveExport, useExportPayload, useRedoExport, type ExportApproveBody } from "../../api/export";
import { useProject } from "../../api/projects";
import { useSettings } from "../../api/settings";
import type { ExportApproveEdits, ExportMetadata, ExportReviewPayload, Provenance, ThumbnailChoice } from "../../types/export";
import { renderPresetLabel } from "../../types/timeline";
import { useUnsavedGuard } from "../edit/useUnsavedGuard";
import { ExportFolderPanel } from "../export/ExportFolderPanel";
import { MetadataEditor, metadataBlockers, metadataWarnings } from "../export/MetadataEditor";
import { ThumbnailPicker } from "../export/ThumbnailPicker";
import { Panel } from "../script/Panel";
import { RegenerateDialog } from "../script/RegenerateDialog";
import { useReviewer } from "../script/useReviewer";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/Dialog";
import { Input, Textarea } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../ui/States";
import { useToast } from "../ui/Toast";

export interface ExportReviewProps {
  projectId: string;
}

interface ExportDraft {
  metadata: ExportMetadata;
  thumbnail_choice: ThumbnailChoice;
  headline: string;
  altered_or_synthetic: boolean;
}

function draftFrom(payload: ExportReviewPayload): ExportDraft {
  const chosen = payload.thumbnails.find((variant) => variant.id === payload.thumbnail_choice);
  return {
    metadata: payload.metadata,
    thumbnail_choice: payload.thumbnail_choice,
    headline: chosen?.headline ?? "",
    altered_or_synthetic: payload.altered_or_synthetic,
  };
}

function countWords(text: string): number {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

function todayStamp(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function ProvenanceSummary({ provenance }: { provenance: Provenance | null }) {
  if (!provenance) return <p className="text-xs text-ink-faint">The provenance record is written on export (provenance.json and provenance.md).</p>;
  const byTool = new Map<string, number>();
  for (const image of provenance.images) {
    const key = [image.provider, image.model].filter(Boolean).join(" / ") || "unknown tool";
    byTool.set(key, (byTool.get(key) ?? 0) + 1);
  }
  const flagged = provenance.images.filter((image) => image.synthid || image.c2pa).length;
  return (
    <ul className="flex flex-col gap-1 text-xs text-ink-muted">
      {[...byTool.entries()].map(([tool, count]) => (
        <li key={tool}>
          {count} picture{count === 1 ? "" : "s"} by <span className="text-ink">{tool}</span>
        </li>
      ))}
      {provenance.images.length > 0 ? <li>{flagged > 0 ? `${flagged} carry a SynthID or C2PA mark.` : "No SynthID or C2PA marks reported."}</li> : null}
      {provenance.voice ? (
        <li>
          Voice by <span className="text-ink">{[provenance.voice.provider, provenance.voice.model].filter(Boolean).join(" / ") || "unknown tool"}</span>
          {provenance.voice.consent_ref ? <span className="text-ink-faint"> (consent: {provenance.voice.consent_ref})</span> : null}
        </li>
      ) : null}
    </ul>
  );
}

/**
 * The Export review: the thumbnail picker with its headline, the title, description, tags,
 * chapters and pinned comment, the disclosure flag and the export folder. Approving is the
 * export: the files are copied to the folder with the edited metadata. Rendered inside the
 * project page's stage card, which shows status, failed gates and notes above it.
 */
export function ExportReview({ projectId }: ExportReviewProps) {
  const { toast } = useToast();
  const reviewer = useReviewer(projectId);
  const project = useProject(projectId);
  const channel = useChannel(project.data?.channel_slug);
  const settings = useSettings();
  const { query, payload } = useExportPayload(projectId);
  const edit = useEditPayload(projectId);
  const approve = useApproveExport(projectId);
  const redo = useRedoExport(projectId);

  const status = project.data?.stages.export?.status ?? "pending";
  const running = status === "running";
  const canApprove = status === "awaiting_review" || status === "failed";
  const canRedo = canApprove || status === "done";
  const finished = status === "done" || status === "approved";

  const savedDraft = useMemo(() => (payload ? draftFrom(payload) : null), [payload]);
  const savedKey = useMemo(() => (savedDraft ? JSON.stringify(savedDraft) : ""), [savedDraft]);
  const [draft, setDraft] = useState<ExportDraft | null>(null);
  const lastSavedKey = useRef("");
  useEffect(() => {
    if (savedKey !== lastSavedKey.current) {
      lastSavedKey.current = savedKey;
      setDraft(savedDraft);
    }
  }, [savedDraft, savedKey]);
  const dirty = Boolean(draft && savedDraft && JSON.stringify(draft) !== savedKey);
  const blocker = useUnsavedGuard(dirty);

  const [approveNotes, setApproveNotes] = useState("");
  const [overrideGates, setOverrideGates] = useState(false);
  const [blockedMessage, setBlockedMessage] = useState<string | null>(null);
  const [redoOpen, setRedoOpen] = useState(false);

  if (query.isPending) {
    return <LoadingBlock label="Loading the export..." />;
  }

  if (query.isError) {
    return <ErrorState error={query.error} title="Could not load the export" onRetry={() => void query.refetch()} />;
  }

  if (!payload || !draft || !savedDraft) {
    return (
      <EmptyState
        icon={PackageCheck}
        title={running ? "Preparing the export..." : "Nothing to export yet"}
        description={
          running
            ? "The export stage is making the thumbnails and writing the title, description and tags. They appear here when it is done."
            : "The export stage has not written anything for this project. When it finishes, the thumbnails, the description and the export folder appear here."
        }
        action={
          <Button variant="secondary" size="sm" icon={<RotateCcw />} onClick={() => void query.refetch()}>
            Check again
          </Button>
        }
      />
    );
  }

  const busy = approve.isPending || redo.isPending;
  const locked = running || busy || !canApprove;
  const durationS = edit.payload?.summary.duration_s ?? null;
  const blockers = metadataBlockers(draft.metadata);
  const maxWords = payload.max_headline_words ?? channel.data?.thumbnail.max_headline_words ?? null;
  const headlineWords = countWords(draft.headline);
  if (maxWords !== null && headlineWords > maxWords) blockers.push(`The thumbnail headline has ${headlineWords} words; the channel allows ${maxWords}.`);
  const warnings = [...payload.warnings, ...metadataWarnings(draft.metadata, durationS)];
  if (payload.missing_presets.length > 0) {
    warnings.unshift(
      `No final render for ${payload.missing_presets.map(renderPresetLabel).join(", ")}. Go back to the edit stage and render ${payload.missing_presets.length === 1 ? "it" : "them"}, or the export will be refused.`,
    );
  }
  if (payload.title_promise_early === false) {
    warnings.unshift(`The title's promise does not come up early in the script.${payload.title_promise_note ? ` ${payload.title_promise_note}` : ""}`);
  }
  const failingGates = payload.gate_results.filter((gate) => !gate.passed);
  const savedBlocking = failingGates.some((gate) => gate.severity === "block");
  const showOverride = savedBlocking || blockedMessage !== null || status === "failed";
  const presets = payload.presets.length > 0 ? payload.presets : (edit.payload?.presets ?? []);

  const exportBase = channel.data?.channel.export_folder?.trim() || settings.data?.exports_dir || null;
  const projectData = project.data;
  const plannedFolder =
    exportBase && projectData ? `${exportBase.replace(/[\\/]+$/, "")}\\${projectData.channel_slug}\\${todayStamp()}_${projectData.topic_slug || "video"}` : null;

  const patch = (changes: Partial<ExportDraft>) => setDraft((current) => (current ? { ...current, ...changes } : current));

  const chooseThumbnail = (id: ThumbnailChoice) => {
    const variant = payload.thumbnails.find((item) => item.id === id);
    const savedChoice = savedDraft.thumbnail_choice;
    // Switching variants resets the headline box to that variant's text, unless the box was edited.
    const headlineUntouched = draft.headline.trim() === (payload.thumbnails.find((item) => item.id === draft.thumbnail_choice)?.headline ?? "").trim();
    patch({ thumbnail_choice: id, headline: headlineUntouched || id === savedChoice ? (variant?.headline ?? "") : draft.headline });
  };

  const onApprove = () => {
    if (blockers.length > 0) {
      toast({ tone: "error", title: "Fix the highlighted fields first", description: blockers[0] });
      return;
    }
    const body: ExportApproveBody = { by: reviewer.effective };
    if (approveNotes.trim()) body.notes = approveNotes.trim();
    const edits: ExportApproveEdits = {};
    if (JSON.stringify(draft.metadata) !== JSON.stringify(savedDraft.metadata)) edits.metadata = draft.metadata;
    if (draft.thumbnail_choice !== savedDraft.thumbnail_choice) edits.thumbnail_choice = draft.thumbnail_choice;
    if (draft.altered_or_synthetic !== savedDraft.altered_or_synthetic) edits.altered_or_synthetic = draft.altered_or_synthetic;
    const chosenHeadline = payload.thumbnails.find((item) => item.id === draft.thumbnail_choice)?.headline ?? "";
    if (draft.headline.trim() && draft.headline.trim() !== chosenHeadline.trim()) edits.headline = draft.headline.trim();
    if (overrideGates && showOverride) edits.override_gates = true;
    if (Object.keys(edits).length > 0) body.edits = edits;
    approve.mutate(body, {
      onSuccess: () => {
        setApproveNotes("");
        setOverrideGates(false);
        setBlockedMessage(null);
        toast({ tone: "success", title: "Export approved", description: "The video, thumbnails and metadata are being copied to the export folder." });
      },
      onError: (error) => {
        if (error instanceof ApiError && error.status === 409) {
          setBlockedMessage(errorMessage(error));
          void query.refetch();
        }
        toast({ tone: "error", title: "Could not export", description: errorMessage(error) });
      },
    });
  };

  const onRedo = (notes: string) => {
    redo.mutate(
      { by: reviewer.effective, notes },
      {
        onSuccess: () => {
          setRedoOpen(false);
          toast({ tone: "info", title: "Redoing the export pack", description: "New thumbnails and a new description are on the way. The panel refreshes when they are done." });
        },
        onError: (error) => toast({ tone: "error", title: "Could not start the redo", description: errorMessage(error) }),
      },
    );
  };

  return (
    <div className="flex flex-col gap-4">
      <Panel
        title="Thumbnail"
        description={
          <span className="flex flex-wrap items-center gap-2">
            <span>Pick the picture YouTube shows. The headline is drawn on it by the app.</span>
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
            {canRedo ? (
              <Button variant="secondary" size="sm" icon={<RefreshCw />} onClick={() => setRedoOpen(true)} disabled={busy || running}>
                Redo with notes
              </Button>
            ) : null}
            {canApprove ? (
              <Button variant="primary" size="sm" icon={<PackageCheck />} loading={approve.isPending} disabled={locked || blockers.length > 0} onClick={onApprove}>
                Approve and export
              </Button>
            ) : null}
          </>
        }
      >
        <ThumbnailPicker
          projectId={projectId}
          variants={payload.thumbnails}
          choice={draft.thumbnail_choice}
          onChoose={chooseThumbnail}
          headline={draft.headline}
          originalHeadline={payload.thumbnails.find((item) => item.id === draft.thumbnail_choice)?.headline ?? ""}
          onHeadlineChange={(headline) => patch({ headline })}
          maxWords={maxWords}
          disabled={locked}
        />
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Panel title="Title, description and tags" description="Written by the app in the channel's language from the script and the voice timing. Edit anything before it goes out.">
          <MetadataEditor value={draft.metadata} onChange={(metadata) => patch({ metadata })} durationS={durationS} disabled={locked} />
        </Panel>

        <div className="flex flex-col gap-4 xl:sticky xl:top-0 xl:self-start">
          <Panel title="Disclosure" description="YouTube asks whether a video has altered or synthetic content.">
            <div className="flex flex-col gap-3">
              <label className="flex items-start gap-3">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4 accent-accent"
                  checked={draft.altered_or_synthetic}
                  disabled={locked}
                  onChange={(event) => patch({ altered_or_synthetic: event.target.checked })}
                />
                <span>
                  <span className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
                    <ShieldCheck className="size-3.5 text-ink-faint" aria-hidden />
                    Mark as altered or synthetic content
                  </span>
                  <span className="block text-xs text-ink-muted">On by default: the pictures and the narration are made with AI tools. Written into metadata.json for the upload.</span>
                </span>
              </label>
              {!draft.altered_or_synthetic ? (
                <Notice tone="warn">Turning this off while AI pictures or a cloned voice are used can get the video removed. Leave it on unless you know why.</Notice>
              ) : null}
              <ProvenanceSummary provenance={payload.provenance} />
              {payload.provenance_url || payload.provenance_md_url ? (
                <p className="flex flex-wrap gap-3 text-xs">
                  {payload.provenance_md_url ? (
                    <a href={payload.provenance_md_url} target="_blank" rel="noreferrer" className="font-medium text-accent-text hover:underline">
                      Provenance record
                    </a>
                  ) : null}
                  {payload.provenance_url ? (
                    <a href={payload.provenance_url} target="_blank" rel="noreferrer" className="font-medium text-accent-text hover:underline">
                      provenance.json
                    </a>
                  ) : null}
                </p>
              ) : null}
            </div>
          </Panel>

          <Panel title="Export folder" description="Where the finished files go.">
            <ExportFolderPanel
              projectId={projectId}
              folder={payload.export_folder}
              plannedFolder={plannedFolder}
              exported={payload.exported || finished}
              files={payload.files}
              presets={presets}
              topicSlug={projectData?.topic_slug ?? ""}
            />
          </Panel>

          {blockers.length > 0 || warnings.length > 0 || failingGates.length > 0 ? (
            <Panel title="Checks">
              <ul className="flex flex-col gap-2 text-[13px]">
                {blockers.map((issue) => (
                  <li key={issue} className="flex items-start gap-2">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0 text-fail" aria-hidden />
                    <span className="text-ink">{issue}</span>
                  </li>
                ))}
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
                {warnings.map((warning) => (
                  <li key={warning} className="flex items-start gap-2 text-ink-muted">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
                    <span>{warning}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}

          {canApprove ? (
            <Panel title="Approve and export" description="Copies the final video, both thumbnails and metadata.json into the export folder.">
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
                  <li>{presets.length > 0 ? `Exports the ${presets.map(renderPresetLabel).join(" and ")} video${presets.length === 1 ? "" : "s"}.` : "Exports the rendered video."}</li>
                  <li>{dirty ? "Your edits replace the app's title, description, tags and thumbnail choice." : "The pack goes out as the app wrote it."}</li>
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
                <Button variant="primary" icon={<PackageCheck />} loading={approve.isPending} disabled={locked || blockers.length > 0} onClick={onApprove}>
                  {dirty ? "Approve with my edits and export" : "Approve and export"}
                </Button>
              </div>
            </Panel>
          ) : finished ? (
            <Notice tone="ok" title="This video is exported">
              <span className="flex items-center gap-1.5">
                <Check className="size-3.5" aria-hidden />
                The files are in the export folder above. Redo with notes makes a fresh pack.
              </span>
            </Notice>
          ) : null}
        </div>
      </div>

      <RegenerateDialog
        open={redoOpen}
        title="Redo the thumbnails and description"
        description={
          <>
            The export stage runs again with your notes: new thumbnail variants and a new title, description, tags and chapters.
            {dirty ? <span className="mt-2 block text-warn">Your unsaved edits are not sent along and will be replaced.</span> : null}
          </>
        }
        placeholder="For example: a bolder headline, mention the price in the description, fewer tags."
        confirmLabel="Redo"
        loading={redo.isPending}
        onConfirm={onRedo}
        onCancel={() => setRedoOpen(false)}
      />

      <ConfirmDialog
        open={blocker.state === "blocked"}
        title="Leave without saving?"
        description="Your edits to the thumbnail, title or description are not approved yet and will be lost."
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

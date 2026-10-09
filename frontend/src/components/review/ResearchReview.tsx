import { Check, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { useChannel } from "../../api/channels";
import { errorMessage } from "../../api/client";
import { useApproveStage, useProject, useRedoStage, useStagePayload } from "../../api/projects";
import { formatRelative } from "../../lib/format";
import { findPickVideoId, type Candidate, type ResearchApproveEdits, type ResearchReviewPayload } from "../../types/research";
import { NotesDialog } from "../projects/NotesDialog";
import { useReviewerName } from "../projects/useReviewerName";
import { CandidatesTable } from "../research/CandidatesTable";
import { Button } from "../ui/Button";
import { LoadingBlock } from "../ui/Spinner";
import { ErrorState, Notice } from "../ui/States";
import { useToast } from "../ui/Toast";

export interface ResearchReviewProps {
  projectId: string;
}

/**
 * Review of the research stage: the ranked candidates with the AI pick highlighted. The
 * reviewer can keep the pick, choose another candidate (approve with edits `{video_id}`), or
 * rescan: the competitors are listed again and the AI proposes a video it has not picked for
 * this project before.
 */
export function ResearchReview({ projectId }: ResearchReviewProps) {
  const { toast } = useToast();
  const project = useProject(projectId);
  const channel = useChannel(project.data?.channel_slug);
  const { effective: by } = useReviewerName(channel.data?.reviewer ?? "");
  const status = project.data?.stages.research.status ?? "pending";
  const hasRun = status !== "pending" && status !== "running";
  const payload = useStagePayload<ResearchReviewPayload>(projectId, "research", hasRun);
  const approve = useApproveStage();
  const redo = useRedoStage();

  const [selected, setSelected] = useState<string | null>(null);
  const [showExcluded, setShowExcluded] = useState(false);
  const [redoOpen, setRedoOpen] = useState(false);

  const pickId = findPickVideoId(payload.data);
  // The project's own source wins once a pick was made by hand.
  const sourceId = project.data?.source.kind === "manual_pick" ? (project.data.source.video_id ?? null) : null;
  const effectivePick = sourceId ?? pickId;

  useEffect(() => {
    setSelected(null);
  }, [payload.data]);

  if (!hasRun) {
    return <Notice tone="info">Research has not produced candidates yet. This panel fills in as soon as the scan finishes.</Notice>;
  }
  if (payload.isPending) return <LoadingBlock label="Loading candidates..." />;
  if (payload.isError) {
    return <ErrorState error={payload.error} title="Could not load the candidates" onRetry={() => void payload.refetch()} />;
  }

  // The engine answers {} when the stage wrote nothing yet.
  const candidates: Candidate[] | null = Array.isArray(payload.data.candidates) ? payload.data.candidates : null;
  if (candidates === null) {
    return (
      <Notice tone="warn" title="No candidates saved for this project">
        {status === "failed"
          ? "The scan did not finish. Use Redo with notes to try again."
          : status === "awaiting_manual"
            ? "Research is waiting for files you provide; nothing was scanned by the app."
            : "The research files are missing from the project folder."}
      </Notice>
    );
  }

  const chosenId = selected ?? effectivePick;
  const chosen = candidates.find((candidate) => candidate.video_id === chosenId) ?? null;
  const changed = chosenId !== null && chosenId !== effectivePick;
  const canAct = status === "awaiting_review";
  const excludedCount = candidates.filter((candidate) => candidate.excluded_reason).length;
  const competitorCount = payload.data.channels?.length ?? 0;

  const onApprove = () => {
    const edits: ResearchApproveEdits | undefined = changed && chosenId ? { video_id: chosenId } : undefined;
    approve.mutate(
      { id: projectId, stage: "research", body: { by, edits: edits as Record<string, unknown> | undefined } },
      {
        onSuccess: () =>
          toast({
            tone: "success",
            title: changed ? "Pick changed and approved" : "AI pick approved",
            description: chosen ? `Production continues from "${chosen.title}".` : "The title stage runs next.",
          }),
        onError: (error) => toast({ tone: "error", title: "Could not approve research", description: errorMessage(error) }),
      },
    );
  };

  const onRedo = (notes: string) => {
    redo.mutate(
      { id: projectId, stage: "research", body: { by, notes } },
      {
        onSuccess: () => {
          setRedoOpen(false);
          toast({
            tone: "success",
            title: "Scanning again",
            description: "The competitors are listed afresh and the AI proposes a different video. This panel updates when it is done.",
          });
        },
        onError: (error) => toast({ tone: "error", title: "Could not redo research", description: errorMessage(error) }),
      },
    );
  };

  return (
    <div className="flex flex-col gap-4">
      {payload.data.scanned_at || competitorCount > 0 ? (
        <p className="text-xs text-ink-muted">
          {competitorCount > 0 ? `${competitorCount} competitors scanned together` : "Scan"}
          {payload.data.scanned_at ? ` - ${formatRelative(payload.data.scanned_at)}` : ""}
          {payload.data.transcript_available === false && chosen ? " - no transcript was available for the pick" : ""}
        </p>
      ) : null}

      {candidates.length === 0 ? (
        <Notice tone="warn" title="No candidates passed the filters">
          Every video from the competitors was too new, the wrong length, or already used. Add more competitors or try the other format.
        </Notice>
      ) : (
        <div className="overflow-hidden rounded-card border border-line">
          <CandidatesTable
            candidates={candidates}
            pickVideoId={effectivePick}
            selectedVideoId={chosenId}
            onUse={canAct ? (candidate) => setSelected(candidate.video_id) : undefined}
            useLabel="Use this one"
            disabled={approve.isPending || redo.isPending}
            showExcluded={showExcluded}
          />
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3 text-xs text-ink-muted">
          <span>{candidates.length - excludedCount} candidates ranked across all competitors</span>
          {excludedCount > 0 ? (
            <label className="inline-flex cursor-pointer items-center gap-1.5">
              <input type="checkbox" className="accent-accent" checked={showExcluded} onChange={(event) => setShowExcluded(event.target.checked)} />
              Show the {excludedCount} left out
            </label>
          ) : null}
        </div>
        {canAct ? (
          <div className="flex flex-wrap items-center gap-2">
            {changed ? (
              <Button variant="ghost" size="sm" onClick={() => setSelected(null)} disabled={approve.isPending}>
                Back to the AI pick
              </Button>
            ) : null}
            <Button variant="secondary" size="sm" icon={<RotateCcw />} onClick={() => setRedoOpen(true)} disabled={approve.isPending || redo.isPending}>
              Rescan and pick again
            </Button>
            <Button variant="primary" size="sm" icon={<Check />} onClick={onApprove} loading={approve.isPending} disabled={!chosenId || redo.isPending}>
              {changed ? "Use my pick and continue" : "Approve the AI pick and continue"}
            </Button>
          </div>
        ) : chosen ? (
          <p className="text-xs text-ink-muted">
            Production started from <span className="font-medium text-ink">{chosen.title}</span>.
          </p>
        ) : null}
      </div>

      <NotesDialog
        open={redoOpen}
        title="Rescan and pick again"
        description="The competitors are listed again and the AI proposes a video it has not picked for this project before. To choose a video yourself, press 'Use this one' on a row instead. Your note is kept in the project log."
        confirmLabel="Rescan"
        placeholder="Optional: why you want a different pick."
        loading={redo.isPending}
        onConfirm={onRedo}
        onCancel={() => setRedoOpen(false)}
      />
    </div>
  );
}

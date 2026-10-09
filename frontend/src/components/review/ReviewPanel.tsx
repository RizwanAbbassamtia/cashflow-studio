import { FolderOpen } from "lucide-react";

import { useChannel } from "../../api/channels";
import { useProject } from "../../api/projects";
import { useStageProgress } from "../../api/ws";
import type { StageName } from "../../types/channel";
import type { Project } from "../../types/project";
import { ProgressBar } from "../projects/ProgressBar";
import { StageActions } from "../projects/StageActions";
import { LATER_STAGES, STAGE_DESCRIPTIONS, STAGE_FILES, STAGE_FOLDERS, STAGE_LABELS } from "../projects/stageMeta";
import { StageStatusBadge } from "../projects/StageStatusBadge";
import { useReviewerName } from "../projects/useReviewerName";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { LoadingBlock } from "../ui/Spinner";
import { ErrorState, Notice } from "../ui/States";
import { ResearchReview } from "./ResearchReview";
import { ScriptReview } from "./ScriptReview";
import { StoryboardBoard } from "./StoryboardBoard";
import { TitleReview } from "./TitleReview";

export interface ReviewPanelProps {
  projectId: string;
  /** the stage to show; usually the project's current stage */
  stage: StageName;
}

/** Stages whose panel carries its own approve and redo buttons while a review is open. */
const STAGES_WITH_OWN_CONTROLS: readonly StageName[] = ["research", "title", "script", "storyboard"];

/** Shown while a stage waits for files from a person (mode "manual", or no runner yet). */
function ManualWaitNotice({ project, stage }: { project: Project; stage: StageName }) {
  const later = LATER_STAGES.includes(stage);
  const folder = `${project.folder}\\${STAGE_FOLDERS[stage]}`;
  const files = STAGE_FILES[stage];
  return (
    <div className="flex flex-col gap-2">
      <Notice tone="warn" title={`${STAGE_LABELS[stage]} waits for your files`}>
        {later
          ? `The automatic ${STAGE_LABELS[stage].toLowerCase()} stage arrives in a later update. `
          : project.stage_modes[stage] === "manual"
            ? "This stage is set to Manual, so the AI does not run it. "
            : "The AI did not run this stage. "}
        Put {files.length === 1 ? files[0] : `these files: ${files.join(", ")}`} into the folder below, then press
        &quot;Files are in place, continue&quot;. The pipeline is paused here until you do.
      </Notice>
      <p className="flex items-start gap-2 text-xs text-ink-muted">
        <FolderOpen className="mt-0.5 size-3.5 shrink-0 text-ink-faint" aria-hidden />
        <span className="select-all break-all font-mono">{folder}</span>
      </p>
    </div>
  );
}

function StageBody({ project, stage }: { project: Project; stage: StageName }) {
  const status = project.stages[stage]?.status ?? "pending";
  if (status === "skipped") {
    return (
      <Notice tone="info" title={`${STAGE_LABELS[stage]} was skipped`}>
        {stage === "research" && project.source.kind === "own_topic"
          ? `This video starts from your own topic: "${project.source.topic_text ?? ""}".`
          : (project.stages[stage]?.notes.at(-1) ?? "Nothing to review here.")}
      </Notice>
    );
  }
  switch (stage) {
    case "research":
      return <ResearchReview projectId={project.id} />;
    case "title":
      return <TitleReview projectId={project.id} />;
    case "script":
      return <ScriptReview projectId={project.id} />;
    case "storyboard":
      return <StoryboardBoard projectId={project.id} />;
    default:
      return null;
  }
}

/**
 * The review panel for one stage of a project: status, summary, checks, notes, the
 * stage-specific body (candidates, title variants, script editor, storyboard board) and the
 * generic actions that the body does not provide itself.
 */
export function ReviewPanel({ projectId, stage }: ReviewPanelProps) {
  const project = useProject(projectId);
  const channel = useChannel(project.data?.channel_slug);
  const { effective: by } = useReviewerName(channel.data?.reviewer ?? "");
  const progress = useStageProgress(projectId);

  if (project.isPending) {
    return (
      <Card>
        <LoadingBlock label="Loading project..." />
      </Card>
    );
  }
  if (project.isError) {
    return (
      <Card>
        <ErrorState error={project.error} title="Could not load the project" onRetry={() => void project.refetch()} />
      </Card>
    );
  }

  const data = project.data;
  const state = data.stages[stage];
  const status = state?.status ?? "pending";
  const ownControls = STAGES_WITH_OWN_CONTROLS.includes(stage) && status === "awaiting_review";
  const liveProgress = status === "running" && progress?.stage === stage ? progress : null;
  const failedGates = state?.gate_results.filter((gate) => !gate.passed) ?? [];
  const history = state?.history ?? [];

  const actions =
    status === "running" ? null : status === "pending" ? (
      stage === data.current_stage ? <StageActions project={data} stage={stage} by={by} showApprove={false} showRedo={false} /> : null
    ) : (
      <StageActions
        project={data}
        stage={stage}
        by={by}
        showApprove={!ownControls && status !== "skipped"}
        showRedo={!ownControls}
        approveLabel={status === "awaiting_manual" ? "Files are in place, continue" : undefined}
      />
    );

  return (
    <Card
      title={
        <span className="inline-flex flex-wrap items-center gap-2">
          {STAGE_LABELS[stage]}
          <StageStatusBadge status={status} />
          {data.stage_modes[stage] === "manual" ? <Badge tone="neutral">Manual stage</Badge> : null}
          {data.stage_modes[stage] === "auto" ? <Badge tone="neutral">Runs on its own</Badge> : null}
        </span>
      }
      description={state?.summary || STAGE_DESCRIPTIONS[stage]}
      actions={actions}
    >
      <div className="flex flex-col gap-4">
        {status === "running" ? (
          <ProgressBar value={liveProgress?.pct ?? null} label={liveProgress?.message || `${STAGE_LABELS[stage]} is running...`} />
        ) : null}
        {status === "failed" && state?.error ? (
          <Notice tone="fail" title="This stage failed">
            {state.error}
          </Notice>
        ) : null}
        {status === "awaiting_manual" ? <ManualWaitNotice project={data} stage={stage} /> : null}
        {failedGates.length > 0 ? (
          <Notice tone={failedGates.some((gate) => gate.severity === "block") ? "fail" : "warn"} title="Checks that did not pass">
            <ul className="list-disc pl-4">
              {failedGates.map((gate) => (
                <li key={gate.id}>
                  <span className="font-medium text-ink">{gate.title}</span>
                  {gate.detail ? `: ${gate.detail}` : ""}
                  {gate.severity === "warn" ? " (warning)" : ""}
                </li>
              ))}
            </ul>
          </Notice>
        ) : null}
        {state && state.notes.length > 0 ? (
          <div className="rounded-md border border-line bg-surface-2/40 px-4 py-3 text-[13px]">
            <p className="font-medium text-ink">Reviewer notes</p>
            <ul className="mt-1 list-disc pl-4 text-ink-muted">
              {state.notes.map((note, index) => (
                <li key={`${index}-${note.slice(0, 20)}`}>{note}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {status === "pending" ? (
          <Notice tone="info">
            {stage === data.current_stage ? "This stage is next in line. It starts as soon as the pipeline runs." : "This stage has not started yet."}
          </Notice>
        ) : (
          <StageBody project={data} stage={stage} />
        )}
        {history.length > 0 ? (
          <details className="text-xs text-ink-muted">
            <summary className="cursor-pointer select-none font-medium text-ink-muted hover:text-ink">What happened in this stage ({history.length})</summary>
            <ul className="mt-2 flex flex-col gap-1 border-l border-line pl-3 font-mono text-[11px]">
              {history.map((line, index) => (
                <li key={`${index}-${line.slice(0, 24)}`}>{line}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </Card>
  );
}

import { Archive, ExternalLink, Play, SearchX } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { useChannel } from "../api/channels";
import { ApiError, errorMessage } from "../api/client";
import { useArchiveProject, useProject, useRunProject, useSetStageMode } from "../api/projects";
import { useProjectEvents } from "../api/ws";
import { CostsCard } from "../components/projects/CostsCard";
import { FolderCard } from "../components/projects/FolderCard";
import { LiveIndicator } from "../components/projects/LiveIndicator";
import { FORMAT_SHORT_LABELS, SOURCE_KIND_LABELS, STAGE_LABELS } from "../components/projects/stageMeta";
import { StageStatusBadge } from "../components/projects/StageStatusBadge";
import { StageTimeline } from "../components/projects/StageTimeline";
import { useReviewerName } from "../components/projects/useReviewerName";
import { ReviewPanel } from "../components/review/ReviewPanel";
import { Badge } from "../components/ui/Badge";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/Dialog";
import { Input } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { LoadingBlock } from "../components/ui/Spinner";
import { EmptyState, ErrorState } from "../components/ui/States";
import { useToast } from "../components/ui/Toast";
import { formatDateTime, formatRelative } from "../lib/format";
import type { StageMode, StageName } from "../types/channel";
import { isProjectFinished, projectStatus, type Project } from "../types/project";

function DetailsCard({ project, channelName }: { project: Project; channelName: string }) {
  const source = project.source;
  return (
    <Card title="Details">
      <dl className="grid grid-cols-[110px_1fr] gap-x-4 gap-y-2 text-[13px]">
        <dt className="text-ink-muted">Channel</dt>
        <dd>
          <Link to={`/channels/${encodeURIComponent(project.channel_slug)}`} className="font-medium text-ink hover:underline">
            {channelName}
          </Link>
        </dd>
        <dt className="text-ink-muted">Format</dt>
        <dd className="text-ink">{FORMAT_SHORT_LABELS[project.format]}</dd>
        <dt className="text-ink-muted">Language</dt>
        <dd className="text-ink">{project.language}</dd>
        <dt className="text-ink-muted">Started from</dt>
        <dd className="min-w-0 text-ink">
          {SOURCE_KIND_LABELS[source.kind]}
          {source.kind === "own_topic" && source.topic_text ? <p className="mt-0.5 text-ink-muted">{source.topic_text}</p> : null}
          {source.video_url ? (
            <a href={source.video_url} target="_blank" rel="noreferrer" className="mt-0.5 flex items-center gap-1 truncate text-accent-text hover:underline">
              Competitor video
              <ExternalLink className="size-3" aria-hidden />
            </a>
          ) : source.video_id ? (
            <p className="mt-0.5 font-mono text-xs text-ink-muted">{source.video_id}</p>
          ) : null}
        </dd>
        <dt className="text-ink-muted">Created</dt>
        <dd className="text-ink" title={formatDateTime(project.created_at)}>
          {formatRelative(project.created_at)}
        </dd>
        <dt className="text-ink-muted">Updated</dt>
        <dd className="text-ink" title={formatDateTime(project.updated_at)}>
          {formatRelative(project.updated_at)}
        </dd>
        <dt className="text-ink-muted">Id</dt>
        <dd className="select-all truncate font-mono text-xs text-ink-faint" title={project.id}>
          {project.id}
        </dd>
      </dl>
    </Card>
  );
}

/** /projects/:id - stage timeline, costs, folder and the review panel for the current gate. */
export function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();
  const project = useProject(id);
  const channel = useChannel(project.data?.channel_slug);
  const events = useProjectEvents();
  const setMode = useSetStageMode();
  const run = useRunProject();
  const archive = useArchiveProject();
  const { name, setName, effective } = useReviewerName(channel.data?.reviewer ?? "");

  const [selectedStage, setSelectedStage] = useState<StageName | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [modeChanging, setModeChanging] = useState<StageName | null>(null);

  // Follow the pipeline: when it moves to a new stage, show that stage's panel again.
  const lastCurrent = useRef<StageName | null>(null);
  useEffect(() => {
    const current = project.data?.current_stage ?? null;
    if (current && lastCurrent.current && current !== lastCurrent.current) setSelectedStage(null);
    lastCurrent.current = current;
  }, [project.data?.current_stage]);

  if (!id) return null;

  if (project.isPending) {
    return (
      <Card>
        <LoadingBlock label="Loading project..." />
      </Card>
    );
  }

  if (project.isError) {
    const notFound = project.error instanceof ApiError && project.error.status === 404;
    return (
      <Card>
        {notFound ? (
          <EmptyState
            icon={SearchX}
            title="This project does not exist"
            description="It may have been archived. Archived folders live in _archived inside your projects folder."
            action={
              <LinkButton to="/projects" variant="secondary">
                Back to projects
              </LinkButton>
            }
          />
        ) : (
          <ErrorState error={project.error} title="Could not load the project" onRetry={() => void project.refetch()} />
        )}
      </Card>
    );
  }

  const data = project.data;
  const stage = selectedStage ?? data.current_stage;
  const status = projectStatus(data);
  const finished = isProjectFinished({ current_stage: data.current_stage, status });
  const canResume = !finished && (status === "pending" || status === "failed" || status === "awaiting_manual");
  const channelName = channel.data?.channel.name ?? data.channel_slug;
  const title = data.title || data.source.topic_text || "Untitled video";

  const onModeChange = (target: StageName, mode: StageMode) => {
    if (data.stage_modes[target] === mode) return;
    setModeChanging(target);
    setMode.mutate(
      { id: data.id, stage: target, mode },
      {
        onSuccess: () => toast({ tone: "success", title: `${STAGE_LABELS[target]} set to ${mode}` }),
        onError: (error) => toast({ tone: "error", title: "Could not change the mode", description: errorMessage(error) }),
        onSettled: () => setModeChanging(null),
      },
    );
  };

  const onArchive = () => {
    archive.mutate(data.id, {
      onSuccess: () => {
        setArchiveOpen(false);
        toast({ tone: "success", title: "Project archived", description: "The folder moved to _archived inside your projects folder." });
        navigate("/projects", { replace: true });
      },
      onError: (error) => toast({ tone: "error", title: "Could not archive the project", description: errorMessage(error) }),
    });
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={title}
        actions={
          <>
            <LiveIndicator state={events.state} />
            <label className="flex items-center gap-2 text-xs text-ink-muted">
              Reviewing as
              <Input value={name} onChange={(event) => setName(event.target.value)} placeholder={effective} className="h-8 w-32 text-xs" aria-label="Your name for approvals" />
            </label>
            {canResume ? (
              <Button
                variant="secondary"
                size="sm"
                icon={<Play />}
                loading={run.isPending}
                onClick={() =>
                  run.mutate(data.id, {
                    onSuccess: (response) =>
                      toast({ tone: "info", title: response.started ? "Pipeline resumed" : "Nothing to resume yet", description: response.message }),
                    onError: (error) => toast({ tone: "error", title: "Could not resume", description: errorMessage(error) }),
                  })
                }
              >
                Resume
              </Button>
            ) : null}
            <Button variant="ghost" size="sm" icon={<Archive />} onClick={() => setArchiveOpen(true)}>
              Archive
            </Button>
          </>
        }
      >
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <StageStatusBadge status={status} />
          <Badge tone="neutral">{STAGE_LABELS[data.current_stage]} stage</Badge>
          <Badge tone="neutral">{FORMAT_SHORT_LABELS[data.format]}</Badge>
          <Badge tone="neutral">{data.language}</Badge>
          <Link to={`/channels/${encodeURIComponent(data.channel_slug)}`} className="text-xs text-ink-muted hover:text-ink hover:underline">
            {channelName}
          </Link>
          {finished ? <Badge tone="ok">Finished</Badge> : null}
        </div>
      </PageHeader>

      <div className="grid gap-6 xl:grid-cols-[3fr_2fr]">
        <Card title="Stages" description="Click a stage to see its result. Auto runs on, Review waits for you, Manual waits for your files." flush>
          <StageTimeline project={data} selected={stage} onSelect={setSelectedStage} onModeChange={onModeChange} modeChanging={modeChanging} />
        </Card>
        <div className="flex flex-col gap-6">
          <DetailsCard project={data} channelName={channelName} />
          <CostsCard costs={data.costs} />
          <FolderCard project={data} />
        </div>
      </div>

      <ReviewPanel projectId={data.id} stage={stage} />

      <ConfirmDialog
        open={archiveOpen}
        title="Archive this project?"
        description="The project folder moves to _archived inside your projects folder. Nothing is deleted, but the project leaves the lists."
        confirmLabel="Archive"
        tone="danger"
        loading={archive.isPending}
        onConfirm={onArchive}
        onCancel={() => setArchiveOpen(false)}
      />
    </div>
  );
}

import { ScanSearch, Search, Tv } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";

import { useChannel, useChannels } from "../api/channels";
import { ApiError, errorMessage } from "../api/client";
import { useCreateProject, useJob, jobIsActive } from "../api/projects";
import { useCandidates, useRefreshResearch, useStartScan } from "../api/research";
import { useJobLog, useProjectEvents } from "../api/ws";
import { LiveIndicator } from "../components/projects/LiveIndicator";
import { AiPickCard } from "../components/research/AiPickCard";
import { CandidatesTable } from "../components/research/CandidatesTable";
import { ChannelPicker } from "../components/research/ChannelPicker";
import { CompetitorList } from "../components/research/CompetitorList";
import { FormatToggle } from "../components/research/FormatToggle";
import { OwnTopicForm } from "../components/research/OwnTopicForm";
import { ScanProgress } from "../components/research/ScanProgress";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { LoadingBlock } from "../components/ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../components/ui/States";
import { useToast } from "../components/ui/Toast";
import { formatRelative } from "../lib/format";
import type { ProjectFormat, ProjectSourceInput } from "../types/project";
import { findPickVideoId } from "../types/research";

/**
 * /research/:slug? - scan all competitors of a channel, see their videos ranked as one list,
 * and start production from the AI pick, from any row, or from your own topic.
 */
export function ResearchPage() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const { toast } = useToast();
  const channels = useChannels();
  const channel = useChannel(slug);
  const events = useProjectEvents();

  const [format, setFormat] = useState<ProjectFormat>("long");
  const [showExcluded, setShowExcluded] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);

  const candidates = useCandidates(slug, format);
  const startScan = useStartScan();
  const refreshResearch = useRefreshResearch();
  const create = useCreateProject();
  const jobLog = useJobLog(jobId);

  const job = useJob(jobId, {
    onDone: () => {
      if (slug) refreshResearch(slug);
      toast({ tone: "success", title: "Scan finished", description: "The candidates are ranked and the AI pick is ready." });
    },
    onFailed: (failed) => toast({ tone: "error", title: "The scan stopped", description: failed.error || failed.message || "Try again in a few minutes." }),
  });

  // Only one channel: open it straight away instead of asking.
  useEffect(() => {
    if (!slug && channels.data?.length === 1) {
      navigate(`/research/${encodeURIComponent(channels.data[0]!.slug)}`, { replace: true });
    }
  }, [slug, channels.data, navigate]);

  // Follow the channel's own format when it has only one, once per channel.
  const formatInitFor = useRef<string | null>(null);
  useEffect(() => {
    if (!channel.data || formatInitFor.current === channel.data.slug) return;
    formatInitFor.current = channel.data.slug;
    setFormat(channel.data.channel.formats === "shorts" ? "shorts" : "long");
    setJobId(null);
  }, [channel.data]);

  const scanning = startScan.isPending || (jobId !== null && (job.data === undefined || jobIsActive(job.data)));

  const onScan = (force: boolean) => {
    if (!slug) return;
    startScan.mutate(
      { slug, body: { force } },
      {
        onSuccess: (started) => setJobId(started.job_id),
        onError: (error) => toast({ tone: "error", title: "Could not start the scan", description: errorMessage(error) }),
      },
    );
  };

  const start = (source: ProjectSourceInput, marker: string) => {
    if (!slug) return;
    setStarting(marker);
    create.mutate(
      { channel_slug: slug, format, source },
      {
        onSuccess: (project) => {
          toast({ tone: "success", title: "Production started", description: project.title ? `"${project.title}"` : "Follow the stages on the project page." });
          navigate(`/projects/${encodeURIComponent(project.id)}`);
        },
        onError: (error) => toast({ tone: "error", title: "Could not start production", description: errorMessage(error) }),
        onSettled: () => setStarting(null),
      },
    );
  };

  const data = candidates.data;
  const pickId = findPickVideoId(data);
  // The server's pick may rank below the rows shown (the picker prefers fresh videos in the
  // channel's language), so prefer the full candidate the server sends.
  const pick = data?.pick ?? data?.candidates.find((candidate) => candidate.video_id === pickId) ?? null;
  const notScannedYet = candidates.isError && candidates.error instanceof ApiError && candidates.error.status === 404;
  const competitors = channel.data?.competitors ?? [];
  const excludedCount = data?.candidates.filter((candidate) => candidate.excluded_reason).length ?? 0;
  const rankedCount = (data?.candidates.length ?? 0) - excludedCount;

  const picker = <ChannelPicker channels={channels.data ?? []} value={slug} onChange={(next) => navigate(`/research/${encodeURIComponent(next)}`)} className="w-72" />;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Research"
        description="Scan every competitor of a channel, rank their videos as one list and start production from the best one."
        actions={
          <>
            <LiveIndicator state={events.state} />
            {picker}
          </>
        }
      />

      {!slug ? (
        <Card>
          {channels.isPending ? (
            <LoadingBlock label="Loading channels..." />
          ) : channels.isError ? (
            <ErrorState error={channels.error} title="Could not load the channels" onRetry={() => void channels.refetch()} />
          ) : channels.data.length === 0 ? (
            <EmptyState
              icon={Tv}
              title="Create a channel first"
              description="Research works per channel: it scans that channel's competitors. Set one up with at least one competitor."
              action={
                <LinkButton to="/channels/new" variant="primary">
                  New channel
                </LinkButton>
              }
            />
          ) : (
            <EmptyState icon={Search} title="Choose a channel to research" description="Pick one of your channels above. Its competitors are scanned together." action={picker} />
          )}
        </Card>
      ) : channel.isPending ? (
        <Card>
          <LoadingBlock label="Loading channel..." />
        </Card>
      ) : channel.isError ? (
        <Card>
          <ErrorState error={channel.error} title="Could not load the channel" onRetry={() => void channel.refetch()} />
        </Card>
      ) : (
        <div className="grid gap-6 xl:grid-cols-[3fr_2fr]">
          <div className="flex min-w-0 flex-col gap-6">
            {jobId ? (
              <Card>
                <ScanProgress job={job.data} liveMessage={jobLog?.message} onDismiss={() => setJobId(null)} />
              </Card>
            ) : null}

            {pick && !scanning ? (
              <AiPickCard pick={pick} format={format} onStart={() => start({ kind: "ai_pick" }, "ai")} starting={starting === "ai"} disabled={create.isPending && starting !== "ai"} />
            ) : null}

            <Card
              title="Candidates"
              description={
                data?.scanned_at
                  ? `${rankedCount} videos ranked across ${data.channels.length || competitors.length} competitors by how far they beat their own channel's usual views. Scanned ${formatRelative(data.scanned_at)}.`
                  : "Videos from all competitors, ranked by how far each one beats its own channel's usual views."
              }
              actions={
                <>
                  {excludedCount > 0 ? (
                    <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-ink-muted">
                      <input type="checkbox" className="accent-accent" checked={showExcluded} onChange={(event) => setShowExcluded(event.target.checked)} />
                      Show {excludedCount} left out
                    </label>
                  ) : null}
                  <FormatToggle value={format} onChange={setFormat} />
                </>
              }
              flush
            >
              {competitors.length === 0 ? (
                <EmptyState
                  icon={Tv}
                  title="This channel has no competitors yet"
                  description="Add the channels you want to learn from in the channel setup, then scan them here."
                  action={
                    <LinkButton to={`/channels/${encodeURIComponent(slug)}`} variant="primary" size="sm">
                      Open channel setup
                    </LinkButton>
                  }
                />
              ) : candidates.isPending ? (
                <LoadingBlock label="Loading candidates..." />
              ) : notScannedYet || (candidates.isSuccess && !data?.scanned_at && data?.candidates.length === 0) ? (
                <EmptyState
                  icon={ScanSearch}
                  title="No scan yet"
                  description={`Press Scan now to list the videos of all ${competitors.length} competitors. The first scan of a channel takes a few minutes.`}
                  action={
                    <Button variant="primary" size="sm" icon={<ScanSearch />} loading={scanning} onClick={() => onScan(false)}>
                      Scan now
                    </Button>
                  }
                />
              ) : candidates.isError ? (
                <ErrorState error={candidates.error} title="Could not load the candidates" onRetry={() => void candidates.refetch()} />
              ) : data && data.candidates.length === 0 ? (
                <EmptyState
                  icon={Search}
                  title={`No ${format === "shorts" ? "Shorts" : "long videos"} found`}
                  description="The scan found nothing in this format that passes the filters. Try the other format or rescan."
                />
              ) : data ? (
                <CandidatesTable
                  candidates={data.candidates}
                  pickVideoId={pickId}
                  onUse={(candidate) => start({ kind: "manual_pick", video_id: candidate.video_id }, candidate.video_id)}
                  useLabel="Use this one"
                  busyVideoId={starting && starting !== "ai" && starting !== "topic" ? starting : null}
                  disabled={create.isPending}
                  showExcluded={showExcluded}
                />
              ) : null}
            </Card>

            {data && data.channels.some((row) => row.error) ? (
              <Notice tone="warn" title="Some competitors could not be scanned">
                YouTube sometimes asks to slow down. The ranking uses what was fetched; try Rescan all later.
              </Notice>
            ) : null}
          </div>

          <div className="flex min-w-0 flex-col gap-6">
            <CompetitorList channelSlug={slug} competitors={competitors} scanned={data?.channels ?? []} scannedAt={data?.scanned_at} scanning={scanning} onScan={onScan} />
            <OwnTopicForm format={format} onSubmit={(topic) => start({ kind: "own_topic", topic_text: topic }, "topic")} submitting={starting === "topic"} disabled={create.isPending && starting !== "topic"} />
          </div>
        </div>
      )}
    </div>
  );
}

import { ListChecks, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { useChannels } from "../api/channels";
import { useProjects } from "../api/projects";
import { useProjectEvents } from "../api/ws";
import { LiveIndicator } from "../components/projects/LiveIndicator";
import { matchesFilters, ProjectFilters, type ProjectFiltersValue } from "../components/projects/ProjectFilters";
import { ProjectsTable } from "../components/projects/ProjectsTable";
import { STAGE_STATUS_LABELS } from "../components/projects/stageMeta";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { LoadingBlock } from "../components/ui/Spinner";
import { EmptyState, ErrorState } from "../components/ui/States";
import { cn } from "../lib/cn";
import { WAITING_STATUSES, type StageStatus } from "../types/project";

const QUEUE_STATUS_OPTIONS = [
  { value: "awaiting_review", label: STAGE_STATUS_LABELS.awaiting_review },
  { value: "awaiting_manual", label: STAGE_STATUS_LABELS.awaiting_manual },
  { value: "failed", label: STAGE_STATUS_LABELS.failed },
  { value: "", label: "Everything waiting for a person" },
] as const;

const CHIP_TONES: Record<string, string> = {
  awaiting_review: "border-accent/40 bg-accent/10 text-accent-text",
  awaiting_manual: "border-warn/30 bg-warn/10 text-warn",
  failed: "border-fail/30 bg-fail/10 text-fail",
};

/** /review - every project that waits for a person, across all channels. */
export function ReviewQueuePage() {
  const channels = useChannels();
  const projects = useProjects();
  const events = useProjectEvents();
  const [filters, setFilters] = useState<ProjectFiltersValue>({ channel: "", stage: "", status: "awaiting_review", search: "" });

  const waiting = useMemo(() => (projects.data ?? []).filter((project) => WAITING_STATUSES.includes(project.status)), [projects.data]);

  const rows = useMemo(
    () => waiting.filter((project) => matchesFilters(project, filters)).sort((a, b) => a.updated_at.localeCompare(b.updated_at)),
    [waiting, filters],
  );

  const counts = useMemo(() => {
    const result: Partial<Record<StageStatus, number>> = {};
    for (const project of waiting) result[project.status] = (result[project.status] ?? 0) + 1;
    return result;
  }, [waiting]);

  const selectedLabel = QUEUE_STATUS_OPTIONS.find((option) => option.value === filters.status)?.label ?? "";

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Review"
        description="Everything waiting for a person, oldest first. Open a row to approve, change or redo the stage."
        actions={<LiveIndicator state={events.state} />}
      >
        <div className="mt-3 flex flex-wrap gap-2">
          {QUEUE_STATUS_OPTIONS.filter((option) => option.value !== "").map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setFilters((current) => ({ ...current, status: current.status === option.value ? "" : option.value }))}
              aria-pressed={filters.status === option.value}
              className={cn(
                "inline-flex h-7 items-center gap-2 rounded-full border px-3 text-xs font-medium transition-colors",
                filters.status === option.value ? CHIP_TONES[option.value] : "border-line bg-surface-2 text-ink-muted hover:text-ink",
              )}
            >
              {option.label}
              <span className="tabular-nums">{counts[option.value as StageStatus] ?? 0}</span>
            </button>
          ))}
        </div>
      </PageHeader>

      <ProjectFilters value={filters} onChange={setFilters} channels={channels.data ?? []} statusOptions={QUEUE_STATUS_OPTIONS} />

      <Card flush>
        {projects.isPending ? (
          <LoadingBlock label="Loading the queue..." />
        ) : projects.isError ? (
          <ErrorState error={projects.error} title="Could not load the queue" onRetry={() => void projects.refetch()} />
        ) : waiting.length === 0 ? (
          <EmptyState icon={ListChecks} title="Nothing waits for you" description="Stages set to Review show up here as soon as they finish running." />
        ) : rows.length === 0 ? (
          <EmptyState icon={Search} title={`Nothing ${selectedLabel ? `is ${selectedLabel.toLowerCase()}` : "matches"}`} description="Try another status, channel or stage." />
        ) : (
          <ProjectsTable projects={rows} channels={channels.data ?? []} showCost={false} />
        )}
      </Card>
    </div>
  );
}

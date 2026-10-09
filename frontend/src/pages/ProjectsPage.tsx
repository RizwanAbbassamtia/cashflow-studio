import { Film, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useChannels } from "../api/channels";
import { useProjects } from "../api/projects";
import { useProjectEvents } from "../api/ws";
import { LiveIndicator } from "../components/projects/LiveIndicator";
import { matchesFilters, ProjectFilters, type ProjectFiltersValue } from "../components/projects/ProjectFilters";
import { ProjectsTable } from "../components/projects/ProjectsTable";
import { STAGE_LABELS } from "../components/projects/stageMeta";
import { LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { LoadingBlock } from "../components/ui/Spinner";
import { EmptyState, ErrorState } from "../components/ui/States";
import { STAGE_NAMES, type StageName } from "../types/channel";

export interface ProjectsPageProps {
  /** when set, only projects currently at this stage are listed */
  stageFilter?: string;
}

function asStage(value: string | undefined): "" | StageName {
  return value && (STAGE_NAMES as readonly string[]).includes(value) ? (value as StageName) : "";
}

/** /projects - every video in production with its channel, stage, status and cost. */
export function ProjectsPage({ stageFilter }: ProjectsPageProps) {
  const channels = useChannels();
  const projects = useProjects();
  const events = useProjectEvents();
  const lockedStage = asStage(stageFilter);

  const [filters, setFilters] = useState<ProjectFiltersValue>({ channel: "", stage: lockedStage, status: "", search: "" });

  useEffect(() => {
    setFilters((current) => (current.stage === lockedStage ? current : { ...current, stage: lockedStage }));
  }, [lockedStage]);

  const rows = useMemo(() => {
    const list = projects.data ?? [];
    return list.filter((project) => matchesFilters(project, filters)).sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }, [projects.data, filters]);

  const total = projects.data?.length ?? 0;
  const title = lockedStage ? STAGE_LABELS[lockedStage] : "Projects";

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={title}
        description={
          lockedStage
            ? `Videos that are at the ${STAGE_LABELS[lockedStage].toLowerCase()} stage right now. Open one to review it.`
            : "Every video in production. Open a row to see its stages, costs, folder and what needs your approval."
        }
        actions={
          <>
            <LiveIndicator state={events.state} />
            <LinkButton to="/research" variant="primary" icon={<Search />}>
              New video from research
            </LinkButton>
          </>
        }
      />

      <ProjectFilters value={filters} onChange={setFilters} channels={channels.data ?? []} stageLocked={Boolean(lockedStage)} />

      <Card flush>
        {projects.isPending ? (
          <LoadingBlock label="Loading projects..." />
        ) : projects.isError ? (
          <ErrorState error={projects.error} title="Could not load the projects" onRetry={() => void projects.refetch()} />
        ) : total === 0 ? (
          <EmptyState
            icon={Film}
            title="No videos in production yet"
            description="Run research on a channel and start production from the AI pick, from any candidate, or from your own topic."
            action={
              <LinkButton to="/research" variant="primary" size="sm" icon={<Search />}>
                Go to Research
              </LinkButton>
            }
          />
        ) : rows.length === 0 ? (
          <EmptyState icon={Search} title="No projects match" description="Nothing matches these filters. Clear one of them to see more." />
        ) : (
          <>
            <ProjectsTable projects={rows} channels={channels.data ?? []} />
            <p className="border-t border-line px-5 py-2.5 text-xs text-ink-faint">
              {rows.length === total ? `${total} project${total === 1 ? "" : "s"}` : `${rows.length} of ${total} projects`}
            </p>
          </>
        )}
      </Card>
    </div>
  );
}

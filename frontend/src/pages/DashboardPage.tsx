import { Activity, ArrowRight, Clapperboard, FolderOpen, ListChecks, PackageCheck, Plus, Search, Tv } from "lucide-react";
import { Link } from "react-router";

import { useChannels } from "../api/channels";
import { useDoctor } from "../api/doctor";
import { useProjects } from "../api/projects";
import { useSettings } from "../api/settings";
import { useSystemInfo } from "../api/system";
import { useProjectEvents } from "../api/ws";
import { GetStartedChecklist, type ChecklistStep } from "../components/dashboard/GetStartedChecklist";
import { StatCard } from "../components/dashboard/StatCard";
import { STAGE_LABELS } from "../components/projects/stageMeta";
import { StageStatusBadge } from "../components/projects/StageStatusBadge";
import { ChannelStatusBadge } from "../components/ui/Badge";
import { LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Notice } from "../components/ui/States";
import { summarizeDoctor } from "../layout/DoctorPill";
import { formatRelative, plural } from "../lib/format";
import { isProjectFinished, WAITING_STATUSES } from "../types/project";

export function DashboardPage() {
  const channels = useChannels();
  const projects = useProjects();
  const settings = useSettings();
  const doctor = useDoctor();
  const system = useSystemInfo();
  useProjectEvents();

  const channelList = channels.data ?? [];
  const projectList = projects.data ?? [];
  const activeCount = channelList.filter((c) => c.status === "active").length;
  const keysSet = settings.data ? Object.values(settings.data.keys).filter((k) => k.set).length : 0;
  const anthropicSet = Boolean(settings.data?.keys.ANTHROPIC_API_KEY?.set);
  const health = summarizeDoctor(doctor.data, doctor.error);

  const finished = projectList.filter((p) => isProjectFinished(p));
  const inProgress = projectList.filter((p) => !isProjectFinished(p));
  const awaitingReview = projectList.filter((p) => p.status === "awaiting_review");
  const waiting = projectList
    .filter((p) => WAITING_STATUSES.includes(p.status))
    .sort((a, b) => a.updated_at.localeCompare(b.updated_at))
    .slice(0, 5);

  const settingsDone = settings.isSuccess && keysSet > 0;
  const channelsDone = channels.isSuccess && channelList.length > 0;
  const researchDone = projects.isSuccess && projectList.length > 0;

  const steps: ChecklistStep[] = [
    {
      id: "settings",
      title: "Add your API keys and folders",
      detail: settingsDone
        ? `${plural(keysSet, "key")} set${anthropicSet ? "" : ", but no Anthropic key yet"}${system.data?.shared_dir_is_default ? ". The shared folder still uses the default on this computer." : "."}`
        : "Set at least the Anthropic key, then point the shared folder at your synced Google Drive folder.",
      state: settingsDone ? "done" : "todo",
      action: (
        <LinkButton to="/settings" variant={settingsDone ? "secondary" : "primary"} size="sm" icon={<ArrowRight />}>
          Open Settings
        </LinkButton>
      ),
    },
    {
      id: "channel",
      title: "Create your first channel",
      detail: channelsDone
        ? `${plural(channelList.length, "channel")} set up. Open one to adjust competitors, voice or images.`
        : "Name, competitors, frameworks, voice, images, thumbnail rules and stage modes.",
      state: channelsDone ? "done" : "todo",
      action: (
        <LinkButton to="/channels/new" variant={channelsDone || !settingsDone ? "secondary" : "primary"} size="sm" icon={<Plus />}>
          New channel
        </LinkButton>
      ),
    },
    {
      id: "research",
      title: "Run research on a channel",
      detail: researchDone
        ? `${plural(projectList.length, "video")} started so far. Scan again whenever you want fresh ideas.`
        : "Scan the competitors for outlier videos and start production from the AI pick.",
      state: researchDone ? "done" : "todo",
      action: (
        <LinkButton to="/research" variant={researchDone || !channelsDone ? "secondary" : "primary"} size="sm" icon={<Search />}>
          Open Research
        </LinkButton>
      ),
    },
  ];

  const recent = [...channelList]
    .sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""))
    .slice(0, 5);

  const channelName = (slug: string) => channelList.find((c) => c.slug === slug)?.name ?? slug;

  return (
    <div className="flex flex-col gap-6">
      {channels.isError ? (
        <Notice tone="fail" title="The server is not answering">
          Start the backend (cfs serve) and reload. The pages keep working once it is up.
        </Notice>
      ) : null}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
        <StatCard
          label="Channels"
          icon={Tv}
          to="/channels"
          value={channels.isPending ? "..." : channels.isError ? "-" : channelList.length}
          hint={channels.isSuccess ? `${activeCount} active` : "Open the channel list"}
        />
        <StatCard
          label="Videos in progress"
          icon={Clapperboard}
          to="/projects"
          value={projects.isPending ? "..." : projects.isError ? "-" : inProgress.length}
          tone={inProgress.length > 0 ? "default" : "muted"}
          hint={projects.isSuccess ? (inProgress.length > 0 ? `${inProgress.filter((p) => p.status === "running").length} running right now` : "Start one from Research") : "Open the project list"}
        />
        <StatCard
          label="Awaiting review"
          icon={ListChecks}
          to="/review"
          value={projects.isPending ? "..." : projects.isError ? "-" : awaitingReview.length}
          tone={awaitingReview.length > 0 ? "warn" : "muted"}
          hint={awaitingReview.length > 0 ? "Open the Review queue" : "Nothing waits for you"}
        />
        <StatCard label="Exports" icon={PackageCheck} to="/projects" value={projects.isPending ? "..." : finished.length} tone="muted" hint="Rendering and export arrive in M4" />
        <StatCard
          label="Health checks"
          icon={Activity}
          to="/settings#doctor"
          value={health.label}
          tone={health.level === "pending" ? "muted" : health.level}
          hint={doctor.data ? `${doctor.data.checks.length} checks on this computer` : "Open the Doctor panel"}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[3fr_2fr]">
        <div className="flex flex-col gap-6">
          <GetStartedChecklist steps={steps} />

          <Card
            title="Waiting for you"
            description="Stages that stopped for a review, a file or after a problem. Oldest first."
            actions={
              <Link to="/review" className="text-xs font-medium text-accent-text hover:underline">
                Open the queue
              </Link>
            }
            flush
          >
            {waiting.length === 0 ? (
              <p className="px-5 py-6 text-center text-[13px] text-ink-faint">{projects.isPending ? "Loading..." : "Nothing waits for you right now."}</p>
            ) : (
              <ul className="divide-y divide-line">
                {waiting.map((project) => (
                  <li key={project.id}>
                    <Link to={`/projects/${encodeURIComponent(project.id)}`} className="flex items-center justify-between gap-3 px-5 py-3 transition-colors hover:bg-surface-2/60">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-ink">{project.title || "Untitled video"}</div>
                        <div className="text-xs text-ink-faint">
                          {channelName(project.channel_slug)} - {STAGE_LABELS[project.current_stage]} - {formatRelative(project.updated_at)}
                        </div>
                      </div>
                      <StageStatusBadge status={project.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="flex flex-col gap-6">
          <Card title="Your folders" description="From Settings. Shared data syncs through Google Drive; videos stay local.">
            {system.data ? (
              <dl className="flex flex-col gap-3 text-[13px]">
                {[
                  { label: "Shared folder", value: system.data.shared_dir, note: system.data.shared_dir_is_default ? "default, not shared yet" : "" },
                  { label: "Projects", value: system.data.projects_dir, note: "" },
                  { label: "Exports", value: system.data.exports_dir, note: "" },
                ].map((row) => (
                  <div key={row.label} className="flex items-start gap-3">
                    <FolderOpen className="mt-0.5 size-4 shrink-0 text-ink-faint" aria-hidden />
                    <div className="min-w-0">
                      <dt className="text-ink-muted">
                        {row.label}
                        {row.note ? <span className="ml-2 text-warn">{row.note}</span> : null}
                      </dt>
                      <dd className="break-all font-mono text-xs text-ink">{row.value}</dd>
                    </div>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-[13px] text-ink-faint">{system.isError ? "Not available while the server is offline." : "Loading..."}</p>
            )}
          </Card>

          <Card
            title="Recent channels"
            actions={
              <Link to="/channels" className="text-xs font-medium text-accent-text hover:underline">
                See all
              </Link>
            }
            flush
          >
            {recent.length === 0 ? (
              <p className="px-5 py-6 text-center text-[13px] text-ink-faint">
                {channels.isPending ? "Loading..." : "No channels yet."}
              </p>
            ) : (
              <ul className="divide-y divide-line">
                {recent.map((channel) => (
                  <li key={channel.slug}>
                    <Link to={`/channels/${encodeURIComponent(channel.slug)}`} className="flex items-center justify-between gap-3 px-5 py-3 transition-colors hover:bg-surface-2/60">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-ink">{channel.name}</div>
                        <div className="text-xs text-ink-faint">
                          {channel.language} - {plural(channel.competitors, "competitor")}
                          {channel.updated_at ? ` - ${formatRelative(channel.updated_at)}` : ""}
                        </div>
                      </div>
                      <ChannelStatusBadge status={channel.status} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

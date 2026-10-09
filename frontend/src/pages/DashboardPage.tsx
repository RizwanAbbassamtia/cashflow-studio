import { Activity, ArrowRight, Clapperboard, FolderOpen, PackageCheck, Plus, Tv } from "lucide-react";
import { Link } from "react-router";

import { useChannels } from "../api/channels";
import { useDoctor } from "../api/doctor";
import { useSettings } from "../api/settings";
import { useSystemInfo } from "../api/system";
import { GetStartedChecklist, type ChecklistStep } from "../components/dashboard/GetStartedChecklist";
import { StatCard } from "../components/dashboard/StatCard";
import { ChannelStatusBadge } from "../components/ui/Badge";
import { LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Notice } from "../components/ui/States";
import { summarizeDoctor } from "../layout/DoctorPill";
import { formatRelative, plural } from "../lib/format";

export function DashboardPage() {
  const channels = useChannels();
  const settings = useSettings();
  const doctor = useDoctor();
  const system = useSystemInfo();

  const channelList = channels.data ?? [];
  const activeCount = channelList.filter((c) => c.status === "active").length;
  const keysSet = settings.data ? Object.values(settings.data.keys).filter((k) => k.set).length : 0;
  const anthropicSet = Boolean(settings.data?.keys.ANTHROPIC_API_KEY?.set);
  const health = summarizeDoctor(doctor.data, doctor.error);

  const settingsDone = settings.isSuccess && keysSet > 0;
  const channelsDone = channels.isSuccess && channelList.length > 0;

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
      detail: "Scan the competitors for outlier videos and pick the first ideas.",
      state: "locked",
      milestone: "Arrives in M1",
    },
  ];

  const recent = [...channelList]
    .sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""))
    .slice(0, 5);

  return (
    <div className="flex flex-col gap-6">
      {channels.isError ? (
        <Notice tone="fail" title="The server is not answering">
          Start the backend (cfs serve) and reload. The pages keep working once it is up.
        </Notice>
      ) : null}

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <StatCard
          label="Channels"
          icon={Tv}
          to="/channels"
          value={channels.isPending ? "..." : channels.isError ? "-" : channelList.length}
          hint={channels.isSuccess ? `${activeCount} active` : "Open the channel list"}
        />
        <StatCard label="Videos in progress" icon={Clapperboard} value={0} tone="muted" hint="Research and the pipeline arrive in M1" />
        <StatCard label="Exports" icon={PackageCheck} value={0} tone="muted" hint="Rendering and export arrive in M4" />
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
        <GetStartedChecklist steps={steps} />

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

import { ChevronRight, Plus, Search, Tv } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";

import { useChannels } from "../api/channels";
import { ChannelStatusBadge } from "../components/ui/Badge";
import { LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Input } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { LoadingBlock } from "../components/ui/Spinner";
import { EmptyState, ErrorState } from "../components/ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../components/ui/Table";
import { formatRelative } from "../lib/format";
import { FORMAT_LABELS } from "../lib/labels";

export function ChannelsPage() {
  const navigate = useNavigate();
  const channels = useChannels();
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    const list = channels.data ?? [];
    const needle = search.trim().toLowerCase();
    if (!needle) return list;
    return list.filter(
      (channel) =>
        channel.name.toLowerCase().includes(needle) ||
        channel.slug.includes(needle) ||
        channel.language.toLowerCase().includes(needle) ||
        channel.status.includes(needle),
    );
  }, [channels.data, search]);

  const newChannelButton = (
    <LinkButton to="/channels/new" variant="primary" icon={<Plus />}>
      New channel
    </LinkButton>
  );

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Channels"
        description="One row per YouTube channel. Open a row to change its setup."
        actions={
          <>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-faint" aria-hidden />
              <Input
                type="search"
                placeholder="Search by name, language or status"
                className="w-72 pl-9"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                aria-label="Search channels"
              />
            </div>
            {newChannelButton}
          </>
        }
      />

      <Card flush>
        {channels.isPending ? (
          <LoadingBlock label="Loading channels..." />
        ) : channels.isError ? (
          <ErrorState error={channels.error} title="Could not load the channels" onRetry={() => void channels.refetch()} />
        ) : channels.data.length === 0 ? (
          <EmptyState
            icon={Tv}
            title="No channels yet"
            description="A channel holds everything the pipeline needs: competitors, frameworks, voice, images and thumbnail rules."
            action={newChannelButton}
          />
        ) : filtered.length === 0 ? (
          <EmptyState icon={Search} title="No channels match" description={`Nothing matches "${search}". Try another word.`} />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>Channel</TH>
                <TH className="w-[140px]">Status</TH>
                <TH className="w-[140px]">Language</TH>
                <TH className="w-[130px]">Formats</TH>
                <TH className="w-[130px] text-right">Competitors</TH>
                <TH className="w-[170px]">Last saved</TH>
                <TH className="w-10" />
              </TR>
            </THead>
            <TBody>
              {filtered.map((channel) => {
                const href = `/channels/${encodeURIComponent(channel.slug)}`;
                return (
                  <TR
                    key={channel.slug}
                    interactive
                    onClick={() => navigate(href)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") navigate(href);
                    }}
                    tabIndex={0}
                  >
                    <TD>
                      <div className="font-medium text-ink">{channel.name}</div>
                      <div className="font-mono text-xs text-ink-faint">{channel.slug}</div>
                    </TD>
                    <TD>
                      <ChannelStatusBadge status={channel.status} />
                    </TD>
                    <TD>{channel.language}</TD>
                    <TD className="text-ink-muted">{FORMAT_LABELS[channel.formats]}</TD>
                    <TD className="text-right tabular-nums">{channel.competitors}</TD>
                    <TD className="text-ink-muted">{channel.updated_at ? formatRelative(channel.updated_at) : "-"}</TD>
                    <TD className="text-right">
                      <ChevronRight className="inline size-4 text-ink-faint" aria-hidden />
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        )}
      </Card>
    </div>
  );
}

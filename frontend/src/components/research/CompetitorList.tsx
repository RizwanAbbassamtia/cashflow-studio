import { ExternalLink, RefreshCw, ScanSearch, Users } from "lucide-react";

import { formatRelative } from "../../lib/format";
import type { Competitor } from "../../types/channel";
import type { ScannedChannel } from "../../types/research";
import { Badge } from "../ui/Badge";
import { Button, LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState } from "../ui/States";
import { formatWhole } from "./numbers";

export interface CompetitorListProps {
  channelSlug: string;
  competitors: Competitor[];
  /** per-channel results of the last scan, from the candidates response */
  scanned: ScannedChannel[];
  scannedAt: string | null | undefined;
  scanning: boolean;
  onScan: (force: boolean) => void;
}

function normalizeUrl(url: string): string {
  return url
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\/(www\.)?/, "")
    .replace(/\/+$/, "");
}

const PRIORITY_TEXT = { 1: "Main", 2: "Secondary", 3: "Watch only" } as const;

/** The channel's competitors with what the last scan found for each, and the Scan now button. */
export function CompetitorList({ channelSlug, competitors, scanned, scannedAt, scanning, onScan }: CompetitorListProps) {
  const byUrl = new Map(scanned.map((row) => [normalizeUrl(row.url), row]));

  return (
    <Card
      title="Competitors"
      description={
        scannedAt
          ? `All ${competitors.length} are scanned together and ranked as one list. Last scan ${formatRelative(scannedAt)}.`
          : `All ${competitors.length} are scanned together and ranked as one list. Not scanned yet.`
      }
      actions={
        <>
          {scannedAt ? (
            <Button variant="ghost" size="sm" icon={<RefreshCw />} onClick={() => onScan(true)} disabled={scanning} title="Ignore the cache and list every channel again">
              Rescan all
            </Button>
          ) : null}
          <Button variant="primary" size="sm" icon={<ScanSearch />} loading={scanning} onClick={() => onScan(false)} disabled={competitors.length === 0}>
            {scanning ? "Scanning..." : "Scan now"}
          </Button>
        </>
      }
      flush
    >
      {competitors.length === 0 ? (
        <EmptyState
          icon={Users}
          title="No competitors on this channel"
          description="Add the YouTube channels you want to learn from in the channel setup, then come back and scan."
          action={
            <LinkButton to={`/channels/${encodeURIComponent(channelSlug)}`} variant="primary" size="sm">
              Open channel setup
            </LinkButton>
          }
        />
      ) : (
        <ul className="divide-y divide-line">
          {competitors.map((competitor) => {
            const result = byUrl.get(normalizeUrl(competitor.url));
            const videosFound = result?.videos_found ?? competitor.videos_found;
            const lastScanned = result?.last_scanned ?? competitor.last_scanned;
            const error = result?.error;
            return (
              <li key={competitor.url} className="flex items-center justify-between gap-4 px-5 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-ink">{competitor.name}</span>
                    <Badge tone={competitor.priority === 1 ? "accent" : "neutral"}>{PRIORITY_TEXT[competitor.priority]}</Badge>
                    <a
                      href={competitor.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-ink-faint hover:text-ink"
                      aria-label={`Open ${competitor.name} on YouTube`}
                      title={competitor.url}
                    >
                      <ExternalLink className="size-3.5" aria-hidden />
                    </a>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-ink-muted">{competitor.why || competitor.language}</p>
                </div>
                <div className="shrink-0 text-right text-xs">
                  {error ? (
                    <Badge tone="fail">Problem</Badge>
                  ) : scanning ? (
                    <Badge tone="info">Scanning</Badge>
                  ) : lastScanned ? (
                    <Badge tone="ok" dot>
                      Scanned
                    </Badge>
                  ) : (
                    <Badge tone="neutral">Not scanned</Badge>
                  )}
                  <p className="mt-1 text-ink-faint">
                    {error
                      ? error
                      : lastScanned
                        ? `${videosFound !== null && videosFound !== undefined ? `${formatWhole(videosFound)} videos - ` : ""}${formatRelative(lastScanned)}`
                        : "No data yet"}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

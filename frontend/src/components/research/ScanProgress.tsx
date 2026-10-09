import { X } from "lucide-react";

import type { JobStatus } from "../../types/project";
import { ProgressBar } from "../projects/ProgressBar";
import { Notice } from "../ui/States";

export interface ScanProgressProps {
  job: JobStatus | undefined;
  /** latest line from the WebSocket, usually fresher than the polled message */
  liveMessage?: string | null;
  onDismiss: () => void;
}

/** Progress of a competitor scan: polled from /api/jobs/{id}, with live lines from /api/ws. */
export function ScanProgress({ job, liveMessage, onDismiss }: ScanProgressProps) {
  if (!job) {
    return <ProgressBar value={null} label="Starting the scan..." />;
  }
  const message = liveMessage || job.message || (job.status === "queued" ? "Waiting for a free worker..." : "");

  if (job.status === "failed") {
    return (
      <Notice
        tone="fail"
        title="The scan stopped"
        action={
          <button type="button" onClick={onDismiss} className="rounded p-1 text-ink-faint hover:bg-surface-2 hover:text-ink" aria-label="Dismiss">
            <X className="size-4" />
          </button>
        }
      >
        {job.error || message || "Something went wrong while scanning. Try again in a few minutes."}
      </Notice>
    );
  }

  if (job.status === "done") {
    return (
      <Notice
        tone="ok"
        title="Scan finished"
        action={
          <button type="button" onClick={onDismiss} className="rounded p-1 text-ink-faint hover:bg-surface-2 hover:text-ink" aria-label="Dismiss">
            <X className="size-4" />
          </button>
        }
      >
        {message || "The candidates below are up to date."}
      </Notice>
    );
  }

  return <ProgressBar value={job.progress > 0 ? job.progress : null} label={message || "Scanning the competitors..."} />;
}

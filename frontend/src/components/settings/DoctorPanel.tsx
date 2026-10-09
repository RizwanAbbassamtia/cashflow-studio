import { Play, Wrench } from "lucide-react";

import { useDoctor } from "../../api/doctor";
import { cn } from "../../lib/cn";
import { summarizeDoctor } from "../../layout/DoctorPill";
import type { DoctorStatus } from "../../types";
import { DoctorStatusBadge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { LoadingBlock } from "../ui/Spinner";
import { ErrorState } from "../ui/States";

const dotClasses: Record<DoctorStatus, string> = {
  ok: "bg-ok",
  warn: "bg-warn",
  fail: "bg-fail",
};

const levelText = {
  ok: "text-ok",
  warn: "text-warn",
  fail: "text-fail",
  pending: "text-ink-muted",
} as const;

/** Runs the backend's environment checks (FFmpeg, folders, keys, disk space) and shows how to fix each one. */
export function DoctorPanel() {
  const doctor = useDoctor();
  const summary = summarizeDoctor(doctor.data, doctor.error);

  return (
    <Card
      id="doctor"
      title="Health checks"
      description="Checks that this computer has everything the pipeline needs. Run them again after installing a tool or changing a folder."
      actions={
        <Button variant="primary" size="sm" icon={<Play />} loading={doctor.isFetching} onClick={() => void doctor.refetch()}>
          Run checks
        </Button>
      }
      flush
    >
      {doctor.isPending ? (
        <LoadingBlock label="Running checks..." />
      ) : doctor.isError ? (
        <ErrorState error={doctor.error} title="The checks could not run" onRetry={() => void doctor.refetch()} />
      ) : (
        <>
          <div className="flex items-center justify-between gap-4 border-b border-line px-5 py-3">
            <p className={cn("text-sm font-medium", levelText[summary.level])}>{summary.label}</p>
            <p className="text-xs text-ink-faint">
              {doctor.data.checks.length} checks
              {doctor.dataUpdatedAt ? ` - last run ${new Date(doctor.dataUpdatedAt).toLocaleTimeString()}` : ""}
            </p>
          </div>
          <ul className="divide-y divide-line">
            {doctor.data.checks.map((check) => (
              <li key={check.id} className="flex items-start gap-4 px-5 py-3.5">
                <span className={cn("mt-1.5 size-2.5 shrink-0 rounded-full", dotClasses[check.status])} aria-hidden />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-ink">{check.name}</span>
                    <DoctorStatusBadge status={check.status} />
                  </div>
                  {check.detail ? <p className="mt-0.5 break-words text-[13px] text-ink-muted">{check.detail}</p> : null}
                  {check.status !== "ok" && check.fix_hint ? (
                    <p className="mt-1.5 flex items-start gap-1.5 text-[13px] text-ink">
                      <Wrench className="mt-0.5 size-3.5 shrink-0 text-accent-text" aria-hidden />
                      <span>
                        <span className="font-medium">How to fix: </span>
                        {check.fix_hint}
                      </span>
                    </p>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

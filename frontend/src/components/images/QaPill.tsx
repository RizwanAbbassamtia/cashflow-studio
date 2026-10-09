import { qaFlags, type ImageQA, type ImageRecordStatus, type ImageSource } from "../../types/images";
import { Badge, type BadgeTone } from "../ui/Badge";

const STATUS: Record<ImageRecordStatus, { label: string; tone: BadgeTone }> = {
  pending: { label: "No picture yet", tone: "neutral" },
  generated: { label: "Passed the check", tone: "ok" },
  approved: { label: "Approved", tone: "ok" },
  rejected: { label: "Failed the check", tone: "fail" },
};

const UPLOADED = { label: "Your picture", tone: "info" as BadgeTone };

export function qaStatusLabel(status: ImageRecordStatus, source: ImageSource = "provider"): string {
  return source === "upload" && status !== "pending" ? UPLOADED.label : STATUS[status].label;
}

export interface QaPillProps {
  status: ImageRecordStatus;
  source?: ImageSource;
  qa: ImageQA | null;
  /** the stage's own words ("Passed", "Rejected: ..."), shown on hover */
  verdict?: string;
  className?: string;
}

/** The QA verdict as a pill: the outcome, the score when there is one, the reason on hover. */
export function QaPill({ status, source = "provider", qa, verdict, className }: QaPillProps) {
  const uploaded = source === "upload" && status !== "pending";
  const { label, tone } = uploaded ? UPLOADED : STATUS[status];
  const flags = qa ? qaFlags(qa) : [];
  const title = [verdict, qa?.reason, ...flags].filter(Boolean).join(" - ") || undefined;
  return (
    <span title={title} className={className}>
      <Badge tone={tone} dot>
        {label}
        {qa && !uploaded && status !== "pending" ? <span className="tabular-nums opacity-80">{qa.score}/10</span> : null}
      </Badge>
    </span>
  );
}

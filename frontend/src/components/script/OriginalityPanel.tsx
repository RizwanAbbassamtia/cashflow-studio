import { CheckCircle2, CircleAlert, ShieldAlert, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../../lib/cn";
import { SCRIPT_GATES, type GateResult, type OriginalityDoc } from "../../types";
import { Badge } from "../ui/Badge";
import { formatShare } from "./scriptUtils";

interface CheckRow {
  id: string;
  title: string;
  passed: boolean;
  /** blocks the stage when failing, or only warns */
  blocking: boolean;
  value?: string;
  explanation: ReactNode;
}

function Row({ row }: { row: CheckRow }) {
  const Icon = row.passed ? CheckCircle2 : row.blocking ? ShieldAlert : CircleAlert;
  const tone = row.passed ? "text-ok" : row.blocking ? "text-fail" : "text-warn";
  return (
    <li className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
      <Icon className={cn("mt-0.5 size-4 shrink-0", tone)} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <span className="text-sm font-medium text-ink">{row.title}</span>
          {row.value ? <span className={cn("text-sm font-semibold tabular-nums", tone)}>{row.value}</span> : null}
        </div>
        <p className="mt-0.5 text-xs text-ink-muted">{row.explanation}</p>
      </div>
    </li>
  );
}

/**
 * The originality and policy results in plain English. Each number says what it measures,
 * what the limit is, and what to do when it is over.
 */
export function OriginalityPanel({ originality, gateResults }: { originality: OriginalityDoc | null; gateResults: GateResult[] }) {
  if (!originality && gateResults.length === 0) {
    return <p className="text-[13px] text-ink-muted">The originality and policy checks have not run for this script yet.</p>;
  }

  const rows: CheckRow[] = [];
  if (originality) {
    const sourceOk = originality.ngram_overlap_source <= SCRIPT_GATES.overlap_source_max;
    rows.push({
      id: "overlap_source",
      title: "Copied from the competitor video",
      passed: sourceOk,
      blocking: true,
      value: originality.source_compared ? formatShare(originality.ngram_overlap_source) : "n/a",
      explanation: !originality.source_compared
        ? "No competitor transcript was available, so there was nothing to compare against."
        : sourceOk
          ? `Share of 8-word runs that also appear in the competitor transcript. Anything up to ${formatShare(SCRIPT_GATES.overlap_source_max)} counts as original.`
          : `Too many 8-word runs match the competitor transcript (limit ${formatShare(SCRIPT_GATES.overlap_source_max)}). Rewrite or regenerate the paragraphs that repeat the source, then run the check again.`,
    });
    const historyOk = originality.ngram_overlap_history_max <= SCRIPT_GATES.overlap_history_max;
    const compared = originality.history_compared;
    rows.push({
      id: "overlap_history",
      title: "Repeats one of this channel's recent scripts",
      passed: historyOk,
      blocking: true,
      value: compared > 0 ? formatShare(originality.ngram_overlap_history_max) : "n/a",
      explanation:
        compared === 0
          ? "This channel has no earlier scripts yet, so there was nothing to compare against."
          : historyOk
            ? `Highest share of 8-word runs shared with any of the channel's last ${compared} script${compared === 1 ? "" : "s"}. Up to ${formatShare(SCRIPT_GATES.overlap_history_max)} is fine.`
            : `This script repeats too much of an earlier video on the channel (limit ${formatShare(SCRIPT_GATES.overlap_history_max)}, ${compared} compared). YouTube treats near-duplicate scripts as repetitive content; regenerate the repeated parts.`,
    });
    rows.push({
      id: "advisory_persona",
      title: "Speaks as a personal adviser",
      passed: !originality.policy.advisory_persona,
      blocking: true,
      explanation: originality.policy.advisory_persona
        ? "The script talks like a doctor, lawyer or financial adviser telling the viewer what to do. That is a policy risk, so the stage is blocked until the wording is changed to general information."
        : "The script informs rather than giving the viewer personal medical, legal or financial advice.",
    });
    rows.push({
      id: "sensitive_topic",
      title: "Sensitive topic",
      passed: !originality.policy.sensitive_topic,
      blocking: false,
      explanation: originality.policy.sensitive_topic
        ? "The topic touches an area YouTube watches closely (health, money, politics, tragedy). Not blocked, but check the tone and the facts before approving."
        : "No sensitive subject was flagged.",
    });
    if (originality.title_claim_early !== null) {
      rows.push({
        id: "title_claim_early",
        title: "Title promise comes early",
        passed: originality.title_claim_early,
        blocking: false,
        explanation: originality.title_claim_early
          ? "The claim in the title shows up in the first fifth of the script, where viewers decide whether to stay."
          : "The claim in the title does not appear in the first fifth of the script. Viewers who came for the title may leave; consider moving it into the hook.",
      });
    }
  }

  // The backend reports the same checks under its rule ids; only checks the rows above do
  // not already explain (the word count) are added, so nothing is listed twice.
  const explained: Record<string, string> = {
    "script.ngram_source": "overlap_source",
    "script.ngram_history": "overlap_history",
    "script.advisory_persona": "advisory_persona",
    "script.sensitive_topic": "sensitive_topic",
    "script.title_claim_early": "title_claim_early",
  };
  for (const gate of gateResults) {
    const twin = explained[gate.id] ?? gate.id;
    if (rows.some((row) => row.id === gate.id || row.id === twin)) continue;
    rows.push({
      id: gate.id,
      title: gate.title || gate.id,
      passed: gate.passed,
      blocking: gate.severity === "block",
      explanation: gate.detail || (gate.passed ? "Passed." : "Did not pass."),
    });
  }

  const blockers = rows.filter((row) => !row.passed && row.blocking).length;
  const warnings = rows.filter((row) => !row.passed && !row.blocking).length;
  const overall = originality ? originality.passed && blockers === 0 : blockers === 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <div className="flex items-center gap-2">
          {overall ? <ShieldCheck className="size-5 shrink-0 text-ok" aria-hidden /> : <ShieldAlert className="size-5 shrink-0 text-fail" aria-hidden />}
          <span className="text-sm font-semibold text-ink">{overall ? "Passes the originality and policy checks" : "Needs attention before it can go on"}</span>
        </div>
        <div className="flex gap-1.5">
          {blockers > 0 ? <Badge tone="fail">{blockers} blocking</Badge> : null}
          {warnings > 0 ? <Badge tone="warn">{warnings} to check</Badge> : null}
        </div>
      </div>

      <ul className="divide-y divide-line">
        {rows.map((row) => (
          <Row key={row.id} row={row} />
        ))}
      </ul>

      {originality?.blocking_reasons.length ? (
        <div className="rounded-md border border-fail/30 bg-fail/10 px-3.5 py-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-fail">Why the stage stopped</p>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[13px] text-ink">
            {originality.blocking_reasons.map((reason, index) => (
              <li key={index}>{reason}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {originality?.policy.reasons.length ? (
        <div className="rounded-md border border-line bg-surface-2/50 px-3.5 py-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted">What the policy reading noticed</p>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[13px] text-ink">
            {originality.policy.reasons.map((reason, index) => (
              <li key={index}>{reason}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {originality?.semantic_note ? (
        <p className="text-[13px] text-ink-muted">
          <span className="font-medium text-ink">How it differs from the source: </span>
          {originality.semantic_note}
        </p>
      ) : null}
    </div>
  );
}

import { Check, ExternalLink, Flag, RotateCcw, Star } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useChannel } from "../../api/channels";
import { errorMessage } from "../../api/client";
import { useApproveStage, useProject, useRedoStage, useStagePayload } from "../../api/projects";
import { cn } from "../../lib/cn";
import { formatRelative } from "../../lib/format";
import {
  TITLE_MAX_LENGTH,
  TITLE_SIMILARITY_LIMIT,
  type TitleApproveEdits,
  type TitleReviewPayload,
  type TitleVariant,
} from "../../types/title";
import { NotesDialog } from "../projects/NotesDialog";
import { useReviewerName } from "../projects/useReviewerName";
import { formatMultiple, formatPercent, formatViews } from "../research/numbers";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { Input } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { ErrorState, Notice } from "../ui/States";
import { useToast } from "../ui/Toast";

export interface TitleReviewProps {
  projectId: string;
}

function SimilarityBar({ label, value }: { label: string; value: number }) {
  const over = value >= TITLE_SIMILARITY_LIMIT;
  return (
    <div className="flex items-center gap-2 text-[11px] text-ink-faint" title={`${label}: ${formatPercent(value)} alike. Must stay under ${formatPercent(TITLE_SIMILARITY_LIMIT)}.`}>
      <span className="w-24 shrink-0">{label}</span>
      <span className="relative h-1.5 w-24 overflow-hidden rounded-full bg-surface-2">
        <span className={cn("absolute inset-y-0 left-0 rounded-full", over ? "bg-fail" : "bg-ok")} style={{ width: `${Math.min(100, Math.round(value * 100))}%` }} />
        <span className="absolute inset-y-0 w-px bg-line-strong" style={{ left: `${TITLE_SIMILARITY_LIMIT * 100}%` }} aria-hidden />
      </span>
      <span className={cn("tabular-nums", over && "text-fail")}>{formatPercent(value)}</span>
    </div>
  );
}

function ViralScore({ score }: { score: number }) {
  const tone = score >= 8 ? "text-ok" : score >= 6 ? "text-accent-text" : "text-ink-muted";
  return (
    <span className={cn("inline-flex items-center gap-1 text-sm font-semibold tabular-nums", tone)} title="How likely this title is to get clicked, 1 to 10">
      <Star className="size-3.5" aria-hidden />
      {score}/10
    </span>
  );
}

function VariantCard({
  variant,
  chosen,
  recommended,
  disabled,
  onChoose,
}: {
  variant: TitleVariant;
  chosen: boolean;
  recommended: boolean;
  disabled: boolean;
  onChoose: () => void;
}) {
  const flagged = variant.flags.length > 0;
  const tooLong = variant.length > TITLE_MAX_LENGTH;
  return (
    <li>
      <label
        className={cn(
          "flex cursor-pointer items-start gap-3 rounded-card border p-4 transition-colors",
          chosen ? "border-accent/60 bg-accent/10" : "border-line hover:border-line-strong hover:bg-surface-2/40",
          disabled && "cursor-default",
        )}
      >
        <input type="radio" name="title-variant" className="mt-1 accent-accent" checked={chosen} onChange={onChoose} disabled={disabled} aria-label={`Choose title ${variant.index + 1}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-ink-faint">#{variant.index + 1}</span>
            {recommended ? <Badge tone="accent">Recommended</Badge> : null}
            {flagged ? (
              <Badge tone="fail">
                <Flag className="size-3" aria-hidden />
                {variant.flags.length === 1 ? "1 problem" : `${variant.flags.length} problems`}
              </Badge>
            ) : null}
            <span className={cn("ml-auto text-[11px] tabular-nums", tooLong ? "text-fail" : "text-ink-faint")} title={`${variant.length} of ${TITLE_MAX_LENGTH} characters`}>
              {variant.length}/{TITLE_MAX_LENGTH}
            </span>
            <ViralScore score={variant.viral_score} />
          </div>
          <p className="mt-1.5 text-balance text-[15px] font-semibold text-ink">{variant.title}</p>
          {variant.why_it_outperforms ? <p className="mt-1 text-[13px] text-ink-muted">{variant.why_it_outperforms}</p> : null}
          <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 text-xs text-ink-muted md:grid-cols-2">
            {variant.formula ? (
              <div>
                <dt className="inline text-ink-faint">Formula: </dt>
                <dd className="inline">{variant.formula}</dd>
              </div>
            ) : null}
            {variant.emotional_trigger ? (
              <div>
                <dt className="inline text-ink-faint">Emotion: </dt>
                <dd className="inline">{variant.emotional_trigger}</dd>
              </div>
            ) : null}
            {variant.curiosity_trigger ? (
              <div>
                <dt className="inline text-ink-faint">Curiosity: </dt>
                <dd className="inline">{variant.curiosity_trigger}</dd>
              </div>
            ) : null}
            {variant.hidden_gap ? (
              <div>
                <dt className="inline text-ink-faint">Hidden gap: </dt>
                <dd className="inline">{variant.hidden_gap}</dd>
              </div>
            ) : null}
          </dl>
          <div className="mt-2 flex flex-wrap items-center gap-x-6 gap-y-1">
            <SimilarityBar label="Like the source" value={variant.similarity_to_source} />
            <SimilarityBar label="Like our past titles" value={variant.similarity_to_history} />
            {variant.keywords_kept.length > 0 ? (
              <span className="text-[11px] text-ink-faint">
                Keeps:
                {variant.keywords_kept.map((keyword) => (
                  <span key={keyword} className="ml-1 rounded bg-surface-2 px-1.5 py-0.5 text-ink-muted">
                    {keyword}
                  </span>
                ))}
              </span>
            ) : null}
          </div>
          {flagged ? (
            <ul className="mt-2 flex flex-col gap-0.5 text-xs text-fail">
              {variant.flags.map((flag) => (
                <li key={flag}>{flag}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </label>
    </li>
  );
}

/**
 * Review of the title stage: seven variants with scores and gate flags. Choose one, edit the
 * final text, approve (edits `{chosen_index}` or `{title_text}`), or redo with notes.
 */
export function TitleReview({ projectId }: TitleReviewProps) {
  const { toast } = useToast();
  const project = useProject(projectId);
  const channel = useChannel(project.data?.channel_slug);
  const { effective: by } = useReviewerName(channel.data?.reviewer ?? "");
  const status = project.data?.stages.title.status ?? "pending";
  const hasRun = status !== "pending" && status !== "running";
  const payload = useStagePayload<TitleReviewPayload>(projectId, "title", hasRun);
  const approve = useApproveStage();
  const redo = useRedoStage();

  const [chosenIndex, setChosenIndex] = useState<number | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [redoOpen, setRedoOpen] = useState(false);

  // The engine answers {} when the stage wrote nothing yet; the document sits under `title`.
  const doc = payload.data?.title;
  const variants = useMemo(() => [...(doc?.variants ?? [])].sort((a, b) => a.index - b.index), [doc]);
  const recommended = doc?.recommended_index ?? null;
  const activeIndex = chosenIndex ?? recommended ?? variants[0]?.index ?? null;
  const activeVariant = variants.find((variant) => variant.index === activeIndex) ?? null;
  const finalText = text ?? activeVariant?.title ?? "";
  const edited = activeVariant ? finalText.trim() !== activeVariant.title.trim() : finalText.trim().length > 0;

  useEffect(() => {
    setChosenIndex(null);
    setText(null);
  }, [payload.data]);

  if (!hasRun) {
    return <Notice tone="info">The title stage has not run yet. The variants show up here as soon as it finishes.</Notice>;
  }
  if (payload.isPending) return <LoadingBlock label="Loading title ideas..." />;
  if (payload.isError) {
    return <ErrorState error={payload.error} title="Could not load the title ideas" onRetry={() => void payload.refetch()} />;
  }
  if (!doc) {
    return (
      <Notice tone="warn" title="No title ideas saved for this project">
        {status === "failed"
          ? "The title stage did not finish. Use Redo with notes to try again."
          : status === "awaiting_manual"
            ? "The title stage is waiting for a title.json you provide; the AI did not write one."
            : "02_title/title.json is missing from the project folder."}
      </Notice>
    );
  }

  const canAct = status === "awaiting_review";
  const trimmed = finalText.trim();
  const tooLong = trimmed.length > TITLE_MAX_LENGTH;
  const canApprove = canAct && trimmed.length > 0 && !tooLong && !approve.isPending && !redo.isPending;
  const recentTitles = payload.data?.recent_titles ?? [];
  const failedGates = (doc.gate_results ?? []).filter((gate) => !gate.passed);
  const source = doc.source;

  const onApprove = () => {
    const edits: TitleApproveEdits = edited ? { title_text: trimmed } : { chosen_index: activeIndex ?? undefined };
    approve.mutate(
      { id: projectId, stage: "title", body: { by, edits: edits as Record<string, unknown> } },
      {
        onSuccess: () => toast({ tone: "success", title: "Title approved", description: `"${trimmed}" - the script stage runs next.` }),
        onError: (error) => toast({ tone: "error", title: "Could not approve the title", description: errorMessage(error) }),
      },
    );
  };

  const onRedo = (notes: string) => {
    redo.mutate(
      { id: projectId, stage: "title", body: { by, notes } },
      {
        onSuccess: () => {
          setRedoOpen(false);
          toast({ tone: "success", title: "Writing new titles", description: "Your notes were added to the prompt." });
        },
        onError: (error) => toast({ tone: "error", title: "Could not redo the title", description: errorMessage(error) }),
      },
    );
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3 text-[13px]">
        <div className="min-w-0">
          <span className="text-ink-muted">{source?.kind === "own_topic" ? "Your topic: " : "Competitor's title: "}</span>
          {source?.url ? (
            <a href={source.url} target="_blank" rel="noreferrer" className="font-medium text-ink hover:underline">
              {doc.source_title || source.title || "-"}
              <ExternalLink className="ml-1 inline size-3 text-ink-faint" aria-hidden />
            </a>
          ) : (
            <span className="font-medium text-ink">{doc.source_title || source?.title || "-"}</span>
          )}
          {source?.channel_name || source?.views ? (
            <span className="ml-2 text-xs text-ink-faint">
              {source.channel_name ?? ""}
              {source.views ? ` - ${formatViews(source.views)} views` : ""}
              {source.outlier_score ? ` - ${formatMultiple(source.outlier_score)} outlier` : ""}
            </span>
          ) : null}
        </div>
        <span className="shrink-0 text-xs text-ink-faint">
          {doc.model ? `${doc.model} - ` : ""}
          {doc.framework_name ? `${doc.framework_name} framework - ` : doc.framework_source === "default" ? "built-in framework - " : ""}
          {doc.generated_at ? formatRelative(doc.generated_at) : ""}
        </span>
      </div>

      {failedGates.length > 0 ? (
        <Notice tone="warn" title="Some checks did not pass">
          <ul className="list-disc pl-4">
            {failedGates.map((gate) => (
              <li key={gate.id}>
                {gate.title}
                {gate.detail ? `: ${gate.detail}` : ""}
              </li>
            ))}
          </ul>
        </Notice>
      ) : null}

      {variants.length === 0 ? (
        <Notice tone="warn" title="No variants were produced">
          Use Redo with notes to ask for a fresh set.
        </Notice>
      ) : (
        <ul className="flex flex-col gap-3">
          {variants.map((variant) => (
            <VariantCard
              key={variant.index}
              variant={variant}
              chosen={variant.index === activeIndex}
              recommended={variant.index === recommended}
              disabled={!canAct}
              onChoose={() => {
                setChosenIndex(variant.index);
                setText(null);
              }}
            />
          ))}
        </ul>
      )}

      {canAct ? (
        <div className="flex flex-col gap-4 rounded-card border border-line bg-surface-2/40 p-4">
          <Field
            label="Final title"
            htmlFor="final-title"
            error={tooLong ? `Keep it under ${TITLE_MAX_LENGTH} characters (now ${trimmed.length}).` : undefined}
            hint={edited ? "You changed the text; this exact title is saved." : `Pick a variant above or edit the text. ${trimmed.length}/${TITLE_MAX_LENGTH} characters.`}
          >
            <Input id="final-title" value={finalText} onChange={(event) => setText(event.target.value)} invalid={tooLong} maxLength={200} />
          </Field>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {edited && activeVariant ? (
              <Button variant="ghost" size="sm" onClick={() => setText(null)}>
                Back to variant #{activeVariant.index + 1}
              </Button>
            ) : null}
            <Button variant="secondary" size="sm" icon={<RotateCcw />} onClick={() => setRedoOpen(true)} disabled={approve.isPending || redo.isPending}>
              Redo with notes
            </Button>
            <Button variant="primary" size="sm" icon={<Check />} onClick={onApprove} loading={approve.isPending} disabled={!canApprove}>
              Use this title and continue
            </Button>
          </div>
        </div>
      ) : doc.chosen_title || project.data?.title ? (
        <p className="text-[13px] text-ink-muted">
          Chosen title: <span className="font-medium text-ink">{doc.chosen_title || project.data?.title}</span>
        </p>
      ) : null}

      {recentTitles.length > 0 ? (
        <details className="text-xs text-ink-muted">
          <summary className="cursor-pointer select-none font-medium hover:text-ink">
            The channel&apos;s recent titles ({recentTitles.length}), which the new ones must not copy
          </summary>
          <ul className="mt-2 list-disc pl-5">
            {recentTitles.map((title) => (
              <li key={title}>{title}</li>
            ))}
          </ul>
        </details>
      ) : null}

      <NotesDialog
        open={redoOpen}
        title="Redo the titles"
        description="Seven new variants are written with your notes added to the prompt."
        confirmLabel="Write new titles"
        requireNotes
        placeholder="Less clickbait; mention the number 3; keep the word 'retirement'."
        loading={redo.isPending}
        onConfirm={onRedo}
        onCancel={() => setRedoOpen(false)}
      />
    </div>
  );
}

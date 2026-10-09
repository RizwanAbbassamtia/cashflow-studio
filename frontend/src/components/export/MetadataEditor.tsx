import type { ReactNode } from "react";

import { cn } from "../../lib/cn";
import {
  DESCRIPTION_MAX_CHARS,
  HASHTAGS_MAX,
  PINNED_COMMENT_MAX_CHARS,
  TAGS_MAX_CHARS,
  tagsLength,
  TITLE_MAX_CHARS,
  type ExportMetadata,
} from "../../types/export";
import { Input, Textarea } from "../ui/Input";
import { ChaptersTable, chapterIssues } from "./ChaptersTable";
import { TagChips } from "./TagChips";

export interface MetadataEditorProps {
  value: ExportMetadata;
  onChange: (value: ExportMetadata) => void;
  /** video length for the chapter checks */
  durationS?: number | null;
  disabled?: boolean;
}

/** Problems that stop the export (YouTube would refuse or truncate). */
export function metadataBlockers(value: ExportMetadata): string[] {
  const issues: string[] = [];
  if (!value.title.trim()) issues.push("The title is empty.");
  if (value.title.length > TITLE_MAX_CHARS) issues.push(`The title is ${value.title.length} characters; YouTube allows ${TITLE_MAX_CHARS}.`);
  if (value.description.length > DESCRIPTION_MAX_CHARS) issues.push(`The description is ${value.description.length} characters; YouTube allows ${DESCRIPTION_MAX_CHARS}.`);
  const tagChars = tagsLength(value.tags);
  if (tagChars > TAGS_MAX_CHARS) issues.push(`The tags add up to ${tagChars} characters; YouTube allows ${TAGS_MAX_CHARS} in total.`);
  return issues;
}

/** Things worth a look but not a stop. */
export function metadataWarnings(value: ExportMetadata, durationS?: number | null): string[] {
  const warnings: string[] = [];
  if (!value.description.trim()) warnings.push("The description is empty.");
  if (value.tags.length === 0) warnings.push("No tags.");
  if (value.hashtags.length > HASHTAGS_MAX) warnings.push(`More than ${HASHTAGS_MAX} hashtags; YouTube ignores all of them when there are more than ${HASHTAGS_MAX}.`);
  if (value.pinned_comment.length > PINNED_COMMENT_MAX_CHARS) warnings.push(`The pinned comment is longer than ${PINNED_COMMENT_MAX_CHARS} characters.`);
  return [...warnings, ...chapterIssues(value.chapters, durationS)];
}

function Counter({ length, max }: { length: number; max: number }) {
  const over = length > max;
  return (
    <span className={cn("tabular-nums", over ? "text-fail" : length > max * 0.9 ? "text-warn" : "text-ink-faint")}>
      {length} / {max}
    </span>
  );
}

function FieldLabel({ label, htmlFor, right }: { label: string; htmlFor?: string; right?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <label htmlFor={htmlFor} className="text-[13px] font-medium text-ink-muted">
        {label}
      </label>
      {right ? <span className="text-xs">{right}</span> : null}
    </div>
  );
}

/** Title, description, tags, hashtags, chapters and pinned comment, edited in place. */
export function MetadataEditor({ value, onChange, durationS, disabled }: MetadataEditorProps) {
  const patch = (changes: Partial<ExportMetadata>) => onChange({ ...value, ...changes });
  const tagChars = tagsLength(value.tags);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1.5">
        <FieldLabel label="Title" htmlFor="export-title" right={<Counter length={value.title.length} max={TITLE_MAX_CHARS} />} />
        <Input
          id="export-title"
          value={value.title}
          disabled={disabled}
          invalid={!value.title.trim() || value.title.length > TITLE_MAX_CHARS}
          placeholder="The title YouTube shows"
          onChange={(event) => patch({ title: event.target.value })}
        />
        <p className="text-xs text-ink-faint">Approved in the title stage; change it here only for the upload.</p>
      </div>

      <div className="flex flex-col gap-1.5">
        <FieldLabel label="Description" htmlFor="export-description" right={<Counter length={value.description.length} max={DESCRIPTION_MAX_CHARS} />} />
        <Textarea
          id="export-description"
          rows={8}
          value={value.description}
          disabled={disabled}
          invalid={value.description.length > DESCRIPTION_MAX_CHARS}
          placeholder="What the video is about, links, credits"
          onChange={(event) => patch({ description: event.target.value })}
        />
      </div>

      <div className="grid gap-5 md:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <FieldLabel label="Tags" htmlFor="export-tags" right={<Counter length={tagChars} max={TAGS_MAX_CHARS} />} />
          <TagChips id="export-tags" values={value.tags} onChange={(tags) => patch({ tags })} disabled={disabled} invalid={tagChars > TAGS_MAX_CHARS} placeholder="Add a tag, press Enter" aria-label="Tags" />
          <p className="text-xs text-ink-faint">YouTube counts every tag and the commas between them; keep the total under {TAGS_MAX_CHARS}.</p>
        </div>
        <div className="flex flex-col gap-1.5">
          <FieldLabel label="Hashtags" htmlFor="export-hashtags" right={<span className={cn("tabular-nums", value.hashtags.length > HASHTAGS_MAX ? "text-fail" : "text-ink-faint")}>{value.hashtags.length} / {HASHTAGS_MAX}</span>} />
          <TagChips id="export-hashtags" values={value.hashtags} onChange={(hashtags) => patch({ hashtags })} prefix="#" maxItems={HASHTAGS_MAX} disabled={disabled} placeholder="Add a hashtag, press Enter" aria-label="Hashtags" />
          <p className="text-xs text-ink-faint">The first three show above the title.</p>
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <FieldLabel label="Chapters" right={<span className="text-ink-faint">{value.chapters.length} chapter{value.chapters.length === 1 ? "" : "s"}</span>} />
        <ChaptersTable chapters={value.chapters} onChange={(chapters) => patch({ chapters })} durationS={durationS} disabled={disabled} />
      </div>

      <div className="flex flex-col gap-1.5">
        <FieldLabel label="Pinned comment" htmlFor="export-pinned" right={value.pinned_comment.length > 0 ? <Counter length={value.pinned_comment.length} max={PINNED_COMMENT_MAX_CHARS} /> : null} />
        <Textarea
          id="export-pinned"
          rows={3}
          value={value.pinned_comment}
          disabled={disabled}
          placeholder="The comment to post and pin under the video (optional)"
          onChange={(event) => patch({ pinned_comment: event.target.value })}
        />
      </div>
    </div>
  );
}

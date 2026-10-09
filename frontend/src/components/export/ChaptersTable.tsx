import { Plus, Trash2 } from "lucide-react";

import { formatChapterTime, parseChapterTime, type Chapter } from "../../types/export";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";

export interface ChaptersTableProps {
  chapters: Chapter[];
  onChange: (chapters: Chapter[]) => void;
  /** video length, so a chapter past the end is flagged */
  durationS?: number | null;
  disabled?: boolean;
}

/** YouTube's rules: the first chapter starts at 0:00, at least three chapters, each 10 s or longer. */
export function chapterIssues(chapters: Chapter[], durationS?: number | null): string[] {
  const issues: string[] = [];
  if (chapters.length === 0) return issues;
  const seconds = chapters.map((chapter) => parseChapterTime(chapter.time));
  seconds.forEach((value, index) => {
    if (value === null) issues.push(`Chapter ${index + 1}: "${chapters[index]!.time || "(empty)"}" is not a time. Use mm:ss, for example 01:30.`);
    if (!chapters[index]!.title.trim()) issues.push(`Chapter ${index + 1} has no title.`);
  });
  if (seconds[0] !== null && seconds[0] !== 0) issues.push("The first chapter must start at 00:00 or YouTube ignores all chapters.");
  for (let i = 1; i < seconds.length; i += 1) {
    const prev = seconds[i - 1];
    const cur = seconds[i];
    if (prev === null || cur === null) continue;
    if (cur <= prev) issues.push(`Chapter ${i + 1} starts before or at the same time as chapter ${i}. Chapters must be in order.`);
    else if (cur - prev < 10) issues.push(`Chapter ${i} is shorter than 10 seconds; YouTube needs at least 10 seconds per chapter.`);
  }
  if (typeof durationS === "number" && durationS > 0) {
    seconds.forEach((value, index) => {
      if (value !== null && value >= durationS) issues.push(`Chapter ${index + 1} starts after the video ends (${formatChapterTime(durationS)}).`);
    });
  }
  if (chapters.length > 0 && chapters.length < 3) issues.push("YouTube only shows chapters when there are at least three.");
  return issues;
}

/** Editable list of chapters (time + title) with add and remove. */
export function ChaptersTable({ chapters, onChange, durationS, disabled }: ChaptersTableProps) {
  const update = (index: number, patch: Partial<Chapter>) => onChange(chapters.map((chapter, i) => (i === index ? { ...chapter, ...patch } : chapter)));
  const remove = (index: number) => onChange(chapters.filter((_, i) => i !== index));
  const add = () => {
    const last = chapters.length > 0 ? parseChapterTime(chapters[chapters.length - 1]!.time) : null;
    const next = chapters.length === 0 ? 0 : last !== null ? last + 60 : 0;
    onChange([...chapters, { time: formatChapterTime(next), title: "" }]);
  };
  const issues = chapterIssues(chapters, durationS);

  return (
    <div className="flex flex-col gap-2">
      {chapters.length > 0 ? (
        <div className="overflow-hidden rounded-md border border-line">
          <div className="grid grid-cols-[88px_1fr_40px] gap-2 border-b border-line bg-surface-2/60 px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-muted">
            <span>Time</span>
            <span>Title</span>
            <span />
          </div>
          <ul className="divide-y divide-line">
            {chapters.map((chapter, index) => {
              const timeOk = parseChapterTime(chapter.time) !== null;
              return (
                <li key={index} className="grid grid-cols-[88px_1fr_40px] items-center gap-2 px-3 py-1.5">
                  <Input
                    value={chapter.time}
                    invalid={!timeOk}
                    disabled={disabled}
                    className="h-8 font-mono text-[13px]"
                    aria-label={`Chapter ${index + 1} time`}
                    placeholder="mm:ss"
                    onChange={(event) => update(index, { time: event.target.value })}
                  />
                  <Input
                    value={chapter.title}
                    disabled={disabled}
                    className="h-8"
                    aria-label={`Chapter ${index + 1} title`}
                    placeholder="What this part is about"
                    onChange={(event) => update(index, { title: event.target.value })}
                  />
                  <button
                    type="button"
                    className="flex h-8 w-8 items-center justify-center rounded text-ink-faint hover:bg-surface-2 hover:text-fail disabled:cursor-not-allowed disabled:opacity-50"
                    onClick={() => remove(index)}
                    disabled={disabled}
                    aria-label={`Remove chapter ${index + 1}`}
                  >
                    <Trash2 className="size-4" aria-hidden />
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <p className="text-xs text-ink-faint">No chapters. YouTube shows them under the player when there are three or more.</p>
      )}
      {issues.length > 0 ? (
        <ul className="list-disc pl-4 text-xs text-warn">
          {issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      ) : null}
      <div>
        <Button variant="ghost" size="sm" icon={<Plus />} onClick={add} disabled={disabled}>
          Add chapter
        </Button>
      </div>
    </div>
  );
}

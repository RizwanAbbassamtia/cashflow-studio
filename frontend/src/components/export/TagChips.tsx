import { X } from "lucide-react";
import { useState, type ClipboardEvent, type KeyboardEvent } from "react";

import { cn } from "../../lib/cn";

export interface TagChipsProps {
  id?: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  /** every chip starts with this, for example "#" for hashtags */
  prefix?: string;
  maxItems?: number;
  disabled?: boolean;
  invalid?: boolean;
  "aria-label"?: string;
}

function normalise(text: string, prefix: string): string {
  let clean = text.trim().replace(/\s+/g, " ");
  if (!clean) return "";
  if (prefix) {
    while (clean.startsWith(prefix)) clean = clean.slice(prefix.length);
    clean = clean.replace(/\s+/g, "");
    return clean ? `${prefix}${clean}` : "";
  }
  return clean;
}

/** Tags as chips: type and press Enter or a comma to add one, Backspace removes the last. */
export function TagChips({ id, values, onChange, placeholder, prefix = "", maxItems, disabled, invalid, ...aria }: TagChipsProps) {
  const [text, setText] = useState("");
  const full = typeof maxItems === "number" && values.length >= maxItems;

  const add = (raw: string) => {
    const parts = raw
      .split(/[,\n]/)
      .map((part) => normalise(part, prefix))
      .filter(Boolean);
    if (parts.length === 0) return;
    const seen = new Set(values.map((value) => value.toLowerCase()));
    const next = [...values];
    for (const part of parts) {
      if (seen.has(part.toLowerCase())) continue;
      if (typeof maxItems === "number" && next.length >= maxItems) break;
      seen.add(part.toLowerCase());
      next.push(part);
    }
    if (next.length !== values.length) onChange(next);
  };

  const commit = () => {
    if (text.trim()) add(text);
    setText("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit();
    } else if (event.key === "Backspace" && !text && values.length > 0) {
      event.preventDefault();
      onChange(values.slice(0, -1));
    }
  };

  const onPaste = (event: ClipboardEvent<HTMLInputElement>) => {
    const pasted = event.clipboardData.getData("text");
    if (/[,\n]/.test(pasted)) {
      event.preventDefault();
      add(`${text}${pasted}`);
      setText("");
    }
  };

  return (
    <div
      className={cn(
        "flex min-h-9 w-full flex-wrap items-center gap-1.5 rounded-md border bg-canvas px-2 py-1.5 text-sm transition-colors",
        "focus-within:border-accent/70 focus-within:ring-2 focus-within:ring-accent/40",
        invalid ? "border-fail/70" : "border-line hover:border-line-strong",
        disabled && "cursor-not-allowed opacity-60",
      )}
      onClick={(event) => (event.currentTarget.querySelector("input") as HTMLInputElement | null)?.focus()}
    >
      {values.map((value) => (
        <span key={value} className="inline-flex items-center gap-1 rounded-full border border-line bg-surface-2 py-0.5 pl-2.5 pr-1 text-xs font-medium text-ink">
          {value}
          <button
            type="button"
            className="rounded-full p-0.5 text-ink-faint hover:bg-surface hover:text-ink disabled:cursor-not-allowed"
            onClick={(event) => {
              event.stopPropagation();
              onChange(values.filter((item) => item !== value));
            }}
            disabled={disabled}
            aria-label={`Remove ${value}`}
          >
            <X className="size-3" aria-hidden />
          </button>
        </span>
      ))}
      <input
        id={id}
        type="text"
        value={text}
        disabled={disabled || full}
        placeholder={full ? "" : (placeholder ?? "Type and press Enter")}
        aria-label={aria["aria-label"]}
        className="min-w-[120px] flex-1 bg-transparent py-0.5 text-sm text-ink placeholder:text-ink-faint focus:outline-none disabled:cursor-not-allowed"
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        onBlur={commit}
      />
    </div>
  );
}

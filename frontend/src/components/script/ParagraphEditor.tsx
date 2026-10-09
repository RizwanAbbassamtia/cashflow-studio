import { Volume2 } from "lucide-react";
import { useState } from "react";

import { cn } from "../../lib/cn";
import type { ScriptParagraph } from "../../types";
import { AutoTextarea } from "../storyboard/AutoTextarea";
import { LockToggle } from "./LockToggle";
import { countWords } from "./scriptUtils";

export interface ParagraphEditorProps {
  paragraph: ScriptParagraph;
  /** 1-based position inside the section, shown in the margin */
  number: number;
  /** the spoken form from speech.json, when it differs from the text */
  speech: string | null;
  edited: boolean;
  onTextChange: (text: string) => void;
  onLockChange: (locked: boolean) => void;
}

/** One paragraph: a growing textarea, the lock, the word count and the spoken form on demand. */
export function ParagraphEditor({ paragraph, number, speech, edited, onTextChange, onLockChange }: ParagraphEditorProps) {
  const [showSpeech, setShowSpeech] = useState(false);
  const words = countWords(paragraph.text);

  return (
    <div
      className={cn(
        "group grid grid-cols-[28px_1fr] gap-x-3 rounded-md border px-3 py-3 transition-colors",
        paragraph.locked ? "border-accent/40 bg-accent/5" : "border-transparent hover:border-line",
      )}
    >
      <div className="flex flex-col items-center gap-2 pt-1">
        <span className="text-[11px] font-semibold tabular-nums text-ink-faint">{number}</span>
        <LockToggle size="sm" locked={paragraph.locked} onChange={onLockChange} subject="this paragraph" />
      </div>
      <div className="min-w-0">
        <AutoTextarea
          value={paragraph.text}
          onChange={(event) => onTextChange(event.target.value)}
          aria-label={`Paragraph ${number}`}
          className={cn("border-transparent bg-transparent px-2 py-1 text-[15px] hover:border-line focus:bg-canvas", edited && "border-warn/40")}
          spellCheck
        />
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 px-2 text-xs text-ink-faint">
          <span className="tabular-nums">{words} words</span>
          {paragraph.locked ? <span className="text-accent-text">Locked: kept as is when regenerating</span> : null}
          {edited ? <span className="text-warn">Edited</span> : null}
          {speech ? (
            <button
              type="button"
              onClick={() => setShowSpeech((value) => !value)}
              className="inline-flex items-center gap-1 rounded text-ink-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
              aria-expanded={showSpeech}
            >
              <Volume2 className="size-3.5" aria-hidden />
              {showSpeech ? "Hide spoken form" : "Spoken form"}
            </button>
          ) : null}
        </div>
        {showSpeech && speech ? (
          <p className="mx-2 mt-2 rounded-md border border-line bg-surface-2/60 px-3 py-2 text-[13px] italic leading-6 text-ink-muted">{speech}</p>
        ) : null}
      </div>
    </div>
  );
}

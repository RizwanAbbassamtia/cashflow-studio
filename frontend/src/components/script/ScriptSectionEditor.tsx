import type { ScriptSection } from "../../types";
import { ParagraphEditor } from "./ParagraphEditor";
import { countWords, paragraphSpeech } from "./scriptUtils";

export interface ScriptSectionEditorProps {
  section: ScriptSection;
  /** the saved paragraph texts by id, to mark edits */
  savedTexts: Map<string, string>;
  speech: Map<string, string>;
  onTextChange: (paragraphId: string, text: string) => void;
  onLockChange: (paragraphId: string, locked: boolean) => void;
}

/** A `## Section` of the script: heading, purpose, and its paragraphs. */
export function ScriptSectionEditor({ section, savedTexts, speech, onTextChange, onLockChange }: ScriptSectionEditorProps) {
  const words = section.paragraphs.reduce((total, paragraph) => total + countWords(paragraph.text), 0);
  const locked = section.paragraphs.filter((paragraph) => paragraph.locked).length;

  return (
    <section className="flex flex-col gap-2">
      <header className="flex items-baseline justify-between gap-4 border-b border-line pb-2">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-ink">{section.name || "Untitled section"}</h3>
          {section.purpose ? <p className="mt-0.5 text-xs text-ink-muted">{section.purpose}</p> : null}
        </div>
        <p className="shrink-0 text-xs tabular-nums text-ink-faint">
          {words} words
          {locked > 0 ? <span className="text-accent-text"> - {locked} locked</span> : null}
        </p>
      </header>
      <div className="flex flex-col gap-1">
        {section.paragraphs.length === 0 ? (
          <p className="px-3 py-2 text-[13px] text-ink-faint">This section has no paragraphs.</p>
        ) : (
          section.paragraphs.map((paragraph, index) => (
            <ParagraphEditor
              key={paragraph.id}
              paragraph={paragraph}
              number={index + 1}
              speech={paragraphSpeech(paragraph, speech)}
              edited={savedTexts.has(paragraph.id) && savedTexts.get(paragraph.id) !== paragraph.text}
              onTextChange={(text) => onTextChange(paragraph.id, text)}
              onLockChange={(value) => onLockChange(paragraph.id, value)}
            />
          ))
        )}
      </div>
    </section>
  );
}

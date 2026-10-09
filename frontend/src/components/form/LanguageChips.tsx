import { Check } from "lucide-react";

import { cn } from "../../lib/cn";
import { LANGUAGES, type Language } from "../../types";

export interface LanguageChipsProps {
  value: Language[];
  onChange: (value: Language[]) => void;
  /** the main language is not offered again as a secondary one */
  exclude?: Language;
}

/** Multi-select as toggle chips, for secondary languages. */
export function LanguageChips({ value, onChange, exclude }: LanguageChipsProps) {
  const toggle = (language: Language) => {
    if (value.includes(language)) {
      onChange(value.filter((item) => item !== language));
    } else {
      onChange([...value, language]);
    }
  };

  return (
    <div className="flex flex-wrap gap-1.5" role="group">
      {LANGUAGES.filter((language) => language !== exclude).map((language) => {
        const selected = value.includes(language);
        return (
          <button
            key={language}
            type="button"
            aria-pressed={selected}
            onClick={() => toggle(language)}
            className={cn(
              "inline-flex h-7 items-center gap-1 rounded-full border px-2.5 text-xs font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
              selected
                ? "border-accent/50 bg-accent/15 text-ink"
                : "border-line bg-canvas text-ink-muted hover:border-line-strong hover:text-ink",
            )}
          >
            {selected ? <Check className="size-3 text-accent-text" aria-hidden /> : null}
            {language}
          </button>
        );
      })}
    </div>
  );
}

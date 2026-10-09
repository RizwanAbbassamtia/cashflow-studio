import { useId } from "react";

import { cn } from "../../lib/cn";
import { POPUP_MAX_WORDS, POPUP_POSITION_LABELS, POPUP_POSITIONS, type PopupPosition, type ScenePopup } from "../../types";
import { Input, Select } from "../ui/Input";
import { countWords } from "./storyboardUtils";

export interface PopupEditorProps {
  popup: ScenePopup;
  styles: string[];
  positions?: ReadonlyArray<PopupPosition>;
  disabled?: boolean;
  onChange: (popup: ScenePopup) => void;
}

/** Popup text (6 words max), its style name and the corner it sits in. */
export function PopupEditor({ popup, styles, positions = POPUP_POSITIONS, disabled, onChange }: PopupEditorProps) {
  const listId = useId();
  const words = countWords(popup.text ?? "");
  const tooLong = words > POPUP_MAX_WORDS;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Input
          value={popup.text ?? ""}
          placeholder="Popup text (short highlight, or leave empty)"
          disabled={disabled}
          invalid={tooLong}
          aria-label="Popup text"
          onChange={(event) => onChange({ ...popup, text: event.target.value === "" ? null : event.target.value })}
          className="h-8 text-[13px]"
        />
        <span className={cn("shrink-0 text-[11px] tabular-nums", tooLong ? "text-fail" : "text-ink-faint")}>
          {words}/{POPUP_MAX_WORDS}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Input
          list={listId}
          value={popup.style}
          placeholder="Style (channel default)"
          disabled={disabled}
          aria-label="Popup style"
          onChange={(event) => onChange({ ...popup, style: event.target.value })}
          className="h-8 text-[13px]"
        />
        <datalist id={listId}>
          {styles.map((style) => (
            <option key={style} value={style} />
          ))}
        </datalist>
        <Select
          value={popup.position}
          disabled={disabled}
          aria-label="Popup position"
          onChange={(event) => onChange({ ...popup, position: event.target.value as PopupPosition })}
          className="h-8 text-[13px]"
        >
          {positions.map((position) => (
            <option key={position} value={position}>
              {POPUP_POSITION_LABELS[position]}
            </option>
          ))}
        </Select>
      </div>
    </div>
  );
}

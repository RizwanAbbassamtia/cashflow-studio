import { Plus, X } from "lucide-react";

import { HEX_COLOR_PATTERN } from "../../lib/channelForm";
import { Button } from "../ui/Button";
import { Input } from "../ui/Input";

/** Pull the whole-list and per-item messages out of a react-hook-form error for an array field. */
export function listErrors(error: unknown): { root?: string; items: (string | undefined)[] } {
  const items: (string | undefined)[] = [];
  let root: string | undefined;
  if (Array.isArray(error)) {
    for (let i = 0; i < error.length; i += 1) {
      const entry: unknown = error[i];
      items[i] = messageOf(entry);
    }
  }
  if (typeof error === "object" && error !== null) {
    root = messageOf(error) ?? messageOf((error as { root?: unknown }).root);
  }
  return { root, items };
}

function messageOf(value: unknown): string | undefined {
  if (typeof value === "object" && value !== null && "message" in value) {
    const message = (value as { message?: unknown }).message;
    return typeof message === "string" ? message : undefined;
  }
  return undefined;
}

export interface StringListInputProps {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  addLabel?: string;
  /** show a colour swatch next to valid hex values */
  swatch?: boolean;
  error?: unknown;
  emptyText?: string;
  inputType?: "text" | "url";
}

/** A list of one-line text values with add/remove, for brand colours, links and sizes. */
export function StringListInput({
  value,
  onChange,
  placeholder,
  addLabel = "Add",
  swatch = false,
  error,
  emptyText,
  inputType = "text",
}: StringListInputProps) {
  const { root, items } = listErrors(error);

  const update = (index: number, next: string) => {
    onChange(value.map((item, i) => (i === index ? next : item)));
  };
  const remove = (index: number) => onChange(value.filter((_, i) => i !== index));
  const add = () => onChange([...value, ""]);

  return (
    <div className="flex flex-col gap-2">
      {value.length === 0 && emptyText ? <p className="text-xs text-ink-faint">{emptyText}</p> : null}
      {value.map((item, index) => {
        const itemError = items[index];
        const showSwatch = swatch && HEX_COLOR_PATTERN.test(item);
        return (
          <div key={index} className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              {swatch ? (
                <span
                  className="size-7 shrink-0 rounded-md border border-line"
                  style={{ backgroundColor: showSwatch ? item : "transparent" }}
                  aria-hidden
                />
              ) : null}
              <Input
                type={inputType}
                value={item}
                placeholder={placeholder}
                invalid={Boolean(itemError)}
                onChange={(event) => update(index, event.target.value)}
                spellCheck={false}
              />
              <Button variant="ghost" size="sm" onClick={() => remove(index)} aria-label="Remove" className="shrink-0 px-2">
                <X />
              </Button>
            </div>
            {itemError ? <p className="text-xs text-fail">{itemError}</p> : null}
          </div>
        );
      })}
      {root ? <p className="text-xs text-fail">{root}</p> : null}
      <div>
        <Button variant="secondary" size="sm" icon={<Plus />} onClick={add}>
          {addLabel}
        </Button>
      </div>
    </div>
  );
}

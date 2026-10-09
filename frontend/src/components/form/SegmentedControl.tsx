import { cn } from "../../lib/cn";

export interface SegmentedOption<Value extends string> {
  value: Value;
  label: string;
  description?: string;
}

export interface SegmentedControlProps<Value extends string> {
  options: ReadonlyArray<SegmentedOption<Value>>;
  value: Value;
  onChange: (value: Value) => void;
  "aria-label"?: string;
  className?: string;
}

/** Row of mutually exclusive buttons, for the per-stage auto / review / manual choice. */
export function SegmentedControl<Value extends string>({ options, value, onChange, className, ...aria }: SegmentedControlProps<Value>) {
  return (
    <div
      role="radiogroup"
      aria-label={aria["aria-label"]}
      className={cn("inline-flex rounded-md border border-line bg-canvas p-0.5", className)}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            title={option.description}
            onClick={() => onChange(option.value)}
            className={cn(
              "h-7 rounded-[5px] px-3 text-xs font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
              selected ? "bg-accent text-accent-ink shadow-sm" : "text-ink-muted hover:text-ink",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

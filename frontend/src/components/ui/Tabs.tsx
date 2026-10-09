import { cn } from "../../lib/cn";

export interface TabItem<Id extends string> {
  id: Id;
  label: string;
  hasError?: boolean;
  count?: number;
}

export interface TabsProps<Id extends string> {
  tabs: ReadonlyArray<TabItem<Id>>;
  value: Id;
  onChange: (id: Id) => void;
  className?: string;
}

export function Tabs<Id extends string>({ tabs, value, onChange, className }: TabsProps<Id>) {
  return (
    <div role="tablist" className={cn("flex gap-1 overflow-x-auto overflow-y-hidden border-b border-line", className)}>
      {tabs.map((tab) => {
        const active = tab.id === value;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(tab.id)}
            className={cn(
              "relative -mb-px flex h-10 items-center gap-2 whitespace-nowrap border-b-2 px-3 text-sm transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
              active
                ? "border-accent font-semibold text-ink"
                : "border-transparent text-ink-muted hover:border-line-strong hover:text-ink",
            )}
          >
            {tab.label}
            {typeof tab.count === "number" ? (
              <span className="rounded-full bg-surface-2 px-1.5 text-[11px] font-medium text-ink-muted">{tab.count}</span>
            ) : null}
            {tab.hasError ? (
              <span className="size-1.5 rounded-full bg-fail" aria-label="has errors" />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

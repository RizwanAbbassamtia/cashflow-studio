import { ChevronDown } from "lucide-react";
import type { ComponentProps } from "react";

import { cn } from "../../lib/cn";

const controlBase =
  "w-full rounded-md border bg-canvas text-sm text-ink placeholder:text-ink-faint transition-colors " +
  "focus:outline-none focus:ring-2 focus:ring-accent/40 focus:border-accent/70 " +
  "disabled:cursor-not-allowed disabled:opacity-60 read-only:opacity-80";

function borderClass(invalid: boolean | undefined) {
  return invalid ? "border-fail/70 focus:ring-fail/30 focus:border-fail/70" : "border-line hover:border-line-strong";
}

export interface InputProps extends ComponentProps<"input"> {
  invalid?: boolean;
}

export function Input({ className, invalid, ...props }: InputProps) {
  return (
    <input
      aria-invalid={invalid || undefined}
      className={cn(controlBase, "h-9 px-3", borderClass(invalid), className)}
      {...props}
    />
  );
}

export interface TextareaProps extends ComponentProps<"textarea"> {
  invalid?: boolean;
}

export function Textarea({ className, invalid, rows = 3, ...props }: TextareaProps) {
  return (
    <textarea
      aria-invalid={invalid || undefined}
      rows={rows}
      className={cn(controlBase, "min-h-9 resize-y px-3 py-2 leading-relaxed", borderClass(invalid), className)}
      {...props}
    />
  );
}

export interface SelectProps extends ComponentProps<"select"> {
  invalid?: boolean;
  wrapperClassName?: string;
}

export function Select({ className, invalid, wrapperClassName, children, ...props }: SelectProps) {
  return (
    <div className={cn("relative", wrapperClassName)}>
      <select
        aria-invalid={invalid || undefined}
        className={cn(controlBase, "h-9 appearance-none pl-3 pr-8", borderClass(invalid), className)}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute right-2.5 top-1/2 size-4 -translate-y-1/2 text-ink-faint"
      />
    </div>
  );
}

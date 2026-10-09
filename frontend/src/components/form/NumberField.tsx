import type { ComponentProps } from "react";

import { Input } from "../ui/Input";

export interface NumberFieldProps extends Omit<ComponentProps<"input">, "value" | "onChange" | "type"> {
  value: number | null | undefined;
  /** empty box -> null; the zod schema then says "Enter a whole number" when a value is required */
  onChange: (value: number | null) => void;
  invalid?: boolean;
}

/** Controlled number box used with react-hook-form's Controller. */
export function NumberField({ value, onChange, ...props }: NumberFieldProps) {
  return (
    <Input
      type="number"
      inputMode="decimal"
      value={value === null || value === undefined || Number.isNaN(value) ? "" : String(value)}
      onChange={(event) => {
        const raw = event.target.value;
        if (raw === "") {
          onChange(null);
          return;
        }
        const parsed = Number(raw);
        onChange(Number.isNaN(parsed) ? null : parsed);
      }}
      {...props}
    />
  );
}

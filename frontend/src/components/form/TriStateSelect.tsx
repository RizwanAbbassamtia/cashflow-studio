import { Select, type SelectProps } from "../ui/Input";

export interface TriStateSelectProps extends Omit<SelectProps, "value" | "onChange"> {
  value: boolean | null | undefined;
  onChange: (value: boolean | null) => void;
  unknownLabel?: string;
  yesLabel?: string;
  noLabel?: string;
}

/** Yes / No / Not sure, stored as true / false / null. */
export function TriStateSelect({
  value,
  onChange,
  unknownLabel = "Not sure yet",
  yesLabel = "Yes",
  noLabel = "No",
  ...props
}: TriStateSelectProps) {
  const current = value === true ? "yes" : value === false ? "no" : "unknown";
  return (
    <Select
      value={current}
      onChange={(event) => {
        const next = event.target.value;
        onChange(next === "yes" ? true : next === "no" ? false : null);
      }}
      {...props}
    >
      <option value="unknown">{unknownLabel}</option>
      <option value="yes">{yesLabel}</option>
      <option value="no">{noLabel}</option>
    </Select>
  );
}

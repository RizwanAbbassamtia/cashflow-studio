import type { ProjectFormat } from "../../types/project";
import { SegmentedControl, type SegmentedOption } from "../form/SegmentedControl";

const OPTIONS: ReadonlyArray<SegmentedOption<ProjectFormat>> = [
  { value: "long", label: "Long videos", description: "4 to 40 minutes, from the Videos tab" },
  { value: "shorts", label: "Shorts", description: "Up to 3 minutes, from the Shorts tab" },
];

/** Long videos or Shorts: changes which candidates are ranked and what gets produced. */
export function FormatToggle({ value, onChange, className }: { value: ProjectFormat; onChange: (value: ProjectFormat) => void; className?: string }) {
  return <SegmentedControl options={OPTIONS} value={value} onChange={onChange} aria-label="Video format" className={className} />;
}

import { Controller, type UseFormReturn } from "react-hook-form";

import type { ChannelFormValues } from "../../../lib/channelForm";
import { STAGE_NAMES, type StageMode, type StageName } from "../../../types";
import { SegmentedControl, type SegmentedOption } from "../../form/SegmentedControl";
import { Field, FormSection } from "../../ui/Field";
import { Input } from "../../ui/Input";

const STAGE_LABELS: Record<StageName, { label: string; description: string }> = {
  research: { label: "Research", description: "Scan competitors and pick video ideas" },
  title: { label: "Title", description: "Write the title and hook" },
  script: { label: "Script", description: "Write the full script" },
  storyboard: { label: "Storyboard", description: "Split the script into scenes with image prompts" },
  voice: { label: "Voice", description: "Record the narration" },
  images: { label: "Images", description: "Generate and check the scene images" },
  edit: { label: "Edit", description: "Build the timeline: motion, transitions, captions, music" },
  export: { label: "Export", description: "Render the video, thumbnail and description" },
};

const MODE_OPTIONS: ReadonlyArray<SegmentedOption<StageMode>> = [
  { value: "auto", label: "Auto", description: "Runs and continues on its own" },
  { value: "review", label: "Review", description: "Runs, then waits for your approval" },
  { value: "manual", label: "Manual", description: "Skips the AI; you supply the result" },
];

export function StageModesTab({ form }: { form: UseFormReturn<ChannelFormValues> }) {
  const { register, control } = form;

  return (
    <div className="flex flex-col gap-10">
      <FormSection title="How each stage runs" description="Auto runs and continues. Review runs and waits for approval in the Review queue. Manual skips the AI and waits for you to supply the result.">
        <div className="overflow-hidden rounded-card border border-line">
          <ul className="divide-y divide-line">
            {STAGE_NAMES.map((stage, index) => (
              <li key={stage} className="flex items-center justify-between gap-6 px-5 py-3.5">
                <div className="flex items-center gap-4">
                  <span className="flex size-7 items-center justify-center rounded-full bg-surface-2 text-xs font-semibold text-ink-muted">{index + 1}</span>
                  <div>
                    <div className="text-sm font-medium text-ink">{STAGE_LABELS[stage].label}</div>
                    <div className="text-xs text-ink-muted">{STAGE_LABELS[stage].description}</div>
                  </div>
                </div>
                <Controller
                  control={control}
                  name={`stage_modes.${stage}` as const}
                  render={({ field }) => (
                    <SegmentedControl options={MODE_OPTIONS} value={field.value} onChange={field.onChange} aria-label={`${STAGE_LABELS[stage].label} mode`} />
                  )}
                />
              </li>
            ))}
          </ul>
        </div>
      </FormSection>

      <FormSection title="Reviewer" description="Who approves the stages set to Review.">
        <Field label="Reviewer" htmlFor="reviewer" className="max-w-sm">
          <Input id="reviewer" placeholder="Imran" {...register("reviewer")} />
        </Field>
      </FormSection>
    </div>
  );
}

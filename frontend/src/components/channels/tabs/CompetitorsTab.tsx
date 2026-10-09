import { Plus, Trash2, Users } from "lucide-react";
import { Controller, useFieldArray, type UseFormReturn } from "react-hook-form";

import type { ChannelFormValues } from "../../../lib/channelForm";
import { formatRelative } from "../../../lib/format";
import { errorAt } from "../../../lib/formErrors";
import { defaultCompetitor, LANGUAGES, PRIORITIES, type Competitor, type Priority } from "../../../types";
import { Button } from "../../ui/Button";
import { Input, Select } from "../../ui/Input";
import { EmptyState } from "../../ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../../ui/Table";

const PRIORITY_LABELS: Record<Priority, string> = {
  1: "1 - Main competitor",
  2: "2 - Secondary",
  3: "3 - Watch only",
};

function CellError({ message }: { message: string | undefined }) {
  return message ? <p className="mt-1 text-xs text-fail">{message}</p> : null;
}

export function CompetitorsTab({ form }: { form: UseFormReturn<ChannelFormValues> }) {
  const {
    register,
    control,
    watch,
    formState: { errors },
  } = form;
  const { fields, append, remove } = useFieldArray({ control, name: "competitors" });
  const rows = watch("competitors");

  const addRow = () => append(defaultCompetitor());

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <p className="text-[13px] text-ink-muted">
          The channels the research stage scans for ideas. Priority 1 channels are scanned first and most often.
        </p>
        <Button variant="secondary" size="sm" icon={<Plus />} onClick={addRow}>
          Add competitor
        </Button>
      </div>

      {fields.length === 0 ? (
        <div className="rounded-card border border-dashed border-line">
          <EmptyState
            icon={Users}
            title="No competitors yet"
            description="Add the YouTube channels you want to learn from. Research (M1) scans them for outlier videos."
            action={
              <Button variant="primary" size="sm" icon={<Plus />} onClick={addRow}>
                Add the first competitor
              </Button>
            }
          />
        </div>
      ) : (
        <div className="overflow-hidden rounded-card border border-line">
          <Table>
            <THead>
              <TR>
                <TH className="w-[220px]">Name</TH>
                <TH>Channel link</TH>
                <TH className="w-[150px]">Language</TH>
                <TH className="w-[190px]">Priority</TH>
                <TH>Why this channel</TH>
                <TH className="w-12" />
              </TR>
            </THead>
            <TBody>
              {fields.map((field, index) => {
                const rowErrors = errors.competitors?.[index];
                const cell = (key: keyof Competitor) => errorAt(errors, `competitors.${index}.${key}`);
                const row = rows[index];
                return (
                  <TR key={field.id} className="align-top">
                    <TD>
                      <Input placeholder="Human Ember" invalid={Boolean(rowErrors?.name)} {...register(`competitors.${index}.name` as const)} />
                      <CellError message={cell("name")} />
                      {row && (row.videos_found !== null || row.last_scanned) ? (
                        <p className="mt-1 text-[11px] text-ink-faint">
                          {row.videos_found !== null ? `${row.videos_found} videos found` : ""}
                          {row.videos_found !== null && row.last_scanned ? " - " : ""}
                          {row.last_scanned ? `scanned ${formatRelative(row.last_scanned)}` : ""}
                        </p>
                      ) : null}
                    </TD>
                    <TD>
                      <Input type="url" placeholder="https://www.youtube.com/@..." spellCheck={false} invalid={Boolean(rowErrors?.url)} {...register(`competitors.${index}.url` as const)} />
                      <CellError message={cell("url")} />
                    </TD>
                    <TD>
                      <Select invalid={Boolean(cell("language"))} {...register(`competitors.${index}.language` as const)}>
                        {LANGUAGES.map((language) => (
                          <option key={language} value={language}>
                            {language}
                          </option>
                        ))}
                      </Select>
                      <CellError message={cell("language")} />
                    </TD>
                    <TD>
                      <Controller
                        control={control}
                        name={`competitors.${index}.priority` as const}
                        render={({ field: priorityField }) => (
                          <Select
                            value={String(priorityField.value)}
                            onBlur={priorityField.onBlur}
                            invalid={Boolean(cell("priority"))}
                            onChange={(event) => priorityField.onChange(Number(event.target.value) as Priority)}
                          >
                            {PRIORITIES.map((priority) => (
                              <option key={priority} value={priority}>
                                {PRIORITY_LABELS[priority]}
                              </option>
                            ))}
                          </Select>
                        )}
                      />
                      <CellError message={cell("priority")} />
                    </TD>
                    <TD>
                      <Input placeholder="Same niche, 3x our views on story videos" invalid={Boolean(cell("why"))} {...register(`competitors.${index}.why` as const)} />
                      <CellError message={cell("why")} />
                    </TD>
                    <TD className="text-right">
                      <Button variant="ghost" size="sm" className="px-2 text-ink-faint hover:text-fail" aria-label="Remove competitor" onClick={() => remove(index)}>
                        <Trash2 />
                      </Button>
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </div>
      )}
    </div>
  );
}

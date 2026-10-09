import { FileText, Plus, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { useFieldArray, type UseFormReturn } from "react-hook-form";

import { useUploadFramework } from "../../../api/channels";
import { errorMessage } from "../../../api/client";
import type { ChannelFormValues } from "../../../lib/channelForm";
import { errorAt } from "../../../lib/formErrors";
import { FORMAT_LABELS, FRAMEWORK_TYPE_LABELS } from "../../../lib/labels";
import { defaultFramework, FRAMEWORK_TYPES, VIDEO_FORMATS, type Framework, type FrameworkType } from "../../../types";
import { Button } from "../../ui/Button";
import { Input, Select } from "../../ui/Input";
import { EmptyState, Notice } from "../../ui/States";
import { Table, TBody, TD, TH, THead, TR } from "../../ui/Table";
import { useToast } from "../../ui/Toast";

function CellError({ message }: { message: string | undefined }) {
  return message ? <p className="mt-1 text-xs text-fail">{message}</p> : null;
}

export interface FrameworksTabProps {
  form: UseFormReturn<ChannelFormValues>;
  mode: "new" | "edit";
  /** slug the channel is saved under; uploads need it */
  slug: string;
}

export function FrameworksTab({ form, mode, slug }: FrameworksTabProps) {
  const {
    register,
    control,
    formState: { errors },
  } = form;
  const { fields, append, remove } = useFieldArray({ control, name: "frameworks" });
  const upload = useUploadFramework();
  const { toast } = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadType, setUploadType] = useState<FrameworkType>("title");

  const canUpload = mode === "edit" && slug.length > 0;

  const onFilePicked = async (file: File | undefined) => {
    if (!file || !canUpload) return;
    try {
      const framework = await upload.mutateAsync({ slug, file, type: uploadType });
      append(framework);
      toast({
        tone: "success",
        title: `Uploaded ${file.name}`,
        description: "Added as a framework row. Save the channel to keep it.",
      });
    } catch (error) {
      toast({ tone: "error", title: "Upload failed", description: errorMessage(error) });
    } finally {
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-3 rounded-card border border-line bg-surface-2/40 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2 text-sm font-medium text-ink">
            <Upload className="size-4 text-accent-text" aria-hidden />
            Upload a framework file
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Select
              aria-label="Framework type for the upload"
              wrapperClassName="w-56"
              value={uploadType}
              disabled={!canUpload}
              onChange={(event) => setUploadType(event.target.value as FrameworkType)}
            >
              {FRAMEWORK_TYPES.map((type) => (
                <option key={type} value={type}>
                  {FRAMEWORK_TYPE_LABELS[type]}
                </option>
              ))}
            </Select>
            <input
              ref={fileInput}
              type="file"
              accept=".txt,.md,.pdf,.docx,.doc,.json,.yaml,.yml,.csv,.xlsx"
              onChange={(event) => void onFilePicked(event.target.files?.[0])}
            />
            <Button
              variant="primary"
              size="md"
              icon={<Upload />}
              disabled={!canUpload}
              loading={upload.isPending}
              onClick={() => fileInput.current?.click()}
            >
              Choose file
            </Button>
          </div>
        </div>
        {canUpload ? (
          <p className="text-xs text-ink-muted">
            The file is copied into the shared drive under channels/{slug}/frameworks and a row is added below.
          </p>
        ) : (
          <Notice tone="info">
            Save the channel once, then you can upload files here. Until then, add rows with a file path or a link.
          </Notice>
        )}
      </div>

      <div className="flex items-start justify-between gap-4">
        <p className="text-[13px] text-ink-muted">
          Prompts and guides the AI follows for this channel: title formulas, script structure, scene prompts, thumbnail rules.
        </p>
        <Button variant="secondary" size="sm" icon={<Plus />} onClick={() => append(defaultFramework())}>
          Add row
        </Button>
      </div>

      {fields.length === 0 ? (
        <div className="rounded-card border border-dashed border-line">
          <EmptyState
            icon={FileText}
            title="No frameworks yet"
            description="Upload a file or add a row that points to a file in the shared drive or a link."
            action={
              <Button variant="primary" size="sm" icon={<Plus />} onClick={() => append(defaultFramework())}>
                Add the first framework
              </Button>
            }
          />
        </div>
      ) : (
        <div className="overflow-hidden rounded-card border border-line">
          <Table>
            <THead>
              <TR>
                <TH className="w-[200px]">Type</TH>
                <TH className="w-[220px]">Name</TH>
                <TH>File path or link</TH>
                <TH className="w-[110px]">Version</TH>
                <TH className="w-[140px]">Formats</TH>
                <TH>Notes</TH>
                <TH className="w-12" />
              </TR>
            </THead>
            <TBody>
              {fields.map((field, index) => {
                const cell = (key: keyof Framework) => errorAt(errors, `frameworks.${index}.${key}`);
                return (
                  <TR key={field.id} className="align-top">
                    <TD>
                      <Select invalid={Boolean(cell("type"))} {...register(`frameworks.${index}.type` as const)}>
                        {FRAMEWORK_TYPES.map((type) => (
                          <option key={type} value={type}>
                            {FRAMEWORK_TYPE_LABELS[type]}
                          </option>
                        ))}
                      </Select>
                      <CellError message={cell("type")} />
                    </TD>
                    <TD>
                      <Input placeholder="Title Writing Prompt" invalid={Boolean(cell("name"))} {...register(`frameworks.${index}.name` as const)} />
                      <CellError message={cell("name")} />
                    </TD>
                    <TD>
                      <Input className="font-mono text-[13px]" placeholder="frameworks/title-prompt.txt or https://..." spellCheck={false} invalid={Boolean(cell("path"))} {...register(`frameworks.${index}.path` as const)} />
                      <CellError message={cell("path")} />
                    </TD>
                    <TD>
                      <Input placeholder="v1" invalid={Boolean(cell("version"))} {...register(`frameworks.${index}.version` as const)} />
                      <CellError message={cell("version")} />
                    </TD>
                    <TD>
                      <Select invalid={Boolean(cell("formats"))} {...register(`frameworks.${index}.formats` as const)}>
                        {VIDEO_FORMATS.map((format) => (
                          <option key={format} value={format}>
                            {FORMAT_LABELS[format]}
                          </option>
                        ))}
                      </Select>
                      <CellError message={cell("formats")} />
                    </TD>
                    <TD>
                      <Input placeholder="When to use it" invalid={Boolean(cell("notes"))} {...register(`frameworks.${index}.notes` as const)} />
                      <CellError message={cell("notes")} />
                    </TD>
                    <TD className="text-right">
                      <Button variant="ghost" size="sm" className="px-2 text-ink-faint hover:text-fail" aria-label="Remove framework" onClick={() => remove(index)}>
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

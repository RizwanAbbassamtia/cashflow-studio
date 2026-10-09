import { Controller, type UseFormReturn } from "react-hook-form";

import type { ChannelFormValues } from "../../../lib/channelForm";
import { FORMAT_LABELS, STATUS_LABELS } from "../../../lib/labels";
import { CHANNEL_STATUSES, LANGUAGES, VIDEO_FORMATS } from "../../../types";
import { LanguageChips } from "../../form/LanguageChips";
import { NumberField } from "../../form/NumberField";
import { StringListInput } from "../../form/StringListInput";
import { Field, FieldGrid, FormSection } from "../../ui/Field";
import { Input, Select } from "../../ui/Input";

export interface ChannelTabProps {
  form: UseFormReturn<ChannelFormValues>;
  mode: "new" | "edit";
  onSlugEdited: () => void;
}

export function ChannelTab({ form, mode, onSlugEdited }: ChannelTabProps) {
  const {
    register,
    control,
    watch,
    formState: { errors },
  } = form;
  const e = errors.channel;
  const mainLanguage = watch("channel.language");
  const youtubeId = watch("channel.id");

  return (
    <div className="flex flex-col gap-10">
      <FormSection title="Identity" description="The name is what you see in the app; the folder name is used in the shared drive.">
        <FieldGrid>
          <Field label="Channel name" required htmlFor="channel.name" error={e?.name?.message}>
            <Input id="channel.name" placeholder="Kind Ledger" invalid={Boolean(e?.name)} {...register("channel.name")} />
          </Field>
          <Field
            label="Folder name"
            htmlFor="slug"
            error={errors.slug?.message}
            hint={
              mode === "new"
                ? "Made from the name. Lowercase letters, numbers and dashes."
                : "Fixed once the channel exists."
            }
          >
            <Input
              id="slug"
              className="font-mono"
              readOnly={mode === "edit"}
              invalid={Boolean(errors.slug)}
              spellCheck={false}
              {...register("slug", { onChange: onSlugEdited })}
            />
          </Field>
          <Field label="YouTube channel link" htmlFor="channel.url" error={e?.url?.message} hint="Optional, for example https://www.youtube.com/@yourchannel">
            <Input id="channel.url" type="url" placeholder="https://www.youtube.com/@..." invalid={Boolean(e?.url)} spellCheck={false} {...register("channel.url")} />
          </Field>
          <Field label="Status" htmlFor="channel.status">
            <Select id="channel.status" {...register("channel.status")}>
              {CHANNEL_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {STATUS_LABELS[status]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Owner" htmlFor="channel.owner" hint="Who looks after this channel">
            <Input id="channel.owner" placeholder="Imran" {...register("channel.owner")} />
          </Field>
          <Field label="Browser profile" htmlFor="channel.browser_profile" error={e?.browser_profile?.message} hint="Number of the browser profile used to upload to this channel. Optional.">
            <Controller
              control={control}
              name="channel.browser_profile"
              render={({ field, fieldState }) => (
                <NumberField
                  id="channel.browser_profile"
                  min={0}
                  step={1}
                  placeholder="84"
                  value={field.value}
                  onChange={field.onChange}
                  onBlur={field.onBlur}
                  invalid={Boolean(fieldState.error)}
                />
              )}
            />
          </Field>
        </FieldGrid>
        <p className="text-xs text-ink-faint">
          YouTube channel id: {youtubeId ? <span className="font-mono text-ink-muted">{youtubeId}</span> : "not found yet, the app fills this in"}
        </p>
      </FormSection>

      <FormSection title="Audience and content">
        <FieldGrid>
          <Field label="Main language" htmlFor="channel.language">
            <Select id="channel.language" {...register("channel.language")}>
              {LANGUAGES.map((language) => (
                <option key={language} value={language}>
                  {language}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Niche" htmlFor="channel.niche">
            <Input id="channel.niche" placeholder="Kindness and emotional stories" {...register("channel.niche")} />
          </Field>
          <Field label="Secondary languages" className="md:col-span-2" hint="Extra languages this channel also publishes in. Optional.">
            <Controller
              control={control}
              name="channel.secondary_languages"
              render={({ field }) => <LanguageChips value={field.value} onChange={field.onChange} exclude={mainLanguage} />}
            />
          </Field>
          <Field label="Audience" htmlFor="channel.audience" className="md:col-span-2">
            <Input id="channel.audience" placeholder="Adults 35+, English-speaking, watches at night" {...register("channel.audience")} />
          </Field>
        </FieldGrid>
      </FormSection>

      <FormSection title="Formats and schedule">
        <FieldGrid columns={3}>
          <Field label="Video formats" htmlFor="channel.formats">
            <Select id="channel.formats" {...register("channel.formats")}>
              {VIDEO_FORMATS.map((format) => (
                <option key={format} value={format}>
                  {FORMAT_LABELS[format]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Long video length (minutes)" htmlFor="channel.long_form_minutes" error={e?.long_form_minutes?.message} hint="1 to 60">
            <Controller
              control={control}
              name="channel.long_form_minutes"
              render={({ field, fieldState }) => (
                <NumberField id="channel.long_form_minutes" min={1} max={60} step={1} value={field.value} onChange={field.onChange} onBlur={field.onBlur} invalid={Boolean(fieldState.error)} />
              )}
            />
          </Field>
          <Field label="Shorts length (seconds)" htmlFor="channel.shorts_seconds" error={e?.shorts_seconds?.message} hint="10 to 180">
            <Controller
              control={control}
              name="channel.shorts_seconds"
              render={({ field, fieldState }) => (
                <NumberField id="channel.shorts_seconds" min={10} max={180} step={1} value={field.value} onChange={field.onChange} onBlur={field.onBlur} invalid={Boolean(fieldState.error)} />
              )}
            />
          </Field>
          <Field label="Videos per week" htmlFor="channel.videos_per_week" error={e?.videos_per_week?.message} hint="0 to 100">
            <Controller
              control={control}
              name="channel.videos_per_week"
              render={({ field, fieldState }) => (
                <NumberField id="channel.videos_per_week" min={0} max={100} step={1} value={field.value} onChange={field.onChange} onBlur={field.onBlur} invalid={Boolean(fieldState.error)} />
              )}
            />
          </Field>
        </FieldGrid>
      </FormSection>

      <FormSection title="Folders and look">
        <FieldGrid>
          <Field label="Export folder" htmlFor="channel.export_folder" hint="Where finished videos for this channel go. Leave empty to use the exports folder from Settings.">
            <Input id="channel.export_folder" placeholder="D:\CashflowStudio\exports\kind-ledger" spellCheck={false} {...register("channel.export_folder")} />
          </Field>
          <Field label="Music folder" htmlFor="channel.music_folder" hint="Background music the editor can pick from.">
            <Input id="channel.music_folder" placeholder="D:\CashflowStudio\music\calm" spellCheck={false} {...register("channel.music_folder")} />
          </Field>
          <Field label="Caption style" htmlFor="channel.caption_style" className="md:col-span-2">
            <Input id="channel.caption_style" placeholder="Bold white, black outline, bottom centre, 2 lines max" {...register("channel.caption_style")} />
          </Field>
          <Field label="Brand colours" className="md:col-span-2" hint="Hex values, like #1F3864. Used for popups, captions and thumbnails.">
            <Controller
              control={control}
              name="channel.brand_colors"
              render={({ field, fieldState }) => (
                <StringListInput
                  value={field.value}
                  onChange={field.onChange}
                  swatch
                  placeholder="#1F3864"
                  addLabel="Add colour"
                  error={fieldState.error}
                  emptyText="No brand colours yet."
                />
              )}
            />
          </Field>
        </FieldGrid>
      </FormSection>
    </div>
  );
}

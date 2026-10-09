import { Controller, type UseFormReturn } from "react-hook-form";

import type { ChannelFormValues } from "../../../lib/channelForm";
import { THUMBNAIL_FACES, type ThumbnailFace } from "../../../types";
import { NumberField } from "../../form/NumberField";
import { StringListInput } from "../../form/StringListInput";
import { Field, FieldGrid, FormSection } from "../../ui/Field";
import { Input, Select } from "../../ui/Input";

const FACE_LABELS: Record<ThumbnailFace, string> = {
  none: "No face",
  face: "A real face",
  ai_character: "An AI character",
};

export function ThumbnailTab({ form }: { form: UseFormReturn<ChannelFormValues> }) {
  const {
    register,
    control,
    formState: { errors },
  } = form;
  const e = errors.thumbnail;

  return (
    <div className="flex flex-col gap-10">
      <FormSection title="Templates" description="Competitor videos whose thumbnails you want to model. Links to YouTube videos.">
        <Controller
          control={control}
          name="thumbnail.template_videos"
          render={({ field, fieldState }) => (
            <StringListInput
              value={field.value}
              onChange={field.onChange}
              inputType="url"
              placeholder="https://www.youtube.com/watch?v=..."
              addLabel="Add video link"
              error={fieldState.error}
              emptyText="No template videos yet."
            />
          )}
        />
      </FormSection>

      <FormSection title="Headline">
        <FieldGrid columns={3}>
          <Field label="Headline font" htmlFor="thumbnail.headline_font">
            <Input id="thumbnail.headline_font" placeholder="Anton" {...register("thumbnail.headline_font")} />
          </Field>
          <Field label="Headline colours" htmlFor="thumbnail.headline_colors">
            <Input id="thumbnail.headline_colors" placeholder="#FFFFFF on #1F3864, accent #FFC000" {...register("thumbnail.headline_colors")} />
          </Field>
          <Field label="Most words in a headline" htmlFor="thumbnail.max_headline_words" error={e?.max_headline_words?.message} hint="1 to 12">
            <Controller
              control={control}
              name="thumbnail.max_headline_words"
              render={({ field, fieldState }) => (
                <NumberField id="thumbnail.max_headline_words" min={1} max={12} step={1} value={field.value} onChange={field.onChange} onBlur={field.onBlur} invalid={Boolean(fieldState.error)} />
              )}
            />
          </Field>
        </FieldGrid>
      </FormSection>

      <FormSection title="Rules">
        <FieldGrid>
          <Field label="Face" htmlFor="thumbnail.face">
            <Select id="thumbnail.face" {...register("thumbnail.face")}>
              {THUMBNAIL_FACES.map((face) => (
                <option key={face} value={face}>
                  {FACE_LABELS[face]}
                </option>
              ))}
            </Select>
          </Field>
          <div className="hidden md:block" />
          <Field label="Must include" htmlFor="thumbnail.must_include">
            <Input id="thumbnail.must_include" placeholder="one emotional subject, strong contrast, one accent colour" {...register("thumbnail.must_include")} />
          </Field>
          <Field label="Must avoid" htmlFor="thumbnail.must_avoid">
            <Input id="thumbnail.must_avoid" placeholder="clutter, more than 2 text blocks, real people, logos" {...register("thumbnail.must_avoid")} />
          </Field>
        </FieldGrid>
      </FormSection>

      <FormSection title="Sizes" description="Thumbnail sizes to export, width x height in pixels.">
        <Controller
          control={control}
          name="thumbnail.sizes"
          render={({ field, fieldState }) => (
            <StringListInput value={field.value} onChange={field.onChange} placeholder="1280x720" addLabel="Add size" error={fieldState.error} emptyText="No sizes yet." />
          )}
        />
      </FormSection>
    </div>
  );
}

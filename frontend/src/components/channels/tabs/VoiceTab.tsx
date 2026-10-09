import { Controller, type UseFormReturn } from "react-hook-form";

import type { ChannelFormValues } from "../../../lib/channelForm";
import { KNOWN_KEY_NAMES, LANGUAGES, VOICE_TOOLS, type VoiceTool } from "../../../types";
import { NumberField } from "../../form/NumberField";
import { TriStateSelect } from "../../form/TriStateSelect";
import { Field, FieldGrid, FormSection } from "../../ui/Field";
import { Input, Select, Textarea } from "../../ui/Input";

const VOICE_TOOL_LABELS: Record<VoiceTool, string> = {
  minimax: "MiniMax",
  cartesia: "Cartesia",
  inworld: "Inworld",
  fish_audio: "Fish Audio",
  azure: "Microsoft Azure",
  google: "Google",
  local: "Local (on this computer)",
  other: "Other",
};

/** Shared <datalist> of the key names the Settings page knows about. */
export function KnownKeysDatalist({ id }: { id: string }) {
  return (
    <datalist id={id}>
      {KNOWN_KEY_NAMES.map((name) => (
        <option key={name} value={name} />
      ))}
    </datalist>
  );
}

export function VoiceTab({ form }: { form: UseFormReturn<ChannelFormValues> }) {
  const {
    register,
    control,
    formState: { errors },
  } = form;
  const e = errors.voice;

  return (
    <div className="flex flex-col gap-10">
      <FormSection title="Voice tool" description="Which service speaks the script, and which voice it uses.">
        <FieldGrid>
          <Field label="Tool" htmlFor="voice.tool">
            <Select id="voice.tool" {...register("voice.tool")}>
              {VOICE_TOOLS.map((tool) => (
                <option key={tool} value={tool}>
                  {VOICE_TOOL_LABELS[tool]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Voice name" htmlFor="voice.name" hint="A name you recognise, for example Daniel - warm narrator">
            <Input id="voice.name" placeholder="Daniel - warm narrator" {...register("voice.name")} />
          </Field>
          <Field label="Clone link or voice id" htmlFor="voice.clone_ref" className="md:col-span-2" hint="The link or id of the voice in the tool, not an API key.">
            <Input id="voice.clone_ref" placeholder="https://fish.audio/m/..." spellCheck={false} {...register("voice.clone_ref")} />
          </Field>
          <Field label="Language" htmlFor="voice.language">
            <Select id="voice.language" {...register("voice.language")}>
              {LANGUAGES.map((language) => (
                <option key={language} value={language}>
                  {language}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Model" htmlFor="voice.model" hint="The tool's model name, if it has more than one.">
            <Input id="voice.model" placeholder="s1" spellCheck={false} {...register("voice.model")} />
          </Field>
        </FieldGrid>
      </FormSection>

      <FormSection title="Delivery">
        <FieldGrid>
          <Field label="Speed" htmlFor="voice.speed" error={e?.speed?.message} hint="1.0 is normal. 0.5 to 2.0.">
            <Controller
              control={control}
              name="voice.speed"
              render={({ field, fieldState }) => (
                <NumberField id="voice.speed" min={0.5} max={2} step={0.05} value={field.value} onChange={field.onChange} onBlur={field.onBlur} invalid={Boolean(fieldState.error)} />
              )}
            />
          </Field>
          <Field label="Style" htmlFor="voice.style" hint="How it should sound.">
            <Input id="voice.style" placeholder="calm, warm, slow at emotional moments" {...register("voice.style")} />
          </Field>
          <Field label="Voice sample file" htmlFor="voice.sample_path" hint="A short recording of the voice, for reference.">
            <Input id="voice.sample_path" placeholder="D:\CashCowStudio\voices\daniel-sample.mp3" spellCheck={false} {...register("voice.sample_path")} />
          </Field>
          <Field label="Gives word timings?" htmlFor="voice.returns_word_timestamps" hint="If no, the app lines up the words with the audio itself.">
            <Controller
              control={control}
              name="voice.returns_word_timestamps"
              render={({ field }) => (
                <TriStateSelect id="voice.returns_word_timestamps" value={field.value} onChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </Field>
        </FieldGrid>
      </FormSection>

      <FormSection
        title="Consent for a cloned voice"
        description="Only the team's own voices may be cloned. When the channel uses a clone (a clone link or voice id above, or a voice sample), record who agreed and when; the voice step stops until this is filled in or a consent.json sits next to the sample. Leave it empty for a stock voice."
      >
        <FieldGrid>
          <Field label="Voice owner" htmlFor="voice.consent.owner_name" error={e?.consent?.owner_name?.message} hint="Whose voice it is.">
            <Input id="voice.consent.owner_name" placeholder="Daniel Example" {...register("voice.consent.owner_name")} />
          </Field>
          <Field label="Recorded by" htmlFor="voice.consent.consented_by" error={e?.consent?.consented_by?.message} hint="Who on the team took the consent.">
            <Input id="voice.consent.consented_by" placeholder="Imran" {...register("voice.consent.consented_by")} />
          </Field>
          <Field label="Agreed on" htmlFor="voice.consent.consented_at" error={e?.consent?.consented_at?.message} hint="The date and time the owner agreed.">
            <Input id="voice.consent.consented_at" type="datetime-local" invalid={Boolean(e?.consent?.consented_at)} {...register("voice.consent.consented_at")} />
          </Field>
          <Field label="What they agreed to" htmlFor="voice.consent.statement" className="md:col-span-2" hint="Optional. The sentence the owner agreed to, for the record.">
            <Textarea id="voice.consent.statement" rows={2} placeholder="I agree that my voice may be cloned for the Kind Ledger channel." {...register("voice.consent.statement")} />
          </Field>
        </FieldGrid>
      </FormSection>

      <FormSection title="Account" description="Keys themselves are entered once in Settings. Here you only name the key to use.">
        <FieldGrid>
          <Field label="API key name" htmlFor="voice.api_key_env" error={e?.api_key_env?.message} hint="The key's name in Settings, like FISH_AUDIO_API_KEY. Never the key itself.">
            <Input id="voice.api_key_env" list="known-keys-voice" className="font-mono" placeholder="FISH_AUDIO_API_KEY" spellCheck={false} autoComplete="off" invalid={Boolean(e?.api_key_env)} {...register("voice.api_key_env")} />
            <KnownKeysDatalist id="known-keys-voice" />
          </Field>
          <Field label="Monthly budget (characters)" htmlFor="voice.monthly_budget_characters" error={e?.monthly_budget_characters?.message} hint="Optional. The app warns when the channel gets close.">
            <Controller
              control={control}
              name="voice.monthly_budget_characters"
              render={({ field, fieldState }) => (
                <NumberField id="voice.monthly_budget_characters" min={0} step={1000} placeholder="1000000" value={field.value} onChange={field.onChange} onBlur={field.onBlur} invalid={Boolean(fieldState.error)} />
              )}
            />
          </Field>
        </FieldGrid>
      </FormSection>
    </div>
  );
}

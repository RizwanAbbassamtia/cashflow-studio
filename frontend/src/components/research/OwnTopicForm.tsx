import { Lightbulb, Play } from "lucide-react";
import { useState } from "react";

import type { ProjectFormat } from "../../types/project";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Field } from "../ui/Field";
import { Textarea } from "../ui/Input";

export interface OwnTopicFormProps {
  format: ProjectFormat;
  onSubmit: (topic: string) => void;
  submitting: boolean;
  disabled?: boolean;
}

const MIN_LENGTH = 8;
const MAX_LENGTH = 500;

/** Skip research: start a project from a topic typed by hand. */
export function OwnTopicForm({ format, onSubmit, submitting, disabled }: OwnTopicFormProps) {
  const [topic, setTopic] = useState("");
  const trimmed = topic.trim();
  const tooShort = trimmed.length > 0 && trimmed.length < MIN_LENGTH;
  const canSubmit = trimmed.length >= MIN_LENGTH && trimmed.length <= MAX_LENGTH && !submitting && !disabled;

  return (
    <Card
      title={
        <span className="inline-flex items-center gap-2">
          <Lightbulb className="size-4 text-accent-text" aria-hidden />
          Own topic
        </span>
      }
      description="Have an idea already? Start production from it. Research is skipped and the title stage runs first."
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit) onSubmit(trimmed);
        }}
      >
        <Field
          label="Topic or angle"
          htmlFor="own-topic"
          error={tooShort ? `Write at least ${MIN_LENGTH} characters so the title stage has something to work with.` : undefined}
          hint={`${trimmed.length}/${MAX_LENGTH} characters. One or two sentences is plenty.`}
        >
          <Textarea
            id="own-topic"
            rows={3}
            maxLength={MAX_LENGTH}
            placeholder={format === "shorts" ? "Why most people fail at saving money in their 20s" : "The three money habits that quietly keep middle-class families stuck"}
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
            disabled={disabled}
          />
        </Field>
        <div className="flex items-center justify-between gap-4">
          <p className="text-xs text-ink-faint">Starts a {format === "shorts" ? "Short" : "long video"}. Switch the format above to change that.</p>
          <Button type="submit" variant="secondary" icon={<Play />} loading={submitting} disabled={!canSubmit}>
            Start from this topic
          </Button>
        </div>
      </form>
    </Card>
  );
}

import {
  TIMING_CONFIDENCE_LABELS,
  TIMING_SOURCE_HELP,
  TIMING_SOURCE_LABELS,
  type TimingConfidence,
  type TimingSource,
} from "../../types/timing";
import { Badge, type BadgeTone } from "../ui/Badge";

const SOURCE_TONE: Record<TimingSource, BadgeTone> = {
  provider_word: "ok",
  aligned: "ok",
  provider_sentence: "info",
  estimated: "warn",
};

const CONFIDENCE_TONE: Record<TimingConfidence, BadgeTone> = {
  high: "ok",
  medium: "info",
  low: "warn",
};

const CONFIDENCE_HELP: Record<TimingConfidence, string> = {
  high: "Captions and popups will land on the spoken words.",
  medium: "Sentence starts are exact; word positions inside a sentence may be a little off.",
  low: "The timings are estimated from the text alone. Check captions in the edit preview before exporting.",
};

/** Where the word and sentence times came from. */
export function TimingSourceBadge({ source }: { source: TimingSource }) {
  return (
    <span title={TIMING_SOURCE_HELP[source]}>
      <Badge tone={SOURCE_TONE[source]} dot>
        {TIMING_SOURCE_LABELS[source]}
      </Badge>
    </span>
  );
}

/** How much to trust those times. */
export function ConfidenceBadge({ confidence }: { confidence: TimingConfidence }) {
  return (
    <span title={CONFIDENCE_HELP[confidence]}>
      <Badge tone={CONFIDENCE_TONE[confidence]}>{TIMING_CONFIDENCE_LABELS[confidence]}</Badge>
    </span>
  );
}

export function confidenceHelp(confidence: TimingConfidence): string {
  return CONFIDENCE_HELP[confidence];
}

import { OUTLIER_LABEL_TEXT, type OutlierLabel } from "../../types/research";
import { Badge, type BadgeTone } from "../ui/Badge";

const tones: Record<OutlierLabel, BadgeTone> = {
  "one-of-ten": "accent",
  strong: "ok",
  notable: "info",
  normal: "neutral",
};

const titles: Record<OutlierLabel, string> = {
  "one-of-ten": "At least 10 times the channel's usual views",
  strong: "At least 5 times the channel's usual views",
  notable: "At least 3 times the channel's usual views",
  normal: "Around the channel's usual views",
};

/** The outlier label pill: how far above its own channel's usual views a video sits. */
export function OutlierBadge({ label }: { label: OutlierLabel }) {
  return (
    <Badge tone={tones[label] ?? "neutral"} className="whitespace-nowrap" dot>
      <span title={titles[label]}>{OUTLIER_LABEL_TEXT[label] ?? label}</span>
    </Badge>
  );
}

import type { ChannelSummary } from "../../types/channel";
import { Select } from "../ui/Input";

export interface ChannelPickerProps {
  channels: ChannelSummary[];
  value: string | undefined;
  onChange: (slug: string) => void;
  className?: string;
  disabled?: boolean;
}

/** Which of your channels to research for. */
export function ChannelPicker({ channels, value, onChange, className, disabled }: ChannelPickerProps) {
  return (
    <Select
      wrapperClassName={className}
      value={value ?? ""}
      disabled={disabled || channels.length === 0}
      onChange={(event) => {
        if (event.target.value) onChange(event.target.value);
      }}
      aria-label="Channel"
    >
      <option value="" disabled>
        {channels.length === 0 ? "No channels yet" : "Choose a channel"}
      </option>
      {channels.map((channel) => (
        <option key={channel.slug} value={channel.slug}>
          {channel.name}
          {channel.competitors ? ` (${channel.competitors} competitor${channel.competitors === 1 ? "" : "s"})` : " (no competitors)"}
        </option>
      ))}
    </Select>
  );
}

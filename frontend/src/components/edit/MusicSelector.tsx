import { Music } from "lucide-react";

import { fileName, type MusicTrack } from "../../types/timeline";
import { Badge } from "../ui/Badge";
import { Select } from "../ui/Input";
import { formatDuration } from "./editUtils";

export interface MusicSelectorProps {
  /** tracks the channel's music folder offers */
  tracks: MusicTrack[];
  /** the chosen track path, or null for no music */
  value: string | null;
  /** licence status of the chosen track (from the timeline when the track list has none) */
  licenseOk: boolean;
  onChange: (path: string | null) => void;
  /** the channel's music folder, for the empty state */
  folder?: string | null;
  disabled?: boolean;
}

const NONE = "__none__";

/** Picks the background music from the channel's folder; shows whether a licence file was found. */
export function MusicSelector({ tracks, value, licenseOk, onChange, folder, disabled }: MusicSelectorProps) {
  const known = value ? tracks.find((track) => track.path === value) : undefined;
  const license = known ? known.license_ok : licenseOk;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={value ?? NONE} onChange={(event) => onChange(event.target.value === NONE ? null : event.target.value)} disabled={disabled} aria-label="Background music" wrapperClassName="min-w-[240px] flex-1">
          <option value={NONE}>No music</option>
          {value && !known ? <option value={value}>{fileName(value)} (from the timeline)</option> : null}
          {tracks.map((track) => (
            <option key={track.path} value={track.path}>
              {track.name}
              {track.duration_s ? ` (${formatDuration(track.duration_s)})` : ""}
              {track.license_ok ? "" : " - no licence file"}
            </option>
          ))}
        </Select>
        {value ? (
          <Badge tone={license ? "ok" : "warn"} dot>
            {license ? "Licence found" : "No licence file"}
          </Badge>
        ) : (
          <Badge tone="neutral">Silent background</Badge>
        )}
      </div>
      {value && !license ? (
        <p className="text-xs text-warn">
          No <span className="font-mono">{fileName(value)}.license.txt</span> or LICENSE file sits next to this track. The video still renders, but the check will warn until the licence is in the music folder.
        </p>
      ) : null}
      {tracks.length === 0 ? (
        <p className="flex items-start gap-2 text-xs text-ink-faint">
          <Music className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>
            No tracks found{folder ? <> in <span className="font-mono">{folder}</span></> : " in the channel's music folder"}. Drop MP3 or WAV files there, each with a <span className="font-mono">.license.txt</span> next to it, then render again.
          </span>
        </p>
      ) : null}
    </div>
  );
}

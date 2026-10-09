import { Copy, FileVideo, FolderOpen, Image as ImageGlyph, FileJson } from "lucide-react";

import { errorMessage } from "../../api/client";
import { useOpenExportFolder } from "../../api/export";
import { renderPresetLabel } from "../../types/timeline";
import type { ExportFile } from "../../types/export";
import { formatBytes } from "../edit/editUtils";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { useToast } from "../ui/Toast";

export interface ExportFolderPanelProps {
  projectId: string;
  /** where the files go (or went); null until the backend decides */
  folder: string | null;
  /** the folder the app will use, worked out from Settings and the channel, when the payload has none */
  plannedFolder: string | null;
  exported: boolean;
  files: ExportFile[];
  /** the final sizes to export, for the preview list */
  presets: string[];
  topicSlug: string;
}

function iconFor(name: string) {
  if (/\.(mp4|mov|mkv|webm)$/i.test(name)) return FileVideo;
  if (/\.(png|jpe?g|webp)$/i.test(name)) return ImageGlyph;
  return FileJson;
}

/** The export folder with Open folder, and the files in it (or the ones that will be written). */
export function ExportFolderPanel({ projectId, folder, plannedFolder, exported, files, presets, topicSlug }: ExportFolderPanelProps) {
  const { toast } = useToast();
  const open = useOpenExportFolder();
  const shown = folder ?? plannedFolder;

  const copy = async () => {
    if (!shown) return;
    try {
      await navigator.clipboard.writeText(shown);
      toast({ tone: "success", title: "Folder path copied" });
    } catch {
      toast({ tone: "error", title: "Could not copy", description: "Select the path and copy it by hand." });
    }
  };

  const planned = [
    ...presets.map((preset) => `${topicSlug || "video"}_${preset}.mp4`),
    "thumbnail.png",
    "thumbnail_shorts.png",
    "metadata.json",
    "provenance.json",
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-xs text-ink-muted">
            {exported ? <Badge tone="ok" dot>Exported</Badge> : <Badge tone="neutral">Not exported yet</Badge>}
            {!folder && plannedFolder ? <span>Planned folder</span> : null}
          </p>
          {shown ? (
            <p className="mt-1.5 select-all break-all font-mono text-xs text-ink">{shown}</p>
          ) : (
            <p className="mt-1.5 text-xs text-ink-faint">The folder is chosen when you approve: your exports folder, then the channel, then the date and topic.</p>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          size="sm"
          icon={<FolderOpen />}
          loading={open.isPending}
          disabled={!shown && !exported}
          title={exported ? "Open the export folder in Explorer" : "Opens the folder once it exists; until then the project folder"}
          onClick={() =>
            open.mutate(
              { projectId, folder: folder ?? plannedFolder },
              { onError: (error) => toast({ tone: "error", title: "Could not open the folder", description: errorMessage(error) }) },
            )
          }
        >
          Open folder
        </Button>
        <Button variant="ghost" size="sm" icon={<Copy />} disabled={!shown} onClick={() => void copy()}>
          Copy path
        </Button>
      </div>
      <ul className="flex flex-col gap-1 text-xs">
        {(exported && files.length > 0 ? files.map((file) => ({ name: file.name, size: formatBytes(file.size_bytes), planned: false })) : planned.map((name) => ({ name, size: "", planned: true }))).map((row) => {
          const Icon = iconFor(row.name);
          return (
            <li key={row.name} className="flex items-center gap-2 text-ink-muted">
              <Icon className="size-3.5 shrink-0 text-ink-faint" aria-hidden />
              <span className={row.planned ? "font-mono text-ink-faint" : "font-mono text-ink"}>{row.name}</span>
              {row.size ? <span className="ml-auto tabular-nums text-ink-faint">{row.size}</span> : null}
            </li>
          );
        })}
      </ul>
      {!exported ? (
        <p className="text-xs text-ink-faint">
          {presets.length > 0
            ? `Written when you approve: the ${presets.map(renderPresetLabel).join(" and ")} video${presets.length === 1 ? "" : "s"}, both thumbnails and the metadata.`
            : "Written when you approve: the rendered video, both thumbnails and the metadata."}
        </p>
      ) : null}
    </div>
  );
}

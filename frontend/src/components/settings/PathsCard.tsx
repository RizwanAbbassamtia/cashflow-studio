import { FolderOpen, RotateCcw, Save } from "lucide-react";
import { useState } from "react";

import { errorMessage } from "../../api/client";
import { useSettings, useUpdateSettings } from "../../api/settings";
import { useSystemInfo } from "../../api/system";
import type { SettingsUpdate } from "../../types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Input } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { ErrorState, Notice } from "../ui/States";
import { useToast } from "../ui/Toast";

type PathKey = "shared_dir" | "projects_dir" | "exports_dir";

const PATHS: ReadonlyArray<{ key: PathKey; label: string; description: string }> = [
  {
    key: "shared_dir",
    label: "Shared folder",
    description: "The synced Google Drive folder with channel files, frameworks and assets. Everyone on the team points to the same folder.",
  },
  {
    key: "projects_dir",
    label: "Projects folder",
    description: "Work folders for each video: research, script, voice, images, timeline. Stays on this computer.",
  },
  {
    key: "exports_dir",
    label: "Exports folder",
    description: "Finished videos and thumbnails. Stays on this computer. A channel can override it.",
  },
];

export function PathsCard() {
  const settings = useSettings();
  const system = useSystemInfo();
  const update = useUpdateSettings();
  const { toast } = useToast();
  const [edits, setEdits] = useState<Partial<Record<PathKey, string>>>({});
  const [resetting, setResetting] = useState<PathKey | null>(null);

  const current = settings.data;
  const changed = current
    ? (Object.keys(edits) as PathKey[]).filter((key) => edits[key] !== undefined && edits[key] !== current[key])
    : [];

  const saveChanges = async () => {
    if (!current || changed.length === 0) return;
    const body: SettingsUpdate = {};
    for (const key of changed) body[key] = edits[key]?.trim() ?? "";
    try {
      await update.mutateAsync(body);
      setEdits({});
      toast({ tone: "success", title: "Folders saved" });
    } catch (error) {
      toast({ tone: "error", title: "Could not save the folders", description: errorMessage(error) });
    }
  };

  const useDefault = async (key: PathKey) => {
    setResetting(key);
    try {
      await update.mutateAsync({ [key]: null });
      setEdits((previous) => {
        const next = { ...previous };
        delete next[key];
        return next;
      });
      toast({ tone: "success", title: "Back to the default folder" });
    } catch (error) {
      toast({ tone: "error", title: "Could not reset the folder", description: errorMessage(error) });
    } finally {
      setResetting(null);
    }
  };

  return (
    <Card
      id="paths"
      title="Folders"
      description="Where the app keeps shared channel data and your local video files."
      actions={
        <Button variant="primary" size="sm" icon={<Save />} disabled={changed.length === 0} loading={update.isPending && resetting === null} onClick={() => void saveChanges()}>
          Save folders
        </Button>
      }
    >
      {settings.isPending ? (
        <LoadingBlock label="Loading folders..." />
      ) : settings.isError ? (
        <ErrorState error={settings.error} title="Could not load the folders" onRetry={() => void settings.refetch()} />
      ) : (
        <div className="flex flex-col gap-5">
          {current?.shared_dir_is_default ? (
            <Notice tone="warn" title="You are using the default shared folder on this computer">
              Channel files are saved under your app data folder and are not shared with the team yet. Point the shared folder at your synced Google Drive folder.
            </Notice>
          ) : null}

          {PATHS.map((path) => {
            const value = edits[path.key] ?? current?.[path.key] ?? "";
            const isEdited = edits[path.key] !== undefined && edits[path.key] !== current?.[path.key];
            return (
              <div key={path.key} className="grid gap-x-6 gap-y-2 md:grid-cols-[260px_1fr]">
                <div>
                  <div className="flex items-center gap-2 text-sm font-medium text-ink">
                    <FolderOpen className="size-4 text-ink-faint" aria-hidden />
                    {path.label}
                  </div>
                  <p className="mt-1 text-xs text-ink-muted">{path.description}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Input
                    className="font-mono text-[13px]"
                    value={value}
                    spellCheck={false}
                    aria-label={path.label}
                    onChange={(event) => setEdits((previous) => ({ ...previous, [path.key]: event.target.value }))}
                  />
                  {isEdited ? <span className="text-xs text-warn">edited</span> : null}
                  <Button
                    variant="secondary"
                    size="sm"
                    icon={<RotateCcw />}
                    loading={resetting === path.key}
                    onClick={() => void useDefault(path.key)}
                    title="Go back to the folder the app uses when none is chosen"
                  >
                    Use default
                  </Button>
                </div>
              </div>
            );
          })}

          {system.data ? (
            <p className="text-xs text-ink-faint">
              App data (settings, keys, cache): <span className="font-mono">{system.data.app_data_dir}</span>
            </p>
          ) : null}
        </div>
      )}
    </Card>
  );
}

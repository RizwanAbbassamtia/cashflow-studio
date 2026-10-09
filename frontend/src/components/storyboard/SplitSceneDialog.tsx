import { Scissors } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "../../lib/cn";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { sentenceChunks } from "./storyboardUtils";

export interface SplitSceneDialogProps {
  open: boolean;
  sceneNumber: number;
  narration: string;
  /** how many script sentences the scene covers; one sentence cannot be split here */
  sentenceCount: number;
  onConfirm: (afterChunk: number) => void;
  onCancel: () => void;
}

/** Pick the sentence after which the scene is cut in two. */
export function SplitSceneDialog({ open, sceneNumber, narration, sentenceCount, onConfirm, onCancel }: SplitSceneDialogProps) {
  const chunks = sentenceChunks(narration);
  const canSplit = chunks.length >= 2 && sentenceCount >= 2;
  const [after, setAfter] = useState(1);

  // Start in the middle each time the dialog opens for a scene.
  useEffect(() => {
    if (open) setAfter(Math.max(1, Math.ceil(sentenceChunks(narration).length / 2)));
  }, [open, narration]);

  return (
    <Dialog
      open={open}
      title={`Split scene ${sceneNumber}`}
      description={
        sentenceCount < 2
          ? "This scene covers one sentence of the script, so it cannot be split here. Split the sentence in the script stage (edit the script and approve), then regenerate the storyboard."
          : chunks.length < 2
            ? "The narration of this scene reads as one sentence, so there is no place to split it."
            : "Click the sentence that should end the first scene. Everything after it becomes a new scene with its own image."
      }
      onClose={onCancel}
      className="max-w-lg"
      footer={
        <>
          <Button variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="primary" icon={<Scissors />} disabled={!canSplit} onClick={() => onConfirm(after)}>
            Split here
          </Button>
        </>
      }
    >
      {canSplit ? (
        <ol className="flex max-h-[50vh] flex-col gap-1 overflow-y-auto pr-1">
          {chunks.map((chunk, index) => {
            const number = index + 1;
            const inFirst = number <= after;
            const isCut = number === after;
            return (
              <li key={index}>
                <button
                  type="button"
                  disabled={number === chunks.length}
                  onClick={() => setAfter(number)}
                  className={cn(
                    "flex w-full items-start gap-3 rounded-md border px-3 py-2 text-left text-[13px] leading-6 transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:cursor-default",
                    inFirst ? "border-line bg-surface-2/60 text-ink" : "border-line/60 text-ink-muted",
                    isCut && "border-accent/60",
                  )}
                >
                  <span className="mt-1 shrink-0 rounded bg-surface px-1.5 text-[11px] font-semibold tabular-nums text-ink-muted">
                    {inFirst ? "A" : "B"}
                  </span>
                  <span>{chunk}</span>
                </button>
                {isCut ? (
                  <div className="my-1 flex items-center gap-2 px-2 text-[11px] font-semibold uppercase tracking-wide text-accent-text">
                    <span className="h-px flex-1 bg-accent/50" />
                    cut here
                    <span className="h-px flex-1 bg-accent/50" />
                  </div>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : null}
    </Dialog>
  );
}

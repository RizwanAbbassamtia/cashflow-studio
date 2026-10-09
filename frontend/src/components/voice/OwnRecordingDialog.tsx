import { FileAudio, Upload, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Textarea } from "../ui/Input";
import { Notice } from "../ui/States";
import { FilePickButton } from "./FilePickButton";
import { AUDIO_FILE_ACCEPT, formatBytes } from "./voiceUtils";

export interface OwnRecordingChoice {
  file: File;
  note: string;
  /** true: the voice step runs again with the file and waits for another review */
  reviewAgain: boolean;
}

export interface OwnRecordingDialogProps {
  open: boolean;
  loading?: boolean;
  /** true when the aligner is set up, so the dialog can say the timings will be exact */
  alignerAvailable?: boolean;
  onConfirm: (choice: OwnRecordingChoice) => void;
  onCancel: () => void;
}

/** "Use my own recording": pick an audio file, say what happens next, confirm. */
export function OwnRecordingDialog({ open, loading = false, alignerAvailable = false, onConfirm, onCancel }: OwnRecordingDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const [reviewAgain, setReviewAgain] = useState(true);

  useEffect(() => {
    if (open) {
      setFile(null);
      setNote("");
      setReviewAgain(true);
    }
  }, [open]);

  return (
    <Dialog
      open={open}
      title="Use my own recording"
      description="Replace the voice tool's narration with a recording of the script read by a person."
      onClose={onCancel}
      className="max-w-lg"
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={loading}>
            Cancel
          </Button>
          <Button variant="primary" icon={<Upload />} loading={loading} disabled={!file} onClick={() => file && onConfirm({ file, note: note.trim(), reviewAgain })}>
            {reviewAgain ? "Upload and check the timing" : "Upload and continue"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Notice tone="info">
          The app converts the file to 48 kHz mono WAV and lines the script sentences up with it.{" "}
          {alignerAvailable
            ? "The speech aligner finds where each word is spoken."
            : "Without a speech aligner the timings are estimated from the text and marked low confidence, so check captions in the edit preview."}{" "}
          Read the script exactly as written: the sentences must match.
        </Notice>

        <div className="flex flex-wrap items-center gap-3">
          <FilePickButton accept={AUDIO_FILE_ACCEPT} icon={<FileAudio />} onPick={setFile} disabled={loading}>
            {file ? "Choose another file" : "Choose audio file"}
          </FilePickButton>
          {file ? (
            <span className="flex min-w-0 items-center gap-2 text-[13px] text-ink">
              <span className="truncate" title={file.name}>
                {file.name}
              </span>
              <span className="shrink-0 text-xs text-ink-muted">{formatBytes(file.size)}</span>
              <button type="button" aria-label="Remove the chosen file" className="rounded p-0.5 text-ink-faint hover:bg-surface-2 hover:text-ink" onClick={() => setFile(null)} disabled={loading}>
                <X className="size-3.5" aria-hidden />
              </button>
            </span>
          ) : (
            <span className="text-xs text-ink-faint">WAV, MP3, M4A, FLAC or OGG</span>
          )}
        </div>

        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-ink-muted">Note for the project log (optional)</span>
          <Textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} placeholder="For example: recorded by Sam on the studio mic." disabled={loading} />
        </label>

        <label className="flex items-start gap-2 text-[13px] text-ink">
          <input type="checkbox" className="mt-0.5 accent-accent" checked={reviewAgain} onChange={(event) => setReviewAgain(event.target.checked)} disabled={loading} />
          <span>
            Stay on this step so I can check the timings first.
            <span className="block text-xs text-ink-muted">
              Unticked, the recording is used right away and the project moves on to the images stage.
            </span>
          </span>
        </label>
      </div>
    </Dialog>
  );
}

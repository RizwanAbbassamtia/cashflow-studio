import { Mic } from "lucide-react";
import { useEffect, useState } from "react";

import type { VoiceReviewSentence } from "../../types/timing";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Textarea } from "../ui/Input";

export interface ReRecordChoice {
  note: string;
  /** true: the voice step runs again and waits for another review */
  reviewAgain: boolean;
}

export interface ReRecordDialogProps {
  open: boolean;
  /** the ticked sentences, with their 1-based position in the list */
  sentences: Array<{ position: number; sentence: VoiceReviewSentence }>;
  loading?: boolean;
  onConfirm: (choice: ReRecordChoice) => void;
  onCancel: () => void;
}

/** "Re-record selected sentences": shows what goes back to the voice tool and confirms. */
export function ReRecordDialog({ open, sentences, loading = false, onConfirm, onCancel }: ReRecordDialogProps) {
  const [note, setNote] = useState("");
  const [reviewAgain, setReviewAgain] = useState(true);

  useEffect(() => {
    if (open) {
      setNote("");
      setReviewAgain(true);
    }
  }, [open]);

  const count = sentences.length;

  return (
    <Dialog
      open={open}
      title={count === 1 ? "Re-record one sentence" : `Re-record ${count} sentences`}
      description="Only the ticked sentences go back to the voice tool. The others keep their audio, so only the new takes cost anything."
      onClose={onCancel}
      className="max-w-lg"
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={loading}>
            Cancel
          </Button>
          <Button variant="primary" icon={<Mic />} loading={loading} disabled={count === 0} onClick={() => onConfirm({ note: note.trim(), reviewAgain })}>
            {reviewAgain ? "Re-record and listen again" : "Re-record and continue"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <ol className="max-h-48 overflow-y-auto rounded-md border border-line bg-surface-2/40 px-3 py-2 text-[13px]">
          {sentences.map(({ position, sentence }) => (
            <li key={sentence.id} className="flex gap-2 py-1">
              <span className="shrink-0 font-semibold tabular-nums text-ink-faint">{position}</span>
              <span className="line-clamp-2 text-ink">{sentence.text}</span>
            </li>
          ))}
        </ol>

        <label className="flex flex-col gap-1.5">
          <span className="text-[13px] font-medium text-ink-muted">Note for the project log (optional)</span>
          <Textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} placeholder="For example: the brand name was mispronounced." disabled={loading} />
        </label>

        <label className="flex items-start gap-2 text-[13px] text-ink">
          <input type="checkbox" className="mt-0.5 accent-accent" checked={reviewAgain} onChange={(event) => setReviewAgain(event.target.checked)} disabled={loading} />
          <span>
            Stay on this step so I can listen to the new takes first.
            <span className="block text-xs text-ink-muted">Unticked, the sentences are re-recorded and the project moves on to the images stage.</span>
          </span>
        </label>
      </div>
    </Dialog>
  );
}

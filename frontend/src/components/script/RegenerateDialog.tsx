import { RefreshCw } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Textarea } from "../ui/Input";

export interface RegenerateDialogProps {
  open: boolean;
  title: string;
  /** what the AI will redo and what it will keep, in plain English */
  description: ReactNode;
  placeholder?: string;
  confirmLabel?: string;
  loading?: boolean;
  /** when true the notes may be left empty */
  notesOptional?: boolean;
  onConfirm: (notes: string) => void;
  onCancel: () => void;
}

/** "Regenerate with notes" for the script and the storyboard: one textarea, one button. */
export function RegenerateDialog({
  open,
  title,
  description,
  placeholder = "What should change? For example: shorter hook, mention the price earlier, calmer tone.",
  confirmLabel = "Regenerate",
  loading = false,
  notesOptional = false,
  onConfirm,
  onCancel,
}: RegenerateDialogProps) {
  const [notes, setNotes] = useState("");

  useEffect(() => {
    if (open) setNotes("");
  }, [open]);

  const trimmed = notes.trim();
  const canConfirm = notesOptional || trimmed.length > 0;

  return (
    <Dialog
      open={open}
      title={title}
      description={description}
      onClose={onCancel}
      className="max-w-lg"
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={loading}>
            Cancel
          </Button>
          <Button variant="primary" icon={<RefreshCw />} loading={loading} disabled={!canConfirm} onClick={() => onConfirm(trimmed)}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <label className="flex flex-col gap-1.5">
        <span className="text-[13px] font-medium text-ink-muted">
          Notes for the AI{notesOptional ? " (optional)" : ""}
        </span>
        <Textarea
          rows={4}
          autoFocus
          value={notes}
          placeholder={placeholder}
          onChange={(event) => setNotes(event.target.value)}
        />
      </label>
    </Dialog>
  );
}

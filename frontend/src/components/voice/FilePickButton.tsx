import { useRef, type ReactNode } from "react";

import { buttonClasses, type ButtonSize, type ButtonVariant } from "../ui/Button";
import { Spinner } from "../ui/Spinner";

export interface FilePickButtonProps {
  /** the `accept` attribute of the file input, e.g. "image/png,image/jpeg" */
  accept: string;
  onPick: (file: File) => void;
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  loading?: boolean;
  disabled?: boolean;
  className?: string;
  title?: string;
  children: ReactNode;
}

/**
 * A button that opens the file chooser. The app hides native file inputs (index.css), so
 * the input stays in the DOM only to receive the click; the same file may be picked twice
 * in a row because the input is cleared after every pick.
 */
export function FilePickButton({ accept, onPick, variant = "secondary", size = "md", icon, loading = false, disabled = false, className, title, children }: FilePickButtonProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <button
        type="button"
        title={title}
        disabled={disabled || loading}
        className={buttonClasses(variant, size, className)}
        onClick={() => inputRef.current?.click()}
      >
        {loading ? <Spinner className="size-4" /> : icon}
        {children}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        tabIndex={-1}
        aria-hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onPick(file);
          event.target.value = "";
        }}
      />
    </>
  );
}

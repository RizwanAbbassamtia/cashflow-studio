import { useLayoutEffect, useRef, type CSSProperties } from "react";

import { cn } from "../../lib/cn";
import { Textarea, type TextareaProps } from "../ui/Input";

const NO_RESIZE: CSSProperties = { resize: "none" };

/**
 * A textarea that grows with its content, for narration, prompts and script paragraphs.
 * It re-measures when the text changes, when its width changes (window resize, a column
 * appearing) and once the web font has loaded, so the first paint never leaves a gap.
 */
export function AutoTextarea({ className, value, style, ...props }: TextareaProps) {
  const ref = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;

    const fit = () => {
      element.style.height = "0px";
      element.style.height = `${element.scrollHeight + 2}px`;
    };
    fit();

    let lastWidth = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth !== lastWidth) {
        lastWidth = element.clientWidth;
        fit();
      }
    });
    observer.observe(element);
    let cancelled = false;
    void document.fonts?.ready.then(() => {
      if (!cancelled) fit();
    });
    return () => {
      cancelled = true;
      observer.disconnect();
    };
  }, [value]);

  return (
    <Textarea
      ref={ref}
      value={value}
      rows={2}
      style={{ ...NO_RESIZE, ...style }}
      className={cn("overflow-hidden text-[13px]", className)}
      {...props}
    />
  );
}

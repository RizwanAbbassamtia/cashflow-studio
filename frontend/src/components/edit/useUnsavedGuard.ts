import { useCallback, useEffect, useRef } from "react";
import { useBlocker, type BlockerFunction } from "react-router";

/**
 * Stops in-app navigation and closing the window while a review panel holds unsaved
 * edits. Returns the router blocker; show a confirm dialog when `blocker.state` is
 * "blocked" and call `proceed()` or `reset()`.
 */
export function useUnsavedGuard(dirty: boolean) {
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  const blocker = useBlocker(
    useCallback<BlockerFunction>(({ currentLocation, nextLocation }) => dirtyRef.current && currentLocation.pathname !== nextLocation.pathname, []),
  );
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);
  return blocker;
}

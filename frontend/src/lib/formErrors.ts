/** Read the message for a dotted path ("competitors.0.url") out of a react-hook-form errors tree. */
export function errorAt(errors: unknown, path: string | undefined): string | undefined {
  if (!path || typeof errors !== "object" || errors === null) return undefined;
  let node: unknown = errors;
  for (const part of path.split(".")) {
    if (typeof node !== "object" || node === null) return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  if (typeof node !== "object" || node === null) return undefined;
  const message = (node as { message?: unknown }).message;
  return typeof message === "string" ? message : undefined;
}

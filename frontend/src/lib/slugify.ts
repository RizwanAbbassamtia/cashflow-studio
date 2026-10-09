/**
 * Folder-name preview for a channel name. The server derives the final slug with
 * python-slugify when the body has none; this version matches it for Latin names and
 * returns "" for names it cannot handle, so the form then leaves the slug to the server.
 */
export function slugify(input: string): string {
  return input
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-");
}

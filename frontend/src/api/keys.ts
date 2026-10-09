/** react-query cache keys, one place so invalidation stays consistent. */
export const queryKeys = {
  channels: ["channels"] as const,
  channel: (slug: string) => ["channels", slug] as const,
  settings: ["settings"] as const,
  doctor: ["doctor"] as const,
  system: ["system"] as const,
};

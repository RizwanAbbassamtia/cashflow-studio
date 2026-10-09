/** react-query cache keys, one place so invalidation stays consistent. */
export const queryKeys = {
  channels: ["channels"] as const,
  channel: (slug: string) => ["channels", slug] as const,
  settings: ["settings"] as const,
  doctor: ["doctor"] as const,
  system: ["system"] as const,

  // Projects: ["projects"] invalidates every list, detail and stage payload at once.
  projects: ["projects"] as const,
  projectLists: ["projects", "list"] as const,
  projectList: (params: { channel_slug?: string; status?: string }) =>
    ["projects", "list", params.channel_slug ?? "", params.status ?? ""] as const,
  project: (id: string) => ["projects", "detail", id] as const,
  stagePayload: (id: string, stage: string) => ["projects", "detail", id, "stage", stage] as const,

  // Research: ["research", slug] invalidates every candidates query of a channel.
  research: ["research"] as const,
  researchChannel: (slug: string) => ["research", slug] as const,
  candidates: (slug: string, format: string, limit: number) =>
    ["research", slug, "candidates", format, limit] as const,

  job: (id: string) => ["jobs", id] as const,
};

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { ProjectFormat } from "../types/project";
import type { CandidatesResponse, ScanRequest, ScanStarted } from "../types/research";
import { api, ApiError } from "./client";
import { queryKeys } from "./keys";

export const DEFAULT_CANDIDATE_LIMIT = 50;

// ---- plain functions ------------------------------------------------------

/** Starts a scan of every competitor of the channel; returns the job to poll. */
export function startScan(slug: string, body: ScanRequest = {}): Promise<ScanStarted> {
  return api.post<ScanStarted>(`/api/channels/${encodeURIComponent(slug)}/research/scan`, body);
}

/** Ranked candidates across all competitors of the channel, from the last scan. */
export function getCandidates(
  slug: string,
  params: { format: ProjectFormat; limit?: number },
): Promise<CandidatesResponse> {
  const query = new URLSearchParams({ format: params.format, limit: String(params.limit ?? DEFAULT_CANDIDATE_LIMIT) });
  return api.get<CandidatesResponse>(`/api/channels/${encodeURIComponent(slug)}/research/candidates?${query.toString()}`);
}

// ---- hooks ----------------------------------------------------------------

export function useCandidates(slug: string | undefined, format: ProjectFormat, limit = DEFAULT_CANDIDATE_LIMIT) {
  return useQuery({
    queryKey: queryKeys.candidates(slug ?? "", format, limit),
    queryFn: () => getCandidates(slug as string, { format, limit }),
    enabled: Boolean(slug),
    // A scan refreshes this through invalidation; otherwise the ranking does not move.
    staleTime: 30_000,
    // 404 means "no scan yet" (or the channel is gone): show that at once instead of retrying.
    retry: (failureCount, error) => !(error instanceof ApiError && error.status === 404) && failureCount < 1,
  });
}

export function useStartScan() {
  return useMutation({
    mutationFn: ({ slug, body }: { slug: string; body?: ScanRequest }) => startScan(slug, body ?? {}),
  });
}

/** Call when a scan job finishes so the candidates and the competitor statuses refresh. */
export function useRefreshResearch() {
  const queryClient = useQueryClient();
  return (slug: string) => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.researchChannel(slug) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.channel(slug) });
  };
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type {
  Channel,
  ChannelCreate,
  ChannelSummary,
  Framework,
  FrameworkType,
  UrlValidation,
} from "../types";
import { api } from "./client";
import { queryKeys } from "./keys";

// ---- plain functions ------------------------------------------------------

export function listChannels(): Promise<ChannelSummary[]> {
  return api.get<ChannelSummary[]>("/api/channels");
}

export function getChannel(slug: string): Promise<Channel> {
  return api.get<Channel>(`/api/channels/${encodeURIComponent(slug)}`);
}

export function createChannel(body: ChannelCreate): Promise<Channel> {
  return api.post<Channel>("/api/channels", body);
}

export function updateChannel(slug: string, body: Channel): Promise<Channel> {
  return api.put<Channel>(`/api/channels/${encodeURIComponent(slug)}`, body);
}

/** Moves the channel folder to channels/_archived/<slug>-<timestamp>; never hard-deletes. */
export function archiveChannel(slug: string): Promise<void> {
  return api.delete(`/api/channels/${encodeURIComponent(slug)}`);
}

export function uploadFramework(slug: string, file: File, type: FrameworkType): Promise<Framework> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("type", type);
  return api.upload<Framework>(`/api/channels/${encodeURIComponent(slug)}/frameworks/upload`, form);
}

/** Syntactic check only in M0 (no network call on the server side). */
export function validateUrl(url: string): Promise<UrlValidation> {
  return api.get<UrlValidation>(`/api/channels/validate-url?url=${encodeURIComponent(url)}`);
}

// ---- hooks ----------------------------------------------------------------

export function useChannels() {
  return useQuery({ queryKey: queryKeys.channels, queryFn: listChannels });
}

export function useChannel(slug: string | undefined) {
  return useQuery({
    queryKey: queryKeys.channel(slug ?? ""),
    queryFn: () => getChannel(slug as string),
    enabled: Boolean(slug),
  });
}

export function useCreateChannel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createChannel,
    onSuccess: (channel) => {
      queryClient.setQueryData(queryKeys.channel(channel.slug), channel);
      void queryClient.invalidateQueries({ queryKey: queryKeys.channels });
    },
  });
}

export function useUpdateChannel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, body }: { slug: string; body: Channel }) => updateChannel(slug, body),
    onSuccess: (channel) => {
      queryClient.setQueryData(queryKeys.channel(channel.slug), channel);
      void queryClient.invalidateQueries({ queryKey: queryKeys.channels });
    },
  });
}

export function useArchiveChannel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: archiveChannel,
    onSuccess: (_result, slug) => {
      queryClient.removeQueries({ queryKey: queryKeys.channel(slug) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.channels });
    },
  });
}

export function useUploadFramework() {
  return useMutation({
    mutationFn: ({ slug, file, type }: { slug: string; file: File; type: FrameworkType }) =>
      uploadFramework(slug, file, type),
  });
}

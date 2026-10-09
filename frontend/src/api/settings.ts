import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { resolveModelSettings, resolveRenderSettings, type ModelSettings, type RenderSettingsResolved, type Settings, type SettingsUpdate } from "../types";
import { api } from "./client";
import { queryKeys } from "./keys";

export function getSettings(): Promise<Settings> {
  return api.get<Settings>("/api/settings");
}

/** Only the fields present are changed. Key values are sent once and never read back. */
export function updateSettings(body: SettingsUpdate): Promise<Settings> {
  return api.put<Settings>("/api/settings", body);
}

export function useSettings() {
  return useQuery({ queryKey: queryKeys.settings, queryFn: getSettings });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateSettings,
    onSuccess: (settings) => {
      queryClient.setQueryData(queryKeys.settings, settings);
      // Paths and keys change what the doctor and system info report.
      void queryClient.invalidateQueries({ queryKey: queryKeys.system });
      void queryClient.invalidateQueries({ queryKey: queryKeys.doctor });
    },
  });
}

/**
 * The nested `llm`, `research`, `pipeline` and `voice` settings (M2, contract section 11)
 * with defaults filled in, so screens can rely on every field even on an older backend.
 */
export function useModelSettings(): { query: ReturnType<typeof useSettings>; resolved: ModelSettings } {
  const query = useSettings();
  const resolved = useMemo(() => resolveModelSettings(query.data), [query.data]);
  return { query, resolved };
}

/**
 * The nested `render` and `captions` settings (M4, docs/M3-M4-CONTRACT.md section 5) with
 * defaults filled in: default presets, x264 preset, 4K on/off, captions on/off and style.
 */
export function useRenderSettings(): { query: ReturnType<typeof useSettings>; resolved: RenderSettingsResolved } {
  const query = useSettings();
  const resolved = useMemo(() => resolveRenderSettings(query.data), [query.data]);
  return { query, resolved };
}

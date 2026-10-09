import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { Settings, SettingsUpdate } from "../types";
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

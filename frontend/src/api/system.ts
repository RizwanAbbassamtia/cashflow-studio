import { useQuery } from "@tanstack/react-query";

import type { SystemInfo } from "../types";
import { api } from "./client";
import { queryKeys } from "./keys";

export function getSystemInfo(): Promise<SystemInfo> {
  return api.get<SystemInfo>("/api/system/info");
}

export function useSystemInfo() {
  return useQuery({ queryKey: queryKeys.system, queryFn: getSystemInfo, staleTime: 60_000 });
}

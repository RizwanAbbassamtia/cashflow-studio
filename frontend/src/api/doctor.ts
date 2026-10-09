import { useQuery } from "@tanstack/react-query";

import type { DoctorReport } from "../types";
import { api } from "./client";
import { queryKeys } from "./keys";

export function getDoctor(): Promise<DoctorReport> {
  return api.get<DoctorReport>("/api/doctor");
}

/** The doctor runs real checks (FFmpeg, folders, disk space), so it is cached for a while. */
export function useDoctor(options: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: queryKeys.doctor,
    queryFn: getDoctor,
    staleTime: 60_000,
    refetchInterval: options.refetchInterval ?? false,
  });
}

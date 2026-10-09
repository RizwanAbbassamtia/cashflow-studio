import { Film } from "lucide-react";

import { PlaceholderPage } from "./PlaceholderPage";

export interface ProjectsPageProps {
  /** when set, only projects currently at this stage are listed */
  stageFilter?: string;
}

/** Stub owned by the M1/M2 front-end agent; replaced in that milestone. */
export function ProjectsPage(_props: ProjectsPageProps) {
  return <PlaceholderPage title="Projects" milestone="M1" icon={Film} summary="Every video in production with its current stage and status." />;
}

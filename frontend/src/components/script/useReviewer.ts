import { useChannel } from "../../api/channels";
import { useProject } from "../../api/projects";
import { useReviewerName } from "../projects/useReviewerName";

/**
 * Who approves, for a panel that only knows the project id: the name remembered on this
 * computer (shared with the project page through `useReviewerName`), else the channel's
 * reviewer, else "Reviewer", so `by` is never empty.
 */
export function useReviewer(projectId: string): { name: string; setName: (name: string) => void; effective: string } {
  const project = useProject(projectId);
  const channel = useChannel(project.data?.channel_slug);
  return useReviewerName(channel.data?.reviewer ?? "");
}

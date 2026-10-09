import { SearchX } from "lucide-react";
import { useParams } from "react-router";

import { useChannel } from "../api/channels";
import { ApiError } from "../api/client";
import { ChannelForm } from "../components/channels/ChannelForm";
import { LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { LoadingBlock } from "../components/ui/Spinner";
import { EmptyState, ErrorState } from "../components/ui/States";
import { defaultChannel } from "../types";

/** /channels/new and /channels/:slug share this page; the form remounts per slug. */
export function ChannelSetupPage() {
  const { slug } = useParams<{ slug: string }>();
  const channel = useChannel(slug);

  if (!slug) {
    return <ChannelForm key="new" mode="new" initial={defaultChannel()} />;
  }

  if (channel.isPending) {
    return (
      <Card>
        <LoadingBlock label="Loading channel..." />
      </Card>
    );
  }

  if (channel.isError) {
    const notFound = channel.error instanceof ApiError && channel.error.status === 404;
    return (
      <Card>
        {notFound ? (
          <EmptyState
            icon={SearchX}
            title="This channel does not exist"
            description={`There is no channel with the folder name "${slug}". It may have been archived or renamed.`}
            action={
              <LinkButton to="/channels" variant="secondary">
                Back to channels
              </LinkButton>
            }
          />
        ) : (
          <ErrorState error={channel.error} title="Could not load the channel" onRetry={() => void channel.refetch()} />
        )}
      </Card>
    );
  }

  return <ChannelForm key={slug} mode="edit" initial={channel.data} />;
}

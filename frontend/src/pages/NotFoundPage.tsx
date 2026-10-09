import { Compass } from "lucide-react";
import { useLocation } from "react-router";

import { LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState } from "../components/ui/States";

export function NotFoundPage() {
  const location = useLocation();
  return (
    <Card className="mx-auto max-w-xl">
      <EmptyState
        icon={Compass}
        title="There is no page here"
        description={`Nothing lives at ${location.pathname}.`}
        action={
          <LinkButton to="/" variant="primary">
            Go to the Dashboard
          </LinkButton>
        }
      />
    </Card>
  );
}

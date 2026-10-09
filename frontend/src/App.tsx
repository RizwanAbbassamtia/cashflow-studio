import { Scissors } from "lucide-react";
import { createBrowserRouter, isRouteErrorResponse, Link, RouterProvider, useRouteError } from "react-router";

import { AppShell } from "./layout/AppShell";
import { ChannelSetupPage } from "./pages/ChannelSetupPage";
import { ChannelsPage } from "./pages/ChannelsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ResearchPage } from "./pages/ResearchPage";
import { ReviewQueuePage } from "./pages/ReviewQueuePage";
import { SettingsPage } from "./pages/SettingsPage";

/** Shown when a page throws while rendering, so the app never goes blank. */
function RouteError() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : "Something went wrong on this page.";
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas p-8 text-ink">
      <div className="max-w-md rounded-card border border-line bg-surface p-8 text-center shadow-card">
        <h1 className="text-lg font-semibold">This page hit a problem</h1>
        <p className="mt-2 text-sm text-ink-muted">{message}</p>
        <Link to="/" className="mt-6 inline-flex h-9 items-center rounded-md bg-accent px-4 text-sm font-semibold text-accent-ink hover:bg-accent-hover">
          Back to the Dashboard
        </Link>
      </div>
    </div>
  );
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <DashboardPage />, handle: { title: "Dashboard" } },
      { path: "channels", element: <ChannelsPage />, handle: { title: "Channels" } },
      { path: "channels/new", element: <ChannelSetupPage />, handle: { title: "Channel Setup" } },
      { path: "channels/:slug", element: <ChannelSetupPage />, handle: { title: "Channel Setup" } },
      { path: "settings", element: <SettingsPage />, handle: { title: "Settings" } },
      { path: "research", element: <ResearchPage />, handle: { title: "Research" } },
      { path: "research/:slug", element: <ResearchPage />, handle: { title: "Research" } },
      { path: "projects", element: <ProjectsPage />, handle: { title: "Projects" } },
      { path: "projects/:id", element: <ProjectPage />, handle: { title: "Project" } },
      { path: "storyboard", element: <ProjectsPage stageFilter="storyboard" />, handle: { title: "Storyboard" } },
      {
        path: "editor",
        handle: { title: "Editor" },
        element: (
          <PlaceholderPage
            title="Editor"
            milestone="M5"
            icon={Scissors}
            summary="A timeline editor for the common fixes: trim, swap an image, move a popup, change music, then render with FFmpeg or hand off to Kdenlive, DaVinci Resolve or CapCut."
          />
        ),
      },
      { path: "review", element: <ReviewQueuePage />, handle: { title: "Review" } },
      { path: "*", element: <NotFoundPage />, handle: { title: "Page not found" } },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}

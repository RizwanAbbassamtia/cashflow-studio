import { LayoutGrid, ListChecks, Scissors, Search } from "lucide-react";
import { createBrowserRouter, isRouteErrorResponse, Link, RouterProvider, useRouteError } from "react-router";

import { AppShell } from "./layout/AppShell";
import { ChannelSetupPage } from "./pages/ChannelSetupPage";
import { ChannelsPage } from "./pages/ChannelsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
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
      {
        path: "research",
        handle: { title: "Research" },
        element: (
          <PlaceholderPage
            title="Research"
            milestone="M1"
            icon={Search}
            summary="Scans each channel's competitors for outlier videos, pulls transcripts and thumbnails, and lets you pick the ideas worth making."
            meanwhile={{ label: "Add competitors to a channel", to: "/channels" }}
          />
        ),
      },
      {
        path: "storyboard",
        handle: { title: "Storyboard" },
        element: (
          <PlaceholderPage
            title="Storyboard"
            milestone="M2"
            icon={LayoutGrid}
            summary="One card per scene: narration, image prompt, generated image, popup text, motion and transition. Lock a scene and the AI never overwrites it."
            meanwhile={{ label: "Set image and voice rules per channel", to: "/channels" }}
          />
        ),
      },
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
      {
        path: "review",
        handle: { title: "Review" },
        element: (
          <PlaceholderPage
            title="Review"
            milestone="M5"
            icon={ListChecks}
            summary="The queue of stages waiting for a human: approve, edit or send back each title, script, storyboard, image set and export."
            meanwhile={{ label: "Choose which stages need review per channel", to: "/channels" }}
          />
        ),
      },
      { path: "*", element: <NotFoundPage />, handle: { title: "Page not found" } },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}

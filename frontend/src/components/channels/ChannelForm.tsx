import { zodResolver } from "@hookform/resolvers/zod";
import { Archive, Save } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { FormProvider, useForm, useWatch, type FieldErrors } from "react-hook-form";
import { useBlocker, useNavigate, type BlockerFunction } from "react-router";

import { useArchiveChannel, useCreateChannel, useUpdateChannel } from "../../api/channels";
import { ApiError, errorMessage } from "../../api/client";
import {
  applyServerErrors,
  CHANNEL_TABS,
  channelFormSchema,
  channelToForm,
  formToChannel,
  tabForPath,
  type ChannelFormValues,
  type ChannelTabId,
} from "../../lib/channelForm";
import { cn } from "../../lib/cn";
import { formatRelative } from "../../lib/format";
import { slugify } from "../../lib/slugify";
import type { Channel } from "../../types";
import { Badge, ChannelStatusBadge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { ConfirmDialog } from "../ui/Dialog";
import { PageHeader } from "../ui/PageHeader";
import { Notice } from "../ui/States";
import { Tabs } from "../ui/Tabs";
import { useToast } from "../ui/Toast";
import { ChannelTab } from "./tabs/ChannelTab";
import { CompetitorsTab } from "./tabs/CompetitorsTab";
import { FrameworksTab } from "./tabs/FrameworksTab";
import { ImagesTab } from "./tabs/ImagesTab";
import { StageModesTab } from "./tabs/StageModesTab";
import { ThumbnailTab } from "./tabs/ThumbnailTab";
import { VoiceTab } from "./tabs/VoiceTab";

export interface ChannelFormProps {
  mode: "new" | "edit";
  /** the saved channel, or defaults for a new one */
  initial: Channel;
}

export function ChannelForm({ mode, initial }: ChannelFormProps) {
  const navigate = useNavigate();
  const { toast } = useToast();
  const create = useCreateChannel();
  const update = useUpdateChannel();
  const archive = useArchiveChannel();

  const form = useForm<ChannelFormValues>({
    resolver: zodResolver(channelFormSchema),
    defaultValues: channelToForm(initial),
    mode: "onTouched",
  });
  const { control, handleSubmit, formState, reset, setError, setValue } = form;
  const { isDirty, isSubmitting, errors } = formState;

  const [activeTab, setActiveTab] = useState<ChannelTabId>("channel");
  const [serverMessages, setServerMessages] = useState<string[]>([]);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const slugEdited = useRef(mode === "edit");
  const skipGuard = useRef(false);

  const name = useWatch({ control, name: "channel.name" });
  const status = useWatch({ control, name: "channel.status" });
  const competitors = useWatch({ control, name: "competitors" });
  const frameworks = useWatch({ control, name: "frameworks" });

  // New channels: the folder name follows the channel name until the user edits it by hand.
  useEffect(() => {
    if (mode === "new" && !slugEdited.current) {
      setValue("slug", slugify(name ?? ""));
    }
  }, [mode, name, setValue]);

  // Unsaved-changes guard: in-app navigation and closing the window. The blocker reads refs so
  // that a save handler can set skipGuard and navigate in the same tick, before the next render.
  const dirtyRef = useRef(false);
  dirtyRef.current = isDirty;
  const blocker = useBlocker(
    useCallback<BlockerFunction>(
      ({ currentLocation, nextLocation }) =>
        dirtyRef.current && !skipGuard.current && currentLocation.pathname !== nextLocation.pathname,
      [],
    ),
  );
  useEffect(() => {
    if (!isDirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!skipGuard.current) event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [isDirty]);

  const onValid = async (values: ChannelFormValues) => {
    setServerMessages([]);
    const body = formToChannel(values);
    try {
      if (mode === "new") {
        const saved = await create.mutateAsync(body);
        skipGuard.current = true;
        reset(channelToForm(saved));
        toast({
          tone: "success",
          title: `Channel "${saved.channel.name}" created`,
          description: `Saved as channels/${saved.slug}/channel.json in the shared folder.`,
        });
        navigate(`/channels/${encodeURIComponent(saved.slug)}`, { replace: true });
      } else {
        const saved = await update.mutateAsync({ slug: initial.slug, body: { ...body, slug: initial.slug } });
        reset(channelToForm(saved));
        toast({ tone: "success", title: "Channel saved", description: `${saved.channel.name} is up to date.` });
      }
    } catch (error) {
      if (error instanceof ApiError) {
        const { firstTab, unassigned } = applyServerErrors(error, setError);
        if (firstTab) setActiveTab(firstTab);
        setServerMessages(unassigned.length > 0 ? unassigned : [error.message]);
      } else {
        setServerMessages([errorMessage(error)]);
      }
    }
  };

  const onInvalid = (invalid: FieldErrors<ChannelFormValues>) => {
    setServerMessages(["Some fields need attention. They are marked in red, and the tabs with problems show a red dot."]);
    const firstTab = Object.keys(invalid)
      .map(tabForPath)
      .find((tab): tab is ChannelTabId => tab !== null);
    if (firstTab) setActiveTab(firstTab);
  };

  const onArchive = async () => {
    try {
      await archive.mutateAsync(initial.slug);
      skipGuard.current = true;
      setArchiveOpen(false);
      toast({
        tone: "success",
        title: `Archived ${initial.channel.name}`,
        description: "The folder was moved to channels/_archived in the shared folder. Nothing was deleted.",
      });
      navigate("/channels");
    } catch (error) {
      setArchiveOpen(false);
      toast({ tone: "error", title: "Could not archive the channel", description: errorMessage(error) });
    }
  };

  const errorTabs = new Set(Object.keys(errors).map(tabForPath));
  const tabs = CHANNEL_TABS.map((tab) => ({
    id: tab.id,
    label: tab.label,
    hasError: errorTabs.has(tab.id),
    ...(tab.id === "competitors" ? { count: competitors?.length ?? 0 } : {}),
    ...(tab.id === "frameworks" ? { count: frameworks?.length ?? 0 } : {}),
  }));

  const title = mode === "new" ? "New channel" : name?.trim() || initial.channel.name;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={title}
        description={
          mode === "new" ? (
            "Fill in the channel once. Every stage of the pipeline reads these settings."
          ) : (
            <>
              Folder <span className="font-mono text-ink">channels/{initial.slug}</span>
              {initial.updated_at ? <> - last saved {formatRelative(initial.updated_at)}</> : null}
            </>
          )
        }
        actions={
          <>
            {mode === "edit" ? (
              <Button variant="ghost" icon={<Archive />} onClick={() => setArchiveOpen(true)}>
                Archive
              </Button>
            ) : null}
            <Button
              type="submit"
              form="channel-form"
              variant="primary"
              icon={<Save />}
              loading={isSubmitting}
              disabled={mode === "edit" && !isDirty}
            >
              {mode === "new" ? "Create channel" : "Save changes"}
            </Button>
          </>
        }
      >
        <div className="mt-2 flex items-center gap-2">
          <ChannelStatusBadge status={status ?? initial.channel.status} />
          {isDirty ? <Badge tone="warn">Unsaved changes</Badge> : null}
        </div>
      </PageHeader>

      {serverMessages.length > 0 ? (
        <Notice tone="fail" title="The channel was not saved">
          <ul className={cn(serverMessages.length > 1 && "list-disc pl-4")}>
            {serverMessages.map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        </Notice>
      ) : null}

      <FormProvider {...form}>
        <form id="channel-form" noValidate onSubmit={handleSubmit(onValid, onInvalid)}>
          <Card flush>
          <div className="px-5">
            <Tabs tabs={tabs} value={activeTab} onChange={setActiveTab} />
          </div>
          <div className="px-6 py-6">
            <div className={cn(activeTab !== "channel" && "hidden")} role="tabpanel">
              <ChannelTab form={form} mode={mode} onSlugEdited={() => (slugEdited.current = true)} />
            </div>
            <div className={cn(activeTab !== "competitors" && "hidden")} role="tabpanel">
              <CompetitorsTab form={form} />
            </div>
            <div className={cn(activeTab !== "frameworks" && "hidden")} role="tabpanel">
              <FrameworksTab form={form} mode={mode} slug={initial.slug} />
            </div>
            <div className={cn(activeTab !== "voice" && "hidden")} role="tabpanel">
              <VoiceTab form={form} />
            </div>
            <div className={cn(activeTab !== "images" && "hidden")} role="tabpanel">
              <ImagesTab form={form} />
            </div>
            <div className={cn(activeTab !== "thumbnail" && "hidden")} role="tabpanel">
              <ThumbnailTab form={form} />
            </div>
            <div className={cn(activeTab !== "stage_modes" && "hidden")} role="tabpanel">
              <StageModesTab form={form} />
            </div>
          </div>
          </Card>
        </form>
      </FormProvider>

      <ConfirmDialog
        open={blocker.state === "blocked"}
        title="Leave without saving?"
        description="Your changes to this channel have not been saved yet."
        confirmLabel="Leave without saving"
        cancelLabel="Keep editing"
        tone="danger"
        onConfirm={() => {
          if (blocker.state === "blocked") blocker.proceed();
        }}
        onCancel={() => {
          if (blocker.state === "blocked") blocker.reset();
        }}
      />

      <ConfirmDialog
        open={archiveOpen}
        title={`Archive ${initial.channel.name}?`}
        description="The channel folder is moved to channels/_archived in the shared folder. Nothing is deleted, and it disappears from the channel list."
        confirmLabel="Archive channel"
        tone="danger"
        loading={archive.isPending}
        onConfirm={() => void onArchive()}
        onCancel={() => setArchiveOpen(false)}
      />
    </div>
  );
}

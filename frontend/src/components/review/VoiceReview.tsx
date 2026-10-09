import { Check, FileAudio, Mic, RefreshCw, RotateCcw, Volume2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError, errorMessage } from "../../api/client";
import { useProject } from "../../api/projects";
import { useApproveVoice, useRedoVoice, useUploadRecording, useVoicePayload } from "../../api/voice";
import { useStageProgress } from "../../api/ws";
import { TIMING_SOURCE_HELP, type VoiceApproveEdits } from "../../types/timing";
import { ProgressBar } from "../projects/ProgressBar";
import { formatUsd } from "../projects/stageMeta";
import { Panel } from "../script/Panel";
import { RegenerateDialog } from "../script/RegenerateDialog";
import { useReviewer } from "../script/useReviewer";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Input, Textarea } from "../ui/Input";
import { LoadingBlock } from "../ui/Spinner";
import { EmptyState, ErrorState, Notice } from "../ui/States";
import { useToast } from "../ui/Toast";
import { OwnRecordingDialog, type OwnRecordingChoice } from "../voice/OwnRecordingDialog";
import { ReRecordDialog, type ReRecordChoice } from "../voice/ReRecordDialog";
import { SentenceList } from "../voice/SentenceList";
import { ConfidenceBadge, TimingSourceBadge, confidenceHelp } from "../voice/TimingBadges";
import { VoicePlayer, type VoicePlayerHandle } from "../voice/VoicePlayer";
import { activeSentenceIndex, durationDelta, durationOutOfBand, formatDuration, sentenceNumbers, sumWordFlags } from "../voice/voiceUtils";

export interface VoiceReviewProps {
  projectId: string;
}

/**
 * The voice review: the narration player with a sentence strip, the sentence list (text,
 * start, end, per-sentence play), where the timings came from and how much to trust them,
 * the stage's warnings, "Re-record selected sentences" (`edits.re_record`), "Use my own
 * recording" (upload, then `edits.audio_path`) and Approve. Rendered inside the project
 * page's stage card, which shows status, failed gates and notes above it.
 */
export function VoiceReview({ projectId }: VoiceReviewProps) {
  const { toast } = useToast();
  const reviewer = useReviewer(projectId);
  const project = useProject(projectId);
  const progress = useStageProgress(projectId);
  const { query, payload, timing } = useVoicePayload(projectId);
  const approve = useApproveVoice(projectId);
  const redo = useRedoVoice(projectId);
  const upload = useUploadRecording(projectId);

  const playerRef = useRef<VoicePlayerHandle>(null);
  const [time, setTime] = useState(0);
  const [playingIndex, setPlayingIndex] = useState<number | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [reRecordOpen, setReRecordOpen] = useState(false);
  const [ownOpen, setOwnOpen] = useState(false);
  const [redoOpen, setRedoOpen] = useState(false);
  const [approveNotes, setApproveNotes] = useState("");
  // The server refuses an approval while a blocking check fails; "Approve anyway" overrides.
  const [overrideGates, setOverrideGates] = useState(false);
  const [blockedMessage, setBlockedMessage] = useState<string | null>(null);

  const sentences = useMemo(() => payload?.sentences ?? [], [payload]);

  // Ticks follow the payload: ids that vanish after a run are dropped.
  useEffect(() => {
    setSelected((current) => {
      const ids = new Set(sentences.map((sentence) => sentence.id));
      const next = new Set([...current].filter((id) => ids.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [sentences]);

  const stageStatus = project.data?.stages.voice?.status ?? "pending";
  const running = stageStatus === "running";
  const liveProgress = progress?.stage === "voice" ? progress : null;
  const busy = approve.isPending || redo.isPending || upload.isPending;
  const activeIndex = useMemo(() => activeSentenceIndex(sentences, time), [sentences, time]);
  const savedBlocking = payload?.gate_results.some((gate) => gate.severity === "block" && !gate.passed) ?? false;
  const showOverride = savedBlocking || blockedMessage !== null;

  const fail =(title: string, error: unknown) => toast({ tone: "error", title, description: errorMessage(error) });

  const onApproveError = (error: unknown) => {
    if (error instanceof ApiError && error.status === 409) {
      setBlockedMessage(errorMessage(error));
      void query.refetch();
    }
    fail("Could not approve the narration", error);
  };

  const approved = (description: string) => {
    toast({ tone: "success", title: "Narration approved", description });
    setApproveNotes("");
    setOverrideGates(false);
    setBlockedMessage(null);
    setSelected(new Set());
    setReRecordOpen(false);
    setOwnOpen(false);
  };

  const by = reviewer.effective;
  const notesOrUndefined = (text: string) => (text.trim() ? text.trim() : undefined);

  const onApprove = () => {
    const edits: VoiceApproveEdits = {};
    if (overrideGates && showOverride) edits.override_gates = true;
    approve.mutate(
      { by, notes: notesOrUndefined(approveNotes), edits: Object.keys(edits).length > 0 ? edits : undefined },
      { onSuccess: () => approved("The images stage is next."), onError: onApproveError },
    );
  };

  const selectedRows = sentences
    .map((sentence, index) => ({ position: index + 1, sentence }))
    .filter(({ sentence }) => selected.has(sentence.id));

  const onReRecord = ({ note, reviewAgain }: ReRecordChoice) => {
    const ids = selectedRows.map(({ sentence }) => sentence.id);
    if (ids.length === 0) return;
    const positions = selectedRows.map(({ position }) => position - 1);
    const edits: VoiceApproveEdits = { re_record: ids };
    if (reviewAgain) {
      redo.mutate(
        { by, notes: note || `Re-record ${sentenceNumbers(positions)}.`, edits },
        {
          onSuccess: () => {
            setReRecordOpen(false);
            setSelected(new Set());
            toast({ tone: "info", title: "Re-recording", description: `The voice tool makes ${sentenceNumbers(positions)} again. This list updates when it is done.` });
          },
          onError: (error) => fail("Could not start the re-record", error),
        },
      );
      return;
    }
    if (overrideGates && showOverride) edits.override_gates = true;
    approve.mutate(
      { by, notes: notesOrUndefined(note || approveNotes), edits },
      { onSuccess: () => approved(`${sentenceNumbers(positions)} re-recorded first; the images stage is next.`), onError: onApproveError },
    );
  };

  const onOwnRecording = async ({ file, note, reviewAgain }: OwnRecordingChoice) => {
    let path: string;
    try {
      const result = await upload.mutateAsync(file);
      path = result.path;
    } catch (error) {
      fail("Could not upload the recording", error);
      return;
    }
    const edits: VoiceApproveEdits = { audio_path: path };
    if (reviewAgain) {
      redo.mutate(
        { by, notes: note || `Use my own recording (${file.name}).`, edits },
        {
          onSuccess: () => {
            setOwnOpen(false);
            toast({ tone: "info", title: "Recording uploaded", description: "The voice step lines the script up with your recording. The timings appear here when it is done." });
          },
          onError: (error) => fail("Could not use the recording", error),
        },
      );
      return;
    }
    if (overrideGates && showOverride) edits.override_gates = true;
    approve.mutate(
      { by, notes: notesOrUndefined(note || approveNotes), edits },
      { onSuccess: () => approved("Your recording is used; the images stage is next."), onError: onApproveError },
    );
  };

  const onRedoAll = (notes: string) => {
    redo.mutate(
      { by, notes },
      {
        onSuccess: () => {
          setRedoOpen(false);
          toast({ tone: "info", title: "Recording the narration again", description: "Sentences whose text did not change come from the cache, so only new audio costs anything." });
        },
        onError: (error) => fail("Could not start the redo", error),
      },
    );
  };

  const playSentence = (index: number) => {
    const sentence = sentences[index];
    if (!sentence) return;
    setPlayingIndex(index);
    playerRef.current?.playRange(sentence.start_s, sentence.end_s);
  };

  const toggleSelected = (id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const allSelected = sentences.length > 0 && selected.size === sentences.length;

  // ---- states --------------------------------------------------------------

  const progressBlock = running ? (
    <Panel title="Recording the narration">
      <ProgressBar value={liveProgress?.pct ?? null} label={liveProgress?.message || "The voice tool is working..."} />
    </Panel>
  ) : busy ? (
    <Panel title={upload.isPending ? "Uploading your recording" : "Working on the narration"}>
      <ProgressBar value={liveProgress?.pct ?? null} label={liveProgress?.message || (upload.isPending ? "Sending the file to the app..." : "Please wait...")} />
    </Panel>
  ) : null;

  if (!payload) {
    if (query.isPending) return <LoadingBlock label="Loading the narration..." />;
    if (query.isError) return <ErrorState error={query.error} title="Could not load the narration" onRetry={() => void query.refetch()} />;
    return (
      <div className="flex flex-col gap-4">
        {progressBlock}
        {!running ? (
          <EmptyState
            icon={Volume2}
            title="No narration to review yet"
            description="The voice stage has not written its audio and timings for this project. When it finishes, the player and the sentence list appear here."
            action={
              <div className="flex flex-wrap items-center justify-center gap-2">
                <Button variant="secondary" size="sm" icon={<RotateCcw />} onClick={() => void query.refetch()}>
                  Check again
                </Button>
                <Button variant="secondary" size="sm" icon={<FileAudio />} onClick={() => setOwnOpen(true)} disabled={busy}>
                  Use my own recording
                </Button>
              </div>
            }
          />
        ) : null}
        <OwnRecordingDialog open={ownOpen} loading={busy} onConfirm={(choice) => void onOwnRecording(choice)} onCancel={() => setOwnOpen(false)} />
      </div>
    );
  }

  const flags = sumWordFlags(sentences);
  const delta = durationDelta(payload.duration_s, payload.target_s);
  const outOfBand = durationOutOfBand(payload.duration_s, payload.target_s);
  const hasWordTimes = sentences.some((sentence) => sentence.words.length > 0);
  const lowConfidence = payload.timing_confidence === "low";

  return (
    <div className="flex flex-col gap-4">
      {progressBlock}

      <Panel
        title="Narration"
        description={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">{formatDuration(payload.duration_s)}</Badge>
            <Badge tone="neutral">
              {sentences.length} sentence{sentences.length === 1 ? "" : "s"}
            </Badge>
            <TimingSourceBadge source={payload.source} />
            <ConfidenceBadge confidence={payload.timing_confidence} />
            {payload.own_recording ? <Badge tone="info">Own recording</Badge> : null}
            {payload.provider || payload.voice_id ? (
              <span className="text-xs text-ink-faint">
                {payload.own_recording ? "Converted by" : "Voiced by"} {payload.provider || "the voice tool"}
                {payload.voice_id ? ` (${payload.voice_id})` : ""}
                {payload.model ? `, ${payload.model}` : ""}
              </span>
            ) : null}
            {payload.cost_usd > 0 ? <span className="text-xs text-ink-faint">{formatUsd(payload.cost_usd)}</span> : null}
            {payload.cached_sentences > 0 && !payload.own_recording ? (
              <span className="text-xs text-ink-faint">
                {payload.cached_sentences} of {sentences.length} from the cache
              </span>
            ) : null}
          </span>
        }
        actions={
          <>
            <Button variant="secondary" size="sm" icon={<FileAudio />} onClick={() => setOwnOpen(true)} disabled={busy || running}>
              Use my own recording
            </Button>
            <Button variant="secondary" size="sm" icon={<Mic />} onClick={() => setReRecordOpen(true)} disabled={busy || running || selected.size === 0}>
              Re-record selected{selected.size > 0 ? ` (${selected.size})` : ""}
            </Button>
            <Button variant="primary" size="sm" icon={<Check />} loading={approve.isPending} disabled={busy || running} onClick={onApprove}>
              Approve
            </Button>
          </>
        }
      >
        <p className="text-[13px] text-ink-muted">
          Listen to the whole narration or press Play on a sentence to hear just that one. Tick the sentences that sound wrong and
          re-record them; the rest keep their audio. The timings here are the clock for the scenes, captions and popups that follow.
        </p>
        {payload.warnings.length > 0 ? (
          <Notice tone="warn" title="Things to check" className="mt-3">
            <ul className="list-disc pl-4">
              {payload.warnings.map((warning, index) => (
                <li key={`${index}-${warning.slice(0, 24)}`}>{warning}</li>
              ))}
            </ul>
          </Notice>
        ) : null}
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex flex-col gap-4">
          <Panel title="Listen">
            <VoicePlayer
              ref={playerRef}
              src={payload.voice_url}
              durationS={payload.duration_s}
              sentences={sentences}
              activeIndex={activeIndex}
              onTime={setTime}
              onPlayingChange={(playing) => {
                if (!playing) setPlayingIndex(null);
              }}
              onPickSentence={playSentence}
            />
          </Panel>

          <Panel
            title="Sentences"
            description="Text, start and end of every sentence. Tick the ones to re-record."
            actions={
              sentences.length > 0 ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSelected(allSelected ? new Set() : new Set(sentences.map((sentence) => sentence.id)))}
                  disabled={busy || running}
                >
                  {allSelected ? "Clear ticks" : "Tick all"}
                </Button>
              ) : null
            }
          >
            {sentences.length > 0 ? (
              <SentenceList
                sentences={sentences}
                selected={selected}
                onToggle={toggleSelected}
                activeIndex={activeIndex}
                playingIndex={playingIndex}
                onPlay={playSentence}
                onStop={() => {
                  playerRef.current?.pause();
                  setPlayingIndex(null);
                }}
                disabled={busy || running}
              />
            ) : (
              <p className="text-[13px] text-ink-muted">The timing file has no sentences. Redo the stage, or use your own recording.</p>
            )}
          </Panel>
        </div>

        <div className="flex flex-col gap-4 xl:sticky xl:top-0 xl:self-start">
          <Panel title="Timing check" description="Where the times came from and whether the length fits.">
            <dl className="flex flex-col gap-3 text-[13px]">
              <div>
                <dt className="font-medium text-ink">Source</dt>
                <dd className="mt-0.5 text-ink-muted">{TIMING_SOURCE_HELP[payload.source]}</dd>
              </div>
              <div>
                <dt className="font-medium text-ink">Confidence</dt>
                <dd className={lowConfidence ? "mt-0.5 text-warn" : "mt-0.5 text-ink-muted"}>{confidenceHelp(payload.timing_confidence)}</dd>
              </div>
              <div>
                <dt className="font-medium text-ink">Length</dt>
                <dd className={outOfBand ? "mt-0.5 text-warn" : "mt-0.5 text-ink-muted"}>
                  {formatDuration(payload.duration_s)}
                  {payload.target_s !== null && delta !== null
                    ? ` against a target of ${formatDuration(payload.target_s)} (${delta >= 0 ? "+" : "-"}${Math.round(Math.abs(delta) * 100)}%)${outOfBand ? "; more than 25% off, so expect a warning" : ""}`
                    : ""}
                  {payload.sample_rate ? `, ${Math.round(payload.sample_rate / 1000)} kHz` : ""}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-ink">Word timings</dt>
                <dd className={flags.short + flags.long > 0 ? "mt-0.5 text-warn" : "mt-0.5 text-ink-muted"}>
                  {!hasWordTimes
                    ? timing.isError
                      ? "The word times (05_voice/timing.json) could not be loaded."
                      : timing.isPending
                        ? "Loading the word times..."
                        : "No word times in timing.json."
                    : flags.short + flags.long === 0
                      ? "No words shorter than 40 ms or longer than 2 s."
                      : `${flags.short} word${flags.short === 1 ? "" : "s"} under 40 ms and ${flags.long} over 2 s. Open a sentence's words to see which.`}
                </dd>
              </div>
            </dl>
          </Panel>

          <Panel title="Approve" description="Approving locks this narration and its timings and moves the project on to the images stage.">
            <div className="flex flex-col gap-4">
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium text-ink-muted">Your name</span>
                <Input value={reviewer.name} onChange={(event) => reviewer.setName(event.target.value)} placeholder={reviewer.effective} autoComplete="name" />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-[13px] font-medium text-ink-muted">Note for the project log (optional)</span>
                <Textarea rows={2} value={approveNotes} onChange={(event) => setApproveNotes(event.target.value)} placeholder="Anything the team should know" />
              </label>
              <ul className="list-disc space-y-1 pl-4 text-xs text-ink-muted">
                <li>{payload.own_recording ? "Your own recording is used as the narration." : "The narration is approved as the voice tool made it."}</li>
                {selected.size > 0 ? (
                  <li className="text-warn">
                    {selected.size} sentence{selected.size === 1 ? " is" : "s are"} ticked but not re-recorded yet. Press &quot;Re-record selected&quot; first, or clear the ticks.
                  </li>
                ) : null}
                {lowConfidence ? <li className="text-warn">The timings are estimated with low confidence; captions may drift. Check them in the edit preview.</li> : null}
                {blockedMessage ? <li className="text-fail">{blockedMessage}</li> : null}
              </ul>
              {showOverride ? (
                <label className="flex items-start gap-2 text-[13px] text-ink">
                  <input type="checkbox" className="mt-0.5 accent-accent" checked={overrideGates} onChange={(event) => setOverrideGates(event.target.checked)} />
                  <span>
                    Approve anyway: override the blocking checks.
                    <span className="block text-xs text-ink-muted">Use this only when you have read the reasons. The override is written into the project log.</span>
                  </span>
                </label>
              ) : null}
              <div className="flex flex-col gap-2">
                <Button variant="primary" icon={<Check />} loading={approve.isPending} disabled={busy || running} onClick={onApprove}>
                  Approve narration
                </Button>
                <Button variant="ghost" size="sm" icon={<RefreshCw />} onClick={() => setRedoOpen(true)} disabled={busy || running}>
                  Record the whole narration again
                </Button>
              </div>
            </div>
          </Panel>
        </div>
      </div>

      <ReRecordDialog open={reRecordOpen} sentences={selectedRows} loading={busy} onConfirm={onReRecord} onCancel={() => setReRecordOpen(false)} />

      <OwnRecordingDialog open={ownOpen} loading={busy} onConfirm={(choice) => void onOwnRecording(choice)} onCancel={() => setOwnOpen(false)} />

      <RegenerateDialog
        open={redoOpen}
        title="Record the whole narration again"
        description="The voice stage runs again with your note. Sentences whose text did not change come from the cache, so this mostly helps after a script change or a voice settings change."
        placeholder="For example: the voice settings were changed; use the new speed."
        confirmLabel="Record again"
        loading={redo.isPending}
        onConfirm={onRedoAll}
        onCancel={() => setRedoOpen(false)}
      />
    </div>
  );
}

import { ArrowDown, ArrowUp, GripVertical, Merge, Plus, RefreshCw, Scissors, Trash2, TriangleAlert } from "lucide-react";
import { useState, type DragEvent, type ReactNode } from "react";

import { cn } from "../../lib/cn";
import {
  MOTION_LABELS,
  MOTION_PRESETS,
  POPUP_POSITIONS,
  type MotionPreset,
  type PopupPosition,
  type SceneLockField,
  type ScenePopup,
  type StoryboardAspect,
  type StoryboardScene,
  type TransitionOption,
} from "../../types";
import { LockToggle } from "../script/LockToggle";
import { Button } from "../ui/Button";
import { Input, Select } from "../ui/Input";
import { AutoTextarea } from "./AutoTextarea";
import { PopupEditor } from "./PopupEditor";
import { SceneImage } from "./SceneImage";
import { formatSeconds, sceneImageUrl, type SceneBand } from "./storyboardUtils";

export interface SceneCardProps {
  projectId: string;
  scene: StoryboardScene;
  position: number;
  total: number;
  aspect: StoryboardAspect;
  band: SceneBand;
  transitions: TransitionOption[];
  motionPresets?: ReadonlyArray<MotionPreset>;
  popupPositions?: ReadonlyArray<PopupPosition>;
  popupStyles: string[];
  issues: string[];
  highlighted: boolean;
  dragging: boolean;
  dropTarget: boolean;
  onChange: (patch: Partial<StoryboardScene>) => void;
  onLock: (field: SceneLockField, locked: boolean) => void;
  onMove: (to: number) => void;
  onSplit: () => void;
  onMerge: () => void;
  onDelete: () => void;
  onRegenerate: () => void;
  onDragStart: () => void;
  onDragOver: (event: DragEvent) => void;
  onDrop: () => void;
  onDragEnd: () => void;
}

function FieldLabel({ children, locked, onLock, subject }: { children: ReactNode; locked: boolean; onLock: (locked: boolean) => void; subject: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">{children}</span>
      <LockToggle size="sm" locked={locked} onChange={onLock} subject={subject} />
    </div>
  );
}

/** One scene of the storyboard: picture, narration, prompt, popup, motion and transition, all editable in place. */
export function SceneCard({
  projectId,
  scene,
  position,
  total,
  aspect,
  band,
  transitions,
  motionPresets = MOTION_PRESETS,
  popupPositions = POPUP_POSITIONS,
  popupStyles,
  issues,
  highlighted,
  dragging,
  dropTarget,
  onChange,
  onLock,
  onMove,
  onSplit,
  onMerge,
  onDelete,
  onRegenerate,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
}: SceneCardProps) {
  const [dragArmed, setDragArmed] = useState(false);
  const [newNote, setNewNote] = useState("");
  const number = position + 1;
  const [minS, maxS] = band;
  const durationOk = scene.est_duration_s === 0 || (scene.est_duration_s >= minS && scene.est_duration_s <= maxS);
  const transitionKnown = transitions.some((option) => option.type === scene.transition_out.type);
  const presetKnown = (motionPresets as ReadonlyArray<string>).includes(scene.motion.preset);
  const lockedCount = Object.values(scene.locked).filter(Boolean).length;
  const canSplit = scene.sentence_ids.length >= 2;

  const addNote = () => {
    const note = newNote.trim();
    if (!note) return;
    onChange({ notes: [...scene.notes, note] });
    setNewNote("");
  };

  return (
    <article
      id={`scene-${position}`}
      draggable={dragArmed}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(position));
        onDragStart();
      }}
      onDragOver={onDragOver}
      onDrop={(event) => {
        event.preventDefault();
        onDrop();
      }}
      onDragEnd={() => {
        setDragArmed(false);
        onDragEnd();
      }}
      className={cn(
        "flex flex-col gap-3 rounded-card border bg-surface p-3 shadow-card transition-[border-color,box-shadow,opacity]",
        highlighted ? "border-accent ring-2 ring-accent/40" : dropTarget ? "border-accent/60" : "border-line",
        dragging && "opacity-50",
      )}
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <button
          type="button"
          aria-label={`Drag to move scene ${number}`}
          title="Drag to another place"
          onMouseDown={() => setDragArmed(true)}
          onMouseUp={() => setDragArmed(false)}
          className="cursor-grab rounded p-0.5 text-ink-faint hover:text-ink active:cursor-grabbing"
        >
          <GripVertical className="size-4" aria-hidden />
        </button>
        <h3 className="text-sm font-semibold text-ink">Scene {number}</h3>
        <span
          className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium tabular-nums", durationOk ? "bg-surface-2 text-ink-muted" : "bg-warn/12 text-warn")}
          title={`About ${formatSeconds(scene.est_duration_s)} of narration (${formatSeconds(scene.est_start_s)} to ${formatSeconds(scene.est_end_s)}). Aim for ${minS}-${maxS} s.`}
        >
          {formatSeconds(scene.est_duration_s)}
        </span>
        {lockedCount > 0 ? <span className="text-[11px] text-accent-text">{lockedCount} locked</span> : null}
        <div className="ml-auto flex items-center gap-0.5">
          <Button variant="ghost" size="sm" className="px-1.5" aria-label="Move up" title="Move up" disabled={position === 0} onClick={() => onMove(position - 1)}>
            <ArrowUp />
          </Button>
          <Button variant="ghost" size="sm" className="px-1.5" aria-label="Move down" title="Move down" disabled={position === total - 1} onClick={() => onMove(position + 1)}>
            <ArrowDown />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="px-1.5"
            aria-label="Split scene"
            title={canSplit ? "Split into two scenes" : "This scene covers one script sentence; split the sentence in the script stage first"}
            disabled={!canSplit}
            onClick={onSplit}
          >
            <Scissors />
          </Button>
          <Button variant="ghost" size="sm" className="px-1.5" aria-label="Merge with the next scene" title="Merge with the next scene" disabled={position === total - 1} onClick={onMerge}>
            <Merge />
          </Button>
          <Button variant="ghost" size="sm" className="px-1.5" aria-label="Regenerate this scene" title="Ask the AI to redo this scene" onClick={onRegenerate}>
            <RefreshCw />
          </Button>
          <Button variant="ghost" size="sm" className="px-1.5 text-ink-muted hover:text-fail" aria-label="Remove scene" title="Remove this scene" disabled={total <= 1} onClick={onDelete}>
            <Trash2 />
          </Button>
        </div>
      </header>

      <SceneImage image={scene.image} src={sceneImageUrl(projectId, scene)} aspect={aspect} prompt={scene.image_prompt} motionLabel={MOTION_LABELS[scene.motion.preset]} />

      <div className="flex flex-col gap-1.5">
        <FieldLabel locked={scene.locked.narration} onLock={(locked) => onLock("narration", locked)} subject="this scene's sentences">
          Narration
        </FieldLabel>
        <p className="whitespace-pre-wrap rounded-md border border-line bg-surface-2/40 px-3 py-2 text-sm leading-6 text-ink" aria-label={`Scene ${number} narration`}>
          {scene.narration || <span className="text-ink-faint">This scene has no narration.</span>}
        </p>
        <p className="text-[11px] text-ink-faint">
          The words come from the script stage; edit or redo the script to change them. The lock keeps these sentences together as one
          scene when the storyboard is regenerated.
        </p>
      </div>

      <div className="flex flex-col gap-1.5">
        <FieldLabel locked={scene.locked.image_prompt} onLock={(locked) => onLock("image_prompt", locked)} subject="the image prompt">
          Image prompt
        </FieldLabel>
        <AutoTextarea
          value={scene.image_prompt}
          aria-label={`Scene ${number} image prompt`}
          placeholder="Describe the picture, with no words or logos in it. The channel style is added automatically."
          onChange={(event) => onChange({ image_prompt: event.target.value })}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <FieldLabel locked={scene.locked.popup} onLock={(locked) => onLock("popup", locked)} subject="the popup">
          Popup
        </FieldLabel>
        <PopupEditor popup={scene.popup} styles={popupStyles} positions={popupPositions} onChange={(popup: ScenePopup) => onChange({ popup })} />
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">On-screen text</span>
        <Input
          value={scene.on_screen_text ?? ""}
          aria-label={`Scene ${number} on-screen text`}
          placeholder="Larger text card drawn on the picture (optional)"
          onChange={(event) => onChange({ on_screen_text: event.target.value === "" ? null : event.target.value })}
          className="h-8 text-[13px]"
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          <FieldLabel locked={scene.locked.motion} onLock={(locked) => onLock("motion", locked)} subject="the motion">
            Motion
          </FieldLabel>
          <Select
            value={scene.motion.preset}
            aria-label={`Scene ${number} motion`}
            onChange={(event) => onChange({ motion: { ...scene.motion, preset: event.target.value as MotionPreset } })}
            className="h-8 text-[13px]"
          >
            {!presetKnown ? <option value={scene.motion.preset}>{MOTION_LABELS[scene.motion.preset]}</option> : null}
            {motionPresets.map((preset) => (
              <option key={preset} value={preset}>
                {MOTION_LABELS[preset]}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <FieldLabel locked={scene.locked.transition_out} onLock={(locked) => onLock("transition_out", locked)} subject="the transition">
            Transition out
          </FieldLabel>
          <div className="flex items-center gap-1.5">
            <Select
              value={scene.transition_out.type}
              aria-label={`Scene ${number} transition`}
              invalid={!transitionKnown}
              onChange={(event) => {
                const type = event.target.value;
                const option = transitions.find((item) => item.type === type);
                onChange({ transition_out: { type, duration_s: option?.duration_s ?? scene.transition_out.duration_s } });
              }}
              className="h-8 text-[13px]"
              wrapperClassName="min-w-0 flex-1"
            >
              {!transitionKnown ? <option value={scene.transition_out.type}>{scene.transition_out.type} (unknown)</option> : null}
              {transitions.map((option) => (
                <option key={option.type} value={option.type}>
                  {option.label}
                </option>
              ))}
            </Select>
            <Input
              type="number"
              step={0.1}
              min={0.1}
              max={3}
              value={scene.transition_out.duration_s}
              aria-label={`Scene ${number} transition length in seconds`}
              title="Transition length in seconds"
              onChange={(event) => {
                const parsed = Number(event.target.value);
                if (!Number.isNaN(parsed)) onChange({ transition_out: { ...scene.transition_out, duration_s: parsed } });
              }}
              className="h-8 w-16 px-2 text-[13px] tabular-nums"
            />
          </div>
        </div>
      </div>

      <details>
        <summary className="cursor-pointer select-none text-[11px] font-semibold uppercase tracking-wide text-ink-muted hover:text-ink">
          Notes{scene.notes.length > 0 ? ` (${scene.notes.length})` : ""}
        </summary>
        <div className="mt-2 flex flex-col gap-2">
          {scene.notes.length > 0 ? (
            <ul className="list-disc space-y-1 pl-4 text-xs text-ink-muted">
              {scene.notes.map((note, index) => (
                <li key={index}>{note}</li>
              ))}
            </ul>
          ) : null}
          <form
            className="flex items-center gap-1.5"
            onSubmit={(event) => {
              event.preventDefault();
              addNote();
            }}
          >
            <Input value={newNote} placeholder="Add a note for the team or the AI" aria-label="New note" onChange={(event) => setNewNote(event.target.value)} className="h-8 text-[13px]" />
            <Button type="submit" variant="secondary" size="sm" className="px-2" aria-label="Add note" disabled={!newNote.trim()}>
              <Plus />
            </Button>
          </form>
        </div>
      </details>

      {issues.length > 0 ? (
        <ul className="flex flex-col gap-1 rounded-md border border-warn/30 bg-warn/10 px-3 py-2">
          {issues.map((issue, index) => (
            <li key={index} className="flex items-start gap-2 text-xs text-ink">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
              <span>{issue}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}

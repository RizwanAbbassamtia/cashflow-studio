/**
 * Live events from the backend over WebSocket /api/ws (docs/M1-M2-CONTRACT.md section 1).
 *
 * One shared connection for the whole app: the first component that calls
 * `useProjectEvents()` opens it, the last one to leave closes it a few seconds later. It
 * reconnects on its own with a growing delay (1 s up to 30 s). Every `project.update`
 * refreshes the react-query caches for that project, so pages redraw without polling;
 * `stage.progress` and `job.log` messages are kept per project / job for progress bars.
 */
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useSyncExternalStore } from "react";

import {
  isProjectEvent,
  JOB_STATUSES,
  type JobLogEvent,
  type JobState,
  type JobStatus,
  type ProjectEvent,
  type ProjectSummary,
  type StageProgressEvent,
} from "../types/project";
import { queryKeys } from "./keys";

export type ConnectionState = "connecting" | "open" | "closed";

export interface EventsSnapshot {
  state: ConnectionState;
  /** reconnect attempts since the last successful connection */
  attempts: number;
  progress: ReadonlyMap<string, StageProgressEvent>;
  jobLogs: ReadonlyMap<string, JobLogEvent>;
}

type EventListener = (event: ProjectEvent) => void;

const DEV_BACKEND_WS = "ws://127.0.0.1:8765/api/ws";
const MIN_DELAY_MS = 1_000;
const MAX_DELAY_MS = 30_000;
const IDLE_CLOSE_MS = 5_000;
const INVALIDATE_DEBOUNCE_MS = 150;

/** Same origin in production (the backend serves the app); the backend port in `npm run dev`. */
export function wsUrl(): string {
  if (import.meta.env.DEV) return DEV_BACKEND_WS;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/ws`;
}

class ProjectEventsClient {
  private socket: WebSocket | null = null;
  private refs = 0;
  private attempts = 0;
  private reconnectTimer: number | null = null;
  private closeTimer: number | null = null;
  private invalidateTimer: number | null = null;
  private pendingProjects = new Set<string>();
  private pendingLists = false;
  private queryClient: QueryClient | null = null;
  private listeners = new Set<EventListener>();
  private stateListeners = new Set<() => void>();
  private progress = new Map<string, StageProgressEvent>();
  private jobLogs = new Map<string, JobLogEvent>();
  private snapshot: EventsSnapshot = {
    state: "closed",
    attempts: 0,
    progress: this.progress,
    jobLogs: this.jobLogs,
  };

  attachQueryClient(queryClient: QueryClient): void {
    this.queryClient = queryClient;
  }

  /** A component wants live events; opens the socket on the first call. */
  retain(): void {
    this.refs += 1;
    if (this.closeTimer !== null) {
      window.clearTimeout(this.closeTimer);
      this.closeTimer = null;
    }
    if (!this.socket && this.reconnectTimer === null) this.connect();
  }

  /** The component left; the socket closes once nobody listens for a few seconds. */
  release(): void {
    this.refs = Math.max(0, this.refs - 1);
    if (this.refs > 0 || this.closeTimer !== null) return;
    this.closeTimer = window.setTimeout(() => {
      this.closeTimer = null;
      if (this.refs === 0) this.disconnect();
    }, IDLE_CLOSE_MS);
  }

  subscribe(listener: EventListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  subscribeState(listener: () => void): () => void {
    this.stateListeners.add(listener);
    return () => this.stateListeners.delete(listener);
  }

  getSnapshot(): EventsSnapshot {
    return this.snapshot;
  }

  // ---- connection -------------------------------------------------------------------

  private connect(): void {
    if (typeof WebSocket === "undefined") return;
    this.setState("connecting");
    let socket: WebSocket;
    try {
      socket = new WebSocket(wsUrl());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket) return;
      const reconnected = this.attempts > 0;
      this.attempts = 0;
      this.setState("open");
      // Anything that changed while we were away: refresh every project query once.
      if (reconnected) {
        this.pendingLists = true;
        void this.queryClient?.invalidateQueries({ queryKey: queryKeys.projects });
      }
    };
    socket.onmessage = (message) => this.handleMessage(message.data);
    socket.onerror = () => {
      // onclose follows and schedules the reconnect; nothing else to do here
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (this.refs > 0) this.scheduleReconnect();
      else this.setState("closed");
    };
  }

  private disconnect(): void {
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onclose = null;
      socket.onmessage = null;
      socket.onerror = null;
      socket.onopen = null;
      try {
        socket.close();
      } catch {
        /* already closed */
      }
    }
    this.attempts = 0;
    this.setState("closed");
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return;
    this.socket = null;
    const base = Math.min(MAX_DELAY_MS, MIN_DELAY_MS * 2 ** Math.min(this.attempts, 5));
    const jitter = Math.round(Math.random() * 400);
    this.attempts += 1;
    this.setState("connecting");
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      if (this.refs > 0) this.connect();
      else this.setState("closed");
    }, base + jitter);
  }

  // ---- events -----------------------------------------------------------------------

  private handleMessage(raw: unknown): void {
    if (typeof raw !== "string") return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw) as unknown;
    } catch {
      return;
    }
    if (!isProjectEvent(parsed)) return;
    const event = parsed;

    let changed = false;
    if (event.type === "stage.progress" && event.project_id) {
      this.progress = new Map(this.progress).set(event.project_id, event);
      changed = true;
    } else if (event.type === "job.log") {
      if (event.job_id) {
        this.jobLogs = new Map(this.jobLogs).set(event.job_id, event);
        changed = true;
        // The polled job view gets the same change at once, so progress bars move live.
        const status = (JOB_STATUSES as readonly string[]).includes(event.status ?? "") ? (event.status as JobState) : null;
        this.queryClient?.setQueryData<JobStatus>(queryKeys.job(event.job_id), (old) =>
          old
            ? {
                ...old,
                status: status ?? old.status,
                progress: typeof event.progress === "number" ? event.progress : old.progress,
                message: event.message ?? old.message,
                error: event.error ?? old.error,
              }
            : old,
        );
      }
      if (event.project_id) {
        // A log line is also the freshest progress message for the project.
        const previous = this.progress.get(event.project_id);
        if (previous) {
          this.progress = new Map(this.progress).set(event.project_id, { ...previous, message: event.message });
          changed = true;
        }
      }
    } else if (event.type === "project.update" && event.project_id) {
      // The event carries the list row: patch every cached list straight away, then the
      // debounced invalidation fetches the full project and the stage payloads.
      const summary = event.project;
      if (summary && this.queryClient) {
        this.queryClient.setQueriesData<ProjectSummary[]>({ queryKey: queryKeys.projectLists }, (old) =>
          old && old.some((row) => row.id === summary.id) ? old.map((row) => (row.id === summary.id ? summary : row)) : old,
        );
      }
      // A finished or failed stage clears its progress line.
      if (this.progress.has(event.project_id) && event.status && event.status !== "running") {
        const next = new Map(this.progress);
        next.delete(event.project_id);
        this.progress = next;
        changed = true;
      }
      this.queueInvalidation(event.project_id);
    }

    if (changed) this.publish({ progress: this.progress, jobLogs: this.jobLogs });
    for (const listener of this.listeners) {
      try {
        listener(event);
      } catch {
        /* one listener must not break the others */
      }
    }
  }

  private queueInvalidation(projectId: string): void {
    this.pendingProjects.add(projectId);
    this.pendingLists = true;
    if (this.invalidateTimer !== null) return;
    this.invalidateTimer = window.setTimeout(() => {
      this.invalidateTimer = null;
      const client = this.queryClient;
      const projects = [...this.pendingProjects];
      const lists = this.pendingLists;
      this.pendingProjects.clear();
      this.pendingLists = false;
      if (!client) return;
      for (const id of projects) {
        void client.invalidateQueries({ queryKey: queryKeys.project(id) });
      }
      if (lists) void client.invalidateQueries({ queryKey: queryKeys.projectLists });
    }, INVALIDATE_DEBOUNCE_MS);
  }

  private setState(state: ConnectionState): void {
    if (this.snapshot.state === state && this.snapshot.attempts === this.attempts) return;
    this.publish({ state, attempts: this.attempts });
  }

  private publish(patch: Partial<EventsSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.stateListeners) listener();
  }
}

/** The one shared client. Exported for tests and for code outside React. */
export const projectEvents = new ProjectEventsClient();

/**
 * Keeps the shared WebSocket open while the component is mounted and reports its state.
 * Pass `onEvent` to react to individual events; cache refreshes happen on their own.
 */
export function useProjectEvents(onEvent?: (event: ProjectEvent) => void): { state: ConnectionState; attempts: number } {
  const queryClient = useQueryClient();

  useEffect(() => {
    projectEvents.attachQueryClient(queryClient);
    projectEvents.retain();
    return () => projectEvents.release();
  }, [queryClient]);

  useEffect(() => {
    if (!onEvent) return;
    return projectEvents.subscribe(onEvent);
  }, [onEvent]);

  const snapshot = useSyncExternalStore(
    (listener) => projectEvents.subscribeState(listener),
    () => projectEvents.getSnapshot(),
    () => projectEvents.getSnapshot(),
  );
  return { state: snapshot.state, attempts: snapshot.attempts };
}

/** The latest progress message of a project while one of its stages runs. */
export function useStageProgress(projectId: string | undefined): StageProgressEvent | null {
  const snapshot = useSyncExternalStore(
    (listener) => projectEvents.subscribeState(listener),
    () => projectEvents.getSnapshot(),
    () => projectEvents.getSnapshot(),
  );
  return projectId ? (snapshot.progress.get(projectId) ?? null) : null;
}

/** The latest log line of a background job (for example a competitor scan). */
export function useJobLog(jobId: string | null | undefined): JobLogEvent | null {
  const snapshot = useSyncExternalStore(
    (listener) => projectEvents.subscribeState(listener),
    () => projectEvents.getSnapshot(),
    () => projectEvents.getSnapshot(),
  );
  return jobId ? (snapshot.jobLogs.get(jobId) ?? null) : null;
}

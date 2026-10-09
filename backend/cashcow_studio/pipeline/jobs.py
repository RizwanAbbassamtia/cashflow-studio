"""In-process registry of background jobs, shared by the research scan and the pipeline.

A job is one long-running piece of work started from the API: a competitor scan, a project
run. The registry keeps a record per job id (status, progress, message, result or error) so
``GET /api/jobs/{job_id}`` (``api/jobs.py``) can answer polls, and the WebSocket bridge can
subscribe to changes. The shape of :meth:`Job.to_dict` is the job object of the contract::

    {id, kind, status: queued|running|done|failed, progress: 0-100, message, result?, error?}

Jobs live in memory only; a restart forgets them. Use :data:`registry` (the process-wide
instance) unless a test wants an isolated one.

The module-level shortcuts at the bottom (``create_job``, ``update``, ``finish``, ``get``)
work on :data:`registry` and never raise for an unknown job id, so a late progress call from
a worker thread cannot crash a request.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "done", "failed"]
ProgressFn = Callable[[str, float | None], None]
"""``progress(message, pct)``: ``pct`` is 0-100 or ``None`` to keep the current value."""

ACTIVE_STATUSES: frozenset[str] = frozenset({"queued", "running"})
GENERIC_FAILURE = (
    "Something went wrong while running this job. Try again; if it keeps happening, restart "
    "the app and run the health checks in Settings."
)

_UNSET: Any = object()


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Job:
    id: str
    kind: str
    status: JobStatus = "queued"
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    """Free-form lookup data, e.g. ``{"channel_slug": "kind-ledger"}``. Not sent to the UI."""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def to_dict(self) -> dict[str, Any]:
        """The job as ``GET /api/jobs/{id}`` returns it."""
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": round(max(0.0, min(100.0, self.progress)), 1),
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class JobRegistry:
    """Thread-safe dict of job id -> :class:`Job` with helpers to run work in a thread."""

    def __init__(self, *, max_jobs: int = 500) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._listeners: list[Callable[[Job], None]] = []
        self._max_jobs = max_jobs

    # Records -----------------------------------------------------------------------------

    def create(self, kind: str, *, message: str = "", meta: dict[str, Any] | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex, kind=kind, message=message, meta=dict(meta or {}))
        with self._lock:
            self._jobs[job.id] = job
            self._prune_locked()
        self._notify(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def find(
        self, kind: str | None = None, *, active_only: bool = False, **meta: Any
    ) -> list[Job]:
        """Jobs matching a kind and/or ``meta`` values, newest first."""
        with self._lock:
            rows = list(self._jobs.values())
        rows = [
            job
            for job in rows
            if (kind is None or job.kind == kind)
            and (not active_only or job.is_active)
            and all(job.meta.get(key) == value for key, value in meta.items())
        ]
        rows.sort(key=lambda job: job.created_at, reverse=True)
        return rows

    def update(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        progress: float | None = None,
        message: str | None = None,
        result: Any = _UNSET,
        error: str | None = _UNSET,
    ) -> Job | None:
        """Change a job's record; an unknown id (pruned or cleared) is ignored, never raises."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                log.debug("Ignoring an update for unknown job %s", job_id)
                return None
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = float(progress)
            if message is not None:
                job.message = message
            if result is not _UNSET:
                job.result = result
            if error is not _UNSET:
                job.error = error
            job.updated_at = utc_now()
        self._notify(job)
        return job

    def start(self, job_id: str, message: str | None = None) -> Job | None:
        return self.update(job_id, status="running", message=message)

    def finish(self, job_id: str, result: Any = None, message: str = "Done") -> Job | None:
        return self.update(
            job_id, status="done", progress=100.0, message=message, result=result, error=None
        )

    def fail(self, job_id: str, error: str) -> Job | None:
        return self.update(job_id, status="failed", message=error, error=error)

    # Running -----------------------------------------------------------------------------

    def run_in_thread(
        self,
        job_id: str,
        work: Callable[[Job, ProgressFn], Any],
        *,
        friendly_errors: tuple[type[BaseException], ...] = (),
    ) -> threading.Thread:
        """Run ``work(job, progress)`` in a daemon thread and record its outcome.

        The return value becomes ``result``. An exception of a ``friendly_errors`` type is
        shown to the user as its message; any other exception is logged with its traceback
        and reported as a generic plain-English failure.
        """

        def progress(message: str, pct: float | None = None) -> None:
            self.update(job_id, message=message, progress=pct)

        def target() -> None:
            job = self.get(job_id)
            if job is None:  # pragma: no cover - pruned between create and start
                return
            self.start(job_id)
            try:
                result = work(job, progress)
            except friendly_errors as exc:
                self.fail(job_id, str(exc) or GENERIC_FAILURE)
            except Exception:
                log.exception("Job %s (%s) failed", job_id, job.kind)
                self.fail(job_id, GENERIC_FAILURE)
            else:
                self.finish(job_id, result)

        thread = threading.Thread(target=target, name=f"job-{job_id[:8]}", daemon=True)
        thread.start()
        return thread

    def wait(self, job_id: str, timeout: float = 30.0, poll: float = 0.02) -> Job:
        """Block until the job is done or failed (tests, CLI). Returns the job either way."""
        deadline = time.monotonic() + timeout
        while True:
            job = self.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if not job.is_active or time.monotonic() >= deadline:
                return job
            time.sleep(poll)

    # Listeners ---------------------------------------------------------------------------

    def subscribe(self, callback: Callable[[Job], None]) -> Callable[[], None]:
        """Call ``callback(job)`` after every change; returns an unsubscribe function."""
        with self._lock:
            self._listeners.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)

        return unsubscribe

    def _notify(self, job: Job) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(job)
            except Exception:  # a broken listener must not break the job
                log.exception("Job listener failed")

    # Housekeeping ------------------------------------------------------------------------

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()

    def _prune_locked(self) -> None:
        """Drop the oldest finished jobs once the registry grows past ``max_jobs``."""
        if len(self._jobs) <= self._max_jobs:
            return
        finished = sorted(
            (job for job in self._jobs.values() if not job.is_active),
            key=lambda job: job.updated_at,
        )
        for job in finished[: len(self._jobs) - self._max_jobs]:
            self._jobs.pop(job.id, None)


registry = JobRegistry()
"""The process-wide registry used by the API routers."""


# Module-level shortcuts on :data:`registry` ------------------------------------------------


def create_job(kind: str, message: str = "", meta: dict[str, Any] | None = None) -> str:
    """Register a new job (status ``queued``) and return its id."""
    return registry.create(kind, message=message, meta=meta).id


def update(job_id: str, progress: float | None = None, message: str | None = None) -> Job | None:
    """Report progress (0-100) and/or a message; a queued job becomes ``running``."""
    job = registry.get(job_id)
    if job is None or not job.is_active:
        return job
    status: JobStatus | None = "running" if job.status == "queued" else None
    return registry.update(job_id, status=status, progress=progress, message=message)


def finish(
    job_id: str, result: Any = None, error: str | None = None, message: str | None = None
) -> Job | None:
    """Mark a job ``done`` (with ``result``) or, when ``error`` is given, ``failed``."""
    if registry.get(job_id) is None:
        return None
    if error:
        return registry.fail(job_id, str(error))
    return registry.finish(job_id, result, message=message if message is not None else "Done")


def get(job_id: str) -> Job | None:
    return registry.get(job_id)

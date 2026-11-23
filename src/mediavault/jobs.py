"""In-process async job manager.

A stdio MCP server is one long-lived process, so an in-memory job table is
enough for M0/M1. The manager owns a dedicated event loop on a daemon thread
instead of borrowing the caller's loop: jobs then keep running regardless of
which event loop (or which short-lived asyncio.run invocation) submitted them,
which also makes the behavior identical in tests and in the long-lived server.
Persistence across restarts stays out of scope until a media-library DB lands.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

JobFunc = Callable[[], Awaitable[dict[str, Any]]]


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | succeeded | failed
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        # Lazily start the worker loop so managers that never submit a job
        # pay no thread cost. The lock makes first-submit races impossible.
        with self._start_lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                ready = threading.Event()

                def _run() -> None:
                    asyncio.set_event_loop(loop)
                    ready.set()
                    loop.run_forever()

                self._thread = threading.Thread(
                    target=_run, name="mediavault-jobs", daemon=True
                )
                self._thread.start()
                ready.wait()
                self._loop = loop
            return self._loop

    def submit(self, kind: str, func: JobFunc) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, kind=kind)
        self._jobs[job_id] = job
        loop = self._ensure_loop()

        async def runner() -> None:
            job.status = "running"
            job.started_at = _now()
            try:
                job.result = await func()
                job.status = "succeeded"
            except Exception as exc:  # surfaced through get_job, never crash the loop
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = "failed"
            finally:
                job.finished_at = _now()

        # The coroutine is executed on the manager's own loop, so its
        # lifetime is independent of the submitter's loop.
        asyncio.run_coroutine_threadsafe(runner(), loop)
        return job_id

    def get(self, job_id: str) -> Job:
        if job_id not in self._jobs:
            raise KeyError(job_id)
        return self._jobs[job_id]

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def close(self, timeout: float = 5.0) -> None:
        """Stop the worker loop and join its thread. Daemon-only safety net."""
        if self._loop is None or self._thread is None:
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=timeout)
        self._loop = None
        self._thread = None

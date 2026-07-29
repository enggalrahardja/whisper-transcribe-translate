"""Reusable bounded in-process workers and shared processing job contract."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from itertools import count
from time import perf_counter
from typing import Awaitable, Callable, Generic, TypeVar


Payload = TypeVar("Payload")
Result = TypeVar("Result")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkerJobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobPriority(int, Enum):
    LIVE = 0
    FINAL_TRANSCRIPTION = 10
    TRANSLATION = 20
    DIARIZATION = 30
    POST_PROCESSING = 40


class RetryableJobError(RuntimeError):
    pass


class PermanentJobError(RuntimeError):
    pass


class WorkerBackpressureError(asyncio.QueueFull):
    pass


@dataclass(frozen=True)
class ProcessingJob(Generic[Payload]):
    job_id: str
    job_type: str
    session_id: str
    segment_id: str
    revision: int
    priority: int
    max_retries: int
    timeout_ms: int
    payload: Payload = field(repr=False, compare=False)
    status: WorkerJobStatus = WorkerJobStatus.PENDING
    attempt: int = 0
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    def validate(self) -> None:
        if not self.job_id or not self.job_type or not self.session_id or not self.segment_id:
            raise ValueError("Shared worker job identifiers are required")
        if self.revision < 1 or self.priority < 0 or self.timeout_ms <= 0:
            raise ValueError("Shared worker revision, priority, or timeout is invalid")
        if not 0 <= self.max_retries <= 10:
            raise ValueError("Shared worker maxRetries must be 0-10")

    def as_dict(self) -> dict[str, object]:
        return {
            "jobId": self.job_id, "jobType": self.job_type,
            "sessionId": self.session_id, "segmentId": self.segment_id,
            "revision": self.revision, "status": self.status.value,
            "priority": self.priority, "attempt": self.attempt,
            "maxRetries": self.max_retries, "timeoutMs": self.timeout_ms,
            "createdAt": self.created_at.isoformat(),
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
        }


WorkerHandler = Callable[[Payload], Result | Awaitable[Result]]


class InProcessWorker(Generic[Payload, Result]):
    def __init__(
        self, name: str, handler: WorkerHandler[Payload, Result], *,
        capacity: int, concurrency: int,
        model_loaded: Callable[[], bool] | None = None,
        model_load_time_ms: Callable[[], float] | None = None,
    ) -> None:
        if capacity < 1 or not 1 <= concurrency <= capacity:
            raise ValueError("Worker capacity/concurrency is invalid")
        self.name, self.handler = name, handler
        self.capacity, self.concurrency = capacity, concurrency
        self._queue: asyncio.PriorityQueue[tuple[int, int, str | None]] = asyncio.PriorityQueue(capacity)
        self._order = count()
        self._jobs: dict[str, ProcessingJob[Payload]] = {}
        self._results: dict[str, Result] = {}
        self._waiters: dict[str, asyncio.Future[Result]] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._active: dict[str, asyncio.Task[Result]] = {}
        self._cancelled_sessions: set[str] = set()
        self._accepting = False
        self._running = False
        self._ever_started = False
        self._model_loaded = model_loaded or (lambda: False)
        self._model_load_time_ms = model_load_time_ms or (lambda: 0.0)
        self._metrics: dict[str, int | float | str | None] = {
            "completed": 0, "failed": 0, "retried": 0, "cancelled": 0,
            "rejected_queue_full": 0, "wait_total_ms": 0.0,
            "processing_total_ms": 0.0, "last_success": None,
            "last_failure": None, "restart_count": 0,
        }

    async def start(self) -> None:
        if self._running:
            return
        if self._ever_started:
            self._metrics["restart_count"] = int(self._metrics["restart_count"]) + 1
        self._ever_started = True
        self._accepting = self._running = True
        self._tasks = [asyncio.create_task(self._run(), name=f"worker:{self.name}:{index}") for index in range(self.concurrency)]

    async def submit(self, job: ProcessingJob[Payload]) -> ProcessingJob[Payload]:
        job.validate()
        if not self._accepting:
            raise RuntimeError(f"Worker {self.name} is not accepting jobs")
        if job.job_id in self._jobs:
            return self._jobs[job.job_id]
        if self._queue.full():
            self._metrics["rejected_queue_full"] = int(self._metrics["rejected_queue_full"]) + 1
            raise WorkerBackpressureError(f"Worker {self.name} queue capacity reached")
        self._jobs[job.job_id] = job
        self._waiters[job.job_id] = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((job.priority, next(self._order), job.job_id))
        return job

    async def submit_and_wait(self, job: ProcessingJob[Payload]) -> Result:
        existing = await self.submit(job)
        if existing.status is WorkerJobStatus.COMPLETED:
            return self._results[job.job_id]
        return await asyncio.shield(self._waiters[job.job_id])

    async def cancel_session(self, session_id: str) -> int:
        self._cancelled_sessions.add(session_id)
        cancelled = 0
        for job_id, task in tuple(self._active.items()):
            if self._jobs[job_id].session_id == session_id:
                task.cancel()
                cancelled += 1
        for job_id, job in tuple(self._jobs.items()):
            if job.session_id == session_id and job.status is WorkerJobStatus.PENDING:
                self._finish_cancelled(job_id)
                cancelled += 1
        return cancelled

    async def shutdown(self, *, drain: bool = True) -> None:
        self._accepting = False
        if not self._running:
            return
        if drain:
            await self._queue.join()
        else:
            for session_id in {job.session_id for job in self._jobs.values()}:
                await self.cancel_session(session_id)
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except asyncio.QueueEmpty:
                    break
        for _ in self._tasks:
            await self._queue.put((10**9, next(self._order), None))
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._active.clear()
        self._cancelled_sessions.clear()
        self._running = False

    def health(self) -> dict[str, object]:
        processed = int(self._metrics["completed"]) + int(self._metrics["failed"])
        queued_or_started = max(1, processed + self._queue.qsize() + len(self._active))
        return {
            "name": self.name, "running": self._running,
            "ready": self._running and self._accepting,
            "queueDepth": self._queue.qsize(), "capacity": self.capacity,
            "activeJobs": len(self._active), "modelLoaded": self._model_loaded(),
            "lastSuccess": self._metrics["last_success"],
            "lastFailure": self._metrics["last_failure"],
            "completed": int(self._metrics["completed"]),
            "failed": int(self._metrics["failed"]),
            "retried": int(self._metrics["retried"]),
            "cancelled": int(self._metrics["cancelled"]),
            "rejectedQueueFull": int(self._metrics["rejected_queue_full"]),
            "averageWaitMs": round(float(self._metrics["wait_total_ms"]) / queued_or_started, 3),
            "averageProcessingMs": round(float(self._metrics["processing_total_ms"]) / processed, 3) if processed else 0.0,
            "modelLoadTimeMs": round(self._model_load_time_ms(), 3),
            "workerRestartCount": int(self._metrics["restart_count"]),
        }

    async def _run(self) -> None:
        while True:
            _, _, job_id = await self._queue.get()
            try:
                if job_id is None:
                    return
                job = self._jobs[job_id]
                if job.status is WorkerJobStatus.CANCELLED or job.session_id in self._cancelled_sessions:
                    if job.status is not WorkerJobStatus.CANCELLED:
                        self._finish_cancelled(job_id)
                    continue
                await self._execute(job_id)
            finally:
                self._queue.task_done()

    async def _execute(self, job_id: str) -> None:
        job = self._jobs[job_id]
        self._metrics["wait_total_ms"] = float(self._metrics["wait_total_ms"]) + (utc_now() - job.created_at).total_seconds() * 1000
        for attempt in range(1, job.max_retries + 2):
            job = replace(job, status=WorkerJobStatus.PROCESSING, attempt=attempt, started_at=job.started_at or utc_now(), error=None)
            self._jobs[job_id] = job
            started = perf_counter()
            try:
                task = (
                    asyncio.create_task(self.handler(job.payload))
                    if inspect.iscoroutinefunction(self.handler)
                    else asyncio.create_task(asyncio.to_thread(self.handler, job.payload))
                )
                self._active[job_id] = task
                result = await asyncio.wait_for(task, job.timeout_ms / 1000)
            except (RetryableJobError, asyncio.TimeoutError) as exc:
                self._active.pop(job_id, None)
                if attempt <= job.max_retries:
                    self._metrics["retried"] = int(self._metrics["retried"]) + 1
                    continue
                await self._fail(job_id, exc, started)
                return
            except asyncio.CancelledError:
                self._active.pop(job_id, None)
                self._finish_cancelled(job_id)
                return
            except Exception as exc:
                self._active.pop(job_id, None)
                await self._fail(job_id, exc, started)
                return
            self._active.pop(job_id, None)
            now = utc_now()
            self._jobs[job_id] = replace(job, status=WorkerJobStatus.COMPLETED, completed_at=now)
            self._results[job_id] = result
            self._metrics["completed"] = int(self._metrics["completed"]) + 1
            self._metrics["processing_total_ms"] = float(self._metrics["processing_total_ms"]) + (perf_counter() - started) * 1000
            self._metrics["last_success"] = now.isoformat()
            waiter = self._waiters[job_id]
            if not waiter.done():
                waiter.set_result(result)
            return

    async def _fail(self, job_id: str, exc: Exception, started: float) -> None:
        now = utc_now()
        job = self._jobs[job_id]
        self._jobs[job_id] = replace(job, status=WorkerJobStatus.FAILED, completed_at=now, error=f"{type(exc).__name__}: {exc}")
        self._metrics["failed"] = int(self._metrics["failed"]) + 1
        self._metrics["processing_total_ms"] = float(self._metrics["processing_total_ms"]) + (perf_counter() - started) * 1000
        self._metrics["last_failure"] = now.isoformat()
        waiter = self._waiters[job_id]
        if not waiter.done():
            waiter.set_exception(exc)

    def _finish_cancelled(self, job_id: str) -> None:
        job = self._jobs[job_id]
        if job.status is WorkerJobStatus.CANCELLED:
            return
        self._jobs[job_id] = replace(job, status=WorkerJobStatus.CANCELLED, completed_at=utc_now(), error="session_cancelled")
        self._metrics["cancelled"] = int(self._metrics["cancelled"]) + 1
        waiter = self._waiters[job_id]
        if not waiter.done():
            waiter.cancel()


class WorkerSupervisor:
    def __init__(self) -> None:
        self._workers: dict[str, InProcessWorker] = {}

    def register(self, worker: InProcessWorker) -> None:
        if worker.name in self._workers:
            raise ValueError(f"Duplicate worker: {worker.name}")
        self._workers[worker.name] = worker

    async def start(self) -> None:
        await asyncio.gather(*(worker.start() for worker in self._workers.values()))

    async def cancel_session(self, session_id: str) -> int:
        results = await asyncio.gather(*(worker.cancel_session(session_id) for worker in self._workers.values()), return_exceptions=True)
        return sum(value for value in results if isinstance(value, int))

    async def shutdown(self, *, drain: bool = True) -> None:
        await asyncio.gather(*(worker.shutdown(drain=drain) for worker in self._workers.values()), return_exceptions=True)

    def health(self) -> dict[str, object]:
        workers = {name: worker.health() for name, worker in self._workers.items()}
        return {"ready": all(item["ready"] for item in workers.values()), "workers": workers}

"""Bounded local queue and persistent model runtime for accurate final transcription."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from time import monotonic, perf_counter
from typing import Awaitable, Callable, Protocol

from .whisper_adapter import WhisperAdapter
from .whisper_model_metadata import WHISPER_MODEL_METADATA
from .whisper_models import resolve_available_whisper_model_path


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FinalJobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class FinalTranscriptionTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class FinalTranscriptionConfig:
    model: str = "base"
    device: str = "auto"
    compute_type: str = "auto"
    beam_size: int = 5
    timeout_seconds: float = 30.0
    max_retries: int = 1
    worker_concurrency: int = 1
    queue_capacity: int = 128

    def validate(self) -> None:
        if self.model not in WHISPER_MODEL_METADATA:
            raise ValueError(f"Unsupported final transcription model: {self.model}")
        if self.device not in {"auto", "cpu", "cuda"}:
            raise ValueError("Final transcription device must be auto, cpu, or cuda")
        if self.compute_type not in {"auto", "float16", "float32"}:
            raise ValueError("Final compute type must be auto, float16, or float32")
        if not 1 <= self.beam_size <= 20:
            raise ValueError("Final beam size must be between 1 and 20")
        if self.timeout_seconds <= 0:
            raise ValueError("Final transcription timeout must be positive")
        if not 0 <= self.max_retries <= 10:
            raise ValueError("Final transcription max retries must be between 0 and 10")
        if not 1 <= self.worker_concurrency <= 8:
            raise ValueError("Final worker concurrency must be between 1 and 8")
        if self.queue_capacity < self.worker_concurrency:
            raise ValueError("Final queue capacity must cover worker concurrency")


@dataclass(frozen=True)
class FinalTranscriptionRequest:
    session_id: str
    segment_id: str
    sequence_start: int
    sequence_end: int
    start_ms: float
    end_ms: float
    language: str
    audio_wav: bytes = field(repr=False)

    @property
    def idempotency_key(self) -> str:
        return f"{self.session_id}:{self.segment_id}"

    @property
    def job_id(self) -> str:
        return hashlib.sha256(self.idempotency_key.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class FinalModelMetadata:
    model: str
    checkpoint_path: str
    checkpoint_sha256: str
    device: str
    compute_type: str
    language: str
    beam_size: int
    timestamps: tuple[dict[str, object], ...]
    latency_ms: float

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "checkpointPath": self.checkpoint_path,
            "checkpointSha256": self.checkpoint_sha256,
            "device": self.device,
            "computeType": self.compute_type,
            "language": self.language,
            "beamSize": self.beam_size,
            "timestamps": list(self.timestamps),
            "latencyMs": round(self.latency_ms, 3),
        }


@dataclass(frozen=True)
class FinalTranscriptionResult:
    text: str
    metadata: FinalModelMetadata


@dataclass(frozen=True)
class FinalJobSnapshot:
    job_id: str
    session_id: str
    segment_id: str
    status: FinalJobStatus
    attempt: int
    queued_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: FinalTranscriptionResult | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "jobId": self.job_id,
            "sessionId": self.session_id,
            "segmentId": self.segment_id,
            "status": self.status.value,
            "attempt": self.attempt,
            "queuedAt": self.queued_at.isoformat(),
            "startedAt": self.started_at.isoformat() if self.started_at else None,
            "completedAt": self.completed_at.isoformat() if self.completed_at else None,
            "text": self.result.text if self.result is not None else None,
            "metadata": self.result.metadata.as_dict() if self.result is not None else None,
            "error": self.error,
        }


class FinalTranscriber(Protocol):
    model_load_time_ms: float

    def transcribe(
        self,
        request: FinalTranscriptionRequest,
        timeout_seconds: float,
    ) -> FinalTranscriptionResult: ...


class PersistentLocalFinalTranscriber:
    """One adapter/model cache shared by all final queue workers in this process."""

    def __init__(
        self,
        config: FinalTranscriptionConfig,
        *,
        adapter: WhisperAdapter | None = None,
        checkpoint_resolver: Callable[[str], Path] = resolve_available_whisper_model_path,
    ) -> None:
        config.validate()
        self.config = config
        self.adapter = adapter or WhisperAdapter(device=config.device)
        self.checkpoint_resolver = checkpoint_resolver
        self.model_load_time_ms = 0.0
        self._checkpoint_path: Path | None = None
        self._loaded = False
        import threading

        self._load_lock = threading.Lock()

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            started = perf_counter()
            self._checkpoint_path = self.checkpoint_resolver(self.config.model)
            self.adapter.load_model(self.config.model)
            self.model_load_time_ms = (perf_counter() - started) * 1000
            self._loaded = True

    def transcribe(
        self,
        request: FinalTranscriptionRequest,
        timeout_seconds: float,
    ) -> FinalTranscriptionResult:
        deadline = monotonic() + timeout_seconds
        temporary_path: Path | None = None
        started = perf_counter()
        try:
            self.ensure_loaded()
            if monotonic() >= deadline:
                raise FinalTranscriptionTimeout(
                    f"Final transcription exceeded {timeout_seconds:g} seconds while loading the model"
                )
            with tempfile.NamedTemporaryFile(
                prefix="whisper-final-", suffix=".wav", delete=False
            ) as audio_file:
                audio_file.write(request.audio_wav)
                temporary_path = Path(audio_file.name)
            result = self.adapter.transcribe(
                temporary_path,
                model_name=self.config.model,
                language=request.language,
                cancel_callback=lambda: monotonic() >= deadline,
                fp16=self._uses_fp16(),
                beam_size=self.config.beam_size,
                temperature=0.0,
                word_timestamps=False,
            )
        except InterruptedError as exc:
            if monotonic() >= deadline:
                raise FinalTranscriptionTimeout(
                    f"Final transcription exceeded {timeout_seconds:g} seconds"
                ) from exc
            raise
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        latency_ms = (perf_counter() - started) * 1000
        text = str(result.get("text", "")).strip()
        if not text:
            raise ValueError("Accurate final transcription returned empty text")
        raw_segments = result.get("segments", [])
        timestamps = tuple(
            {
                "startMs": round(request.start_ms + float(segment.get("start", 0)) * 1000, 3),
                "endMs": round(request.start_ms + float(segment.get("end", 0)) * 1000, 3),
                "text": str(segment.get("text", "")).strip(),
            }
            for segment in raw_segments
            if str(segment.get("text", "")).strip()
        )
        checkpoint = WHISPER_MODEL_METADATA[self.config.model]
        return FinalTranscriptionResult(
            text=text,
            metadata=FinalModelMetadata(
                model=self.config.model,
                checkpoint_path=str(self._checkpoint_path),
                checkpoint_sha256=checkpoint.expected_checksum,
                device=self.adapter.effective_device,
                compute_type="float16" if self._uses_fp16() else "float32",
                language=str(result.get("language") or request.language),
                beam_size=self.config.beam_size,
                timestamps=timestamps,
                latency_ms=latency_ms,
            ),
        )

    def _uses_fp16(self) -> bool:
        if self.config.compute_type == "float32":
            return False
        if self.config.compute_type == "float16":
            if self.adapter.effective_device != "cuda":
                raise ValueError("float16 final transcription requires a CUDA device")
            return True
        return self.adapter.effective_device == "cuda"


FinalJobListener = Callable[[FinalJobSnapshot], Awaitable[None]]


class LocalFinalTranscriptionQueue:
    def __init__(
        self,
        config: FinalTranscriptionConfig,
        transcriber: FinalTranscriber,
    ) -> None:
        config.validate()
        self.config = config
        self.transcriber = transcriber
        self._queue: asyncio.Queue[FinalTranscriptionRequest] = asyncio.Queue(
            maxsize=config.queue_capacity
        )
        self._jobs: dict[str, FinalJobSnapshot] = {}
        self._listeners: dict[str, FinalJobListener] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._start_lock = asyncio.Lock()
        self._enqueue_lock = asyncio.Lock()
        self._metrics = {
            "queued_final_jobs": 0,
            "processing_latency_total_ms": 0.0,
            "processing_attempts": 0,
            "completed": 0,
            "failed": 0,
            "retries": 0,
            "timeout_count": 0,
            "final_replacement_count": 0,
        }

    async def start(self) -> None:
        async with self._start_lock:
            if self._workers:
                return
            self._workers = [
                asyncio.create_task(self._worker(), name=f"accurate-final-{index}")
                for index in range(self.config.worker_concurrency)
            ]

    async def enqueue(
        self,
        request: FinalTranscriptionRequest,
        listener: FinalJobListener,
    ) -> tuple[FinalJobSnapshot, bool]:
        async with self._enqueue_lock:
            existing = self._jobs.get(request.idempotency_key)
            if existing is not None:
                return existing, True
            if self._queue.full():
                raise asyncio.QueueFull("Accurate final transcription queue is full")
            await self.start()
            snapshot = FinalJobSnapshot(
                job_id=request.job_id,
                session_id=request.session_id,
                segment_id=request.segment_id,
                status=FinalJobStatus.PENDING,
                attempt=0,
            )
            self._jobs[request.idempotency_key] = snapshot
            self._listeners[request.idempotency_key] = listener
            self._metrics["queued_final_jobs"] += 1
            self._queue.put_nowait(request)
        await self._notify(request.idempotency_key, snapshot)
        return snapshot, False

    def snapshot(self, session_id: str) -> list[FinalJobSnapshot]:
        return [job for job in self._jobs.values() if job.session_id == session_id]

    def metrics(self) -> dict[str, int | float]:
        attempts = int(self._metrics["processing_attempts"])
        average = (
            float(self._metrics["processing_latency_total_ms"]) / attempts
            if attempts
            else 0.0
        )
        return {
            "queued_final_jobs": int(self._metrics["queued_final_jobs"]),
            "processing_latency_ms": round(average, 3),
            "completed": int(self._metrics["completed"]),
            "failed": int(self._metrics["failed"]),
            "retries": int(self._metrics["retries"]),
            "timeout_count": int(self._metrics["timeout_count"]),
            "queue_depth": self._queue.qsize(),
            "model_load_time_ms": round(self.transcriber.model_load_time_ms, 3),
            "final_replacement_count": int(self._metrics["final_replacement_count"]),
        }

    def record_replacement(self) -> None:
        self._metrics["final_replacement_count"] += 1

    async def join(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        workers, self._workers = self._workers, []
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

    async def _worker(self) -> None:
        while True:
            request = await self._queue.get()
            try:
                await self._process(request)
            finally:
                self._queue.task_done()

    async def _process(self, request: FinalTranscriptionRequest) -> None:
        key = request.idempotency_key
        attempt = 0
        while True:
            attempt += 1
            processing = replace(
                self._jobs[key],
                status=FinalJobStatus.PROCESSING,
                attempt=attempt,
                started_at=utc_now(),
                completed_at=None,
                error=None,
            )
            self._jobs[key] = processing
            await self._notify(key, processing)
            started = perf_counter()
            try:
                result = await asyncio.to_thread(
                    self.transcriber.transcribe,
                    request,
                    self.config.timeout_seconds,
                )
            except Exception as exc:
                elapsed_ms = (perf_counter() - started) * 1000
                self._record_attempt(elapsed_ms)
                if isinstance(exc, FinalTranscriptionTimeout):
                    self._metrics["timeout_count"] += 1
                if attempt <= self.config.max_retries:
                    self._metrics["retries"] += 1
                    pending = replace(
                        processing,
                        status=FinalJobStatus.PENDING,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    self._jobs[key] = pending
                    await self._notify(key, pending)
                    continue
                failed = replace(
                    processing,
                    status=FinalJobStatus.FAILED,
                    completed_at=utc_now(),
                    error=f"{type(exc).__name__}: {exc}",
                )
                self._jobs[key] = failed
                self._metrics["failed"] += 1
                await self._notify(key, failed)
                return

            elapsed_ms = (perf_counter() - started) * 1000
            self._record_attempt(elapsed_ms)
            completed = replace(
                processing,
                status=FinalJobStatus.COMPLETED,
                completed_at=utc_now(),
                result=result,
                error=None,
            )
            self._jobs[key] = completed
            self._metrics["completed"] += 1
            await self._notify(key, completed)
            return

    def _record_attempt(self, latency_ms: float) -> None:
        self._metrics["processing_latency_total_ms"] += latency_ms
        self._metrics["processing_attempts"] += 1

    async def _notify(self, key: str, snapshot: FinalJobSnapshot) -> None:
        listener = self._listeners.get(key)
        if listener is not None:
            try:
                await listener(snapshot)
            except Exception:
                return

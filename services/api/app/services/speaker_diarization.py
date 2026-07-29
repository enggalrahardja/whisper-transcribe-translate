"""Local speaker embeddings, session clustering, assignment, and rename state."""

from __future__ import annotations

import asyncio
import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Awaitable, Callable, Protocol


PCM_SAMPLE_RATE = 16_000
PCM_CHANNEL_COUNT = 1
PCM_SAMPLE_WIDTH_BYTES = 2


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DiarizationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class SpeakerDiarizationConfig:
    model: str = "speechbrain/spkrec-ecapa-voxceleb"
    model_revision: str = "main"
    device: str = "auto"
    compute_type: str = "auto"
    similarity_threshold: float = 0.72
    low_confidence_threshold: float = 0.65
    timeout_seconds: float = 30.0
    max_retries: int = 1
    worker_concurrency: int = 1
    queue_capacity: int = 64

    def validate(self) -> None:
        if not self.model.strip() or not self.model_revision.strip():
            raise ValueError("Diarization model and revision are required")
        if self.device not in {"auto", "cpu", "cuda"}:
            raise ValueError("Diarization device must be auto, cpu, or cuda")
        if self.compute_type not in {"auto", "float16", "float32"}:
            raise ValueError("Diarization compute type must be auto, float16, or float32")
        if not -1 <= self.similarity_threshold <= 1:
            raise ValueError("Diarization similarity threshold must be between -1 and 1")
        if not 0 <= self.low_confidence_threshold <= 1:
            raise ValueError("Diarization low-confidence threshold must be between 0 and 1")
        if self.timeout_seconds <= 0 or not 0 <= self.max_retries <= 10:
            raise ValueError("Diarization timeout/retry configuration is invalid")
        if not 1 <= self.worker_concurrency <= 8:
            raise ValueError("Diarization worker concurrency must be between 1 and 8")
        if self.queue_capacity < self.worker_concurrency:
            raise ValueError("Diarization queue capacity must cover worker concurrency")


@dataclass(frozen=True)
class DiarizationRequest:
    session_id: str
    segment_id: str
    sequence_start: int
    sequence_end: int
    start_ms: float
    end_ms: float
    audio_pcm16: bytes = field(repr=False, compare=False)
    sample_rate: int = PCM_SAMPLE_RATE
    channel_count: int = PCM_CHANNEL_COUNT

    @property
    def job_id(self) -> str:
        return hashlib.sha256(
            f"{self.session_id}\0{self.segment_id}".encode("utf-8")
        ).hexdigest()

    def validate(self) -> None:
        if not self.session_id or not self.segment_id:
            raise ValueError("Diarization sessionId and segmentId are required")
        if self.sequence_start < 0 or self.sequence_end < self.sequence_start:
            raise ValueError("Diarization sequence range is invalid")
        if self.start_ms < 0 or self.end_ms < self.start_ms:
            raise ValueError("Diarization timestamp range is invalid")
        if self.sample_rate != PCM_SAMPLE_RATE or self.channel_count != PCM_CHANNEL_COUNT:
            raise ValueError("Diarization requires PCM16 mono 16 kHz")
        if not self.audio_pcm16 or len(self.audio_pcm16) % PCM_SAMPLE_WIDTH_BYTES:
            raise ValueError("Diarization audio must contain complete PCM16 samples")


@dataclass(frozen=True)
class SpeakerEmbedding:
    values: tuple[float, ...]
    provider: str
    model: str
    checkpoint: str
    locality: str
    device: str
    compute_type: str
    embedding_version: str
    latency_ms: float


class SpeakerEmbedder(Protocol):
    model_load_time_ms: float

    def embed(self, request: DiarizationRequest) -> SpeakerEmbedding: ...


class PersistentLocalSpeakerEmbedder:
    """Lazy SpeechBrain ECAPA runtime retained for the API process lifetime."""

    def __init__(
        self,
        config: SpeakerDiarizationConfig,
        *,
        runtime_loader: Callable[[], tuple[object, str, str, str]] | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.model_load_time_ms = 0.0
        self._classifier = None
        self._checkpoint = config.model_revision
        self._device = "cpu"
        self._compute_type = "float32"
        self._runtime_loader = runtime_loader
        self._load_lock = RLock()
        self._inference_lock = RLock()

    def ensure_loaded(self) -> None:
        with self._load_lock:
            if self._classifier is not None:
                return
            started = perf_counter()
            if self._runtime_loader is not None:
                classifier, checkpoint, device, compute_type = self._runtime_loader()
                self._classifier = classifier
                self._checkpoint = checkpoint
                self._device = device
                self._compute_type = compute_type
                self.model_load_time_ms = (perf_counter() - started) * 1000
                return

            import torch
            from huggingface_hub import snapshot_download
            from speechbrain.inference.speaker import EncoderClassifier

            cuda = bool(torch.cuda.is_available())
            self._device = "cuda" if self.config.device == "cuda" or (
                self.config.device == "auto" and cuda
            ) else "cpu"
            if self.config.device == "cuda" and not cuda:
                raise RuntimeError("CUDA diarization requested but CUDA is unavailable")
            self._compute_type = (
                "float16" if self.config.compute_type == "auto" and self._device == "cuda"
                else "float32" if self.config.compute_type == "auto"
                else self.config.compute_type
            )
            if self._device == "cpu" and self._compute_type == "float16":
                raise ValueError("float16 diarization requires CUDA")
            snapshot = Path(snapshot_download(
                repo_id=self.config.model,
                revision=self.config.model_revision,
            ))
            classifier = EncoderClassifier.from_hparams(
                source=str(snapshot),
                run_opts={"device": self._device},
            )
            if self._compute_type == "float16":
                classifier.mods.half()
            self._classifier = classifier
            self._checkpoint = snapshot.name
            self.model_load_time_ms = (perf_counter() - started) * 1000

    def embed(self, request: DiarizationRequest) -> SpeakerEmbedding:
        request.validate()
        self.ensure_loaded()
        import numpy as np
        import torch

        waveform = torch.from_numpy(
            np.frombuffer(request.audio_pcm16, dtype="<i2").copy()
        ).float().div_(32768.0).unsqueeze(0)
        if self._compute_type == "float16":
            waveform = waveform.half()
        waveform = waveform.to(self._device)
        started = perf_counter()
        with self._inference_lock, torch.inference_mode():
            encoded = self._classifier.encode_batch(waveform)
        values = tuple(float(value) for value in encoded.detach().float().cpu().reshape(-1).tolist())
        normalized = _normalize(values)
        return SpeakerEmbedding(
            values=normalized,
            provider="speechbrain",
            model=self.config.model,
            checkpoint=self._checkpoint,
            locality="local",
            device=self._device,
            compute_type=self._compute_type,
            embedding_version="ecapa-tdnn-192-v1",
            latency_ms=(perf_counter() - started) * 1000,
        )


@dataclass(frozen=True)
class ClusterDecision:
    speaker_id: str
    speaker_label: str
    confidence: float
    clustering_revision: int


@dataclass
class _SpeakerCluster:
    speaker_id: str
    speaker_label: str
    centroid: tuple[float, ...]
    samples: int = 1


@dataclass
class _ClusterSession:
    clusters: list[_SpeakerCluster] = field(default_factory=list)
    revision: int = 0


class SessionSpeakerClusterer:
    """Online cosine clustering with stable, session-local speaker identifiers."""

    def __init__(self, similarity_threshold: float = 0.72) -> None:
        if not -1 <= similarity_threshold <= 1:
            raise ValueError("Speaker similarity threshold must be between -1 and 1")
        self.similarity_threshold = similarity_threshold
        self._sessions: dict[str, _ClusterSession] = {}
        self._lock = RLock()

    def assign(self, session_id: str, embedding: tuple[float, ...]) -> ClusterDecision:
        normalized = _normalize(embedding)
        with self._lock:
            session = self._sessions.setdefault(session_id, _ClusterSession())
            best: _SpeakerCluster | None = None
            best_similarity = -1.0
            for cluster in session.clusters:
                similarity = _cosine(normalized, cluster.centroid)
                if similarity > best_similarity:
                    best, best_similarity = cluster, similarity
            if best is None or best_similarity < self.similarity_threshold:
                index = len(session.clusters) + 1
                best = _SpeakerCluster(
                    speaker_id=f"speaker-{index}",
                    speaker_label=f"Speaker {index}",
                    centroid=normalized,
                )
                session.clusters.append(best)
                confidence = 1.0
            else:
                combined = tuple(
                    (best.centroid[index] * best.samples + normalized[index])
                    / (best.samples + 1)
                    for index in range(len(normalized))
                )
                best.centroid = _normalize(combined)
                best.samples += 1
                confidence = max(0.0, min(1.0, (best_similarity + 1.0) / 2.0))
            session.revision += 1
            return ClusterDecision(
                speaker_id=best.speaker_id,
                speaker_label=best.speaker_label,
                confidence=confidence,
                clustering_revision=session.revision,
            )

    def rename(self, session_id: str, speaker_id: str, label: str) -> None:
        cleaned = " ".join(label.split()).strip()
        if not cleaned or len(cleaned) > 80:
            raise ValueError("Speaker label must contain 1-80 characters")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError("Diarization session has no speaker mapping")
            cluster = next(
                (item for item in session.clusters if item.speaker_id == speaker_id),
                None,
            )
            if cluster is None:
                raise KeyError(f"Unknown speaker ID: {speaker_id}")
            cluster.speaker_label = cleaned

    def speaker_count(self, session_id: str | None = None) -> int:
        with self._lock:
            if session_id is not None:
                return len(self._sessions.get(session_id, _ClusterSession()).clusters)
            return sum(len(session.clusters) for session in self._sessions.values())


@dataclass(frozen=True)
class SpeakerAssignment:
    provider: str
    model: str
    checkpoint: str
    locality: str
    device: str
    compute_type: str
    speaker_id: str
    speaker_label: str
    confidence: float
    embedding_version: str
    clustering_revision: int
    latency_ms: float
    start_ms: float
    end_ms: float
    created_at: datetime
    updated_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "checkpoint": self.checkpoint,
            "localCloud": self.locality,
            "device": self.device,
            "computeType": self.compute_type,
            "speakerId": self.speaker_id,
            "speakerLabel": self.speaker_label,
            "confidence": round(self.confidence, 6),
            "embeddingVersion": self.embedding_version,
            "clusteringRevision": self.clustering_revision,
            "latencyMs": round(self.latency_ms, 3),
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class DiarizationSnapshot:
    job_id: str
    session_id: str
    segment_id: str
    status: DiarizationStatus
    sequence_start: int
    sequence_end: int
    start_ms: float
    end_ms: float
    attempt: int = 0
    assignment: SpeakerAssignment | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, object]:
        return {
            "jobId": self.job_id,
            "sessionId": self.session_id,
            "segmentId": self.segment_id,
            "status": self.status.value,
            "sequenceStart": self.sequence_start,
            "sequenceEnd": self.sequence_end,
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "attempt": self.attempt,
            "assignment": self.assignment.as_dict() if self.assignment else None,
            "error": self.error,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }


class DiarizationStateRegistry:
    """Assignment/rename layer separate from transcript and translation registries."""

    def __init__(self, clusterer: SessionSpeakerClusterer) -> None:
        self.clusterer = clusterer
        self._latest: dict[tuple[str, str], DiarizationSnapshot] = {}
        self._lock = RLock()

    def assign(
        self,
        snapshot: DiarizationSnapshot,
        embedding: SpeakerEmbedding,
    ) -> DiarizationSnapshot:
        decision = self.clusterer.assign(snapshot.session_id, embedding.values)
        now = utc_now()
        assignment = SpeakerAssignment(
            provider=embedding.provider,
            model=embedding.model,
            checkpoint=embedding.checkpoint,
            locality=embedding.locality,
            device=embedding.device,
            compute_type=embedding.compute_type,
            speaker_id=decision.speaker_id,
            speaker_label=decision.speaker_label,
            confidence=decision.confidence,
            embedding_version=embedding.embedding_version,
            clustering_revision=decision.clustering_revision,
            latency_ms=embedding.latency_ms,
            start_ms=snapshot.start_ms,
            end_ms=snapshot.end_ms,
            created_at=now,
            updated_at=now,
        )
        completed = replace(
            snapshot,
            status=DiarizationStatus.COMPLETED,
            assignment=assignment,
            error=None,
            updated_at=now,
        )
        with self._lock:
            self._latest[(snapshot.session_id, snapshot.segment_id)] = completed
        return completed

    def retain(self, snapshot: DiarizationSnapshot) -> None:
        with self._lock:
            self._latest[(snapshot.session_id, snapshot.segment_id)] = snapshot

    def rename(
        self,
        session_id: str,
        speaker_id: str,
        label: str,
    ) -> list[DiarizationSnapshot]:
        self.clusterer.rename(session_id, speaker_id, label)
        cleaned = " ".join(label.split()).strip()
        now = utc_now()
        with self._lock:
            for key, snapshot in tuple(self._latest.items()):
                if (
                    key[0] == session_id
                    and snapshot.assignment is not None
                    and snapshot.assignment.speaker_id == speaker_id
                ):
                    assignment = replace(
                        snapshot.assignment,
                        speaker_label=cleaned,
                        updated_at=now,
                    )
                    self._latest[key] = replace(
                        snapshot,
                        assignment=assignment,
                        updated_at=now,
                    )
            return self.snapshot(session_id)

    def snapshot(self, session_id: str) -> list[DiarizationSnapshot]:
        with self._lock:
            return sorted(
                (item for (owner, _), item in self._latest.items() if owner == session_id),
                key=lambda item: (item.sequence_start, item.sequence_end, item.segment_id),
            )


DiarizationListener = Callable[[DiarizationSnapshot], Awaitable[None]]


@dataclass(frozen=True)
class DiarizationEnqueueOutcome:
    snapshot: DiarizationSnapshot
    accepted: bool
    reason: str


class LocalSpeakerDiarizationQueue:
    def __init__(
        self,
        config: SpeakerDiarizationConfig,
        embedder: SpeakerEmbedder,
        state: DiarizationStateRegistry | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.embedder = embedder
        self.state = state or DiarizationStateRegistry(
            SessionSpeakerClusterer(config.similarity_threshold)
        )
        self._queue: asyncio.Queue[DiarizationRequest | None] = asyncio.Queue(config.queue_capacity)
        self._jobs: dict[str, DiarizationSnapshot] = {}
        self._requests: dict[str, DiarizationRequest] = {}
        self._listeners: dict[str, DiarizationListener] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._executor = ThreadPoolExecutor(
            max_workers=config.worker_concurrency,
            thread_name_prefix="speaker-diarization",
        )
        self._closed = False
        self._metrics: dict[str, int | float] = {
            "diarization_jobs": 0,
            "assigned_segments": 0,
            "unassigned_segments": 0,
            "low_confidence_assignments": 0,
            "retries": 0,
            "failures": 0,
            "processing_latency_total_ms": 0.0,
            "processed_jobs": 0,
            "speaker_rename_count": 0,
            "discarded_duplicate": 0,
        }

    async def enqueue(
        self,
        request: DiarizationRequest,
        listener: DiarizationListener,
    ) -> DiarizationEnqueueOutcome:
        request.validate()
        if request.job_id in self._jobs:
            self._metrics["discarded_duplicate"] += 1
            return DiarizationEnqueueOutcome(self._jobs[request.job_id], False, "duplicate")
        if self._queue.full():
            raise asyncio.QueueFull("Speaker diarization queue capacity reached")
        now = utc_now()
        snapshot = DiarizationSnapshot(
            job_id=request.job_id,
            session_id=request.session_id,
            segment_id=request.segment_id,
            status=DiarizationStatus.PENDING,
            sequence_start=request.sequence_start,
            sequence_end=request.sequence_end,
            start_ms=request.start_ms,
            end_ms=request.end_ms,
            created_at=now,
            updated_at=now,
        )
        self._jobs[request.job_id] = snapshot
        self._requests[request.job_id] = request
        self._listeners[request.job_id] = listener
        self.state.retain(snapshot)
        self._metrics["diarization_jobs"] += 1
        self._start_workers()
        self._queue.put_nowait(request)
        await self._notify(request.job_id, snapshot)
        return DiarizationEnqueueOutcome(snapshot, True, "accepted")

    def snapshot(self, session_id: str) -> list[DiarizationSnapshot]:
        return self.state.snapshot(session_id)

    def rename(self, session_id: str, speaker_id: str, label: str) -> list[DiarizationSnapshot]:
        snapshots = self.state.rename(session_id, speaker_id, label)
        for snapshot in snapshots:
            self._jobs[snapshot.job_id] = snapshot
        self._metrics["speaker_rename_count"] += 1
        return snapshots

    def metrics(self) -> dict[str, int | float]:
        processed = int(self._metrics["processed_jobs"])
        return {
            "diarization_jobs": int(self._metrics["diarization_jobs"]),
            "detected_speakers": self.state.clusterer.speaker_count(),
            "assigned_segments": int(self._metrics["assigned_segments"]),
            "unassigned_segments": int(self._metrics["unassigned_segments"]),
            "low_confidence_assignments": int(self._metrics["low_confidence_assignments"]),
            "retries": int(self._metrics["retries"]),
            "failures": int(self._metrics["failures"]),
            "processing_latency_ms": round(float(self._metrics["processing_latency_total_ms"]) / processed, 3) if processed else 0.0,
            "queue_depth": self._queue.qsize(),
            "speaker_rename_count": int(self._metrics["speaker_rename_count"]),
            "discarded_duplicate": int(self._metrics["discarded_duplicate"]),
            "model_load_time_ms": round(float(self.embedder.model_load_time_ms), 3),
        }

    async def join(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for _ in self._workers:
            await self._queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _start_workers(self) -> None:
        if not self._workers:
            self._workers = [asyncio.create_task(self._worker()) for _ in range(self.config.worker_concurrency)]

    async def _worker(self) -> None:
        while True:
            request = await self._queue.get()
            try:
                if request is None:
                    return
                await self._process(request)
            finally:
                self._queue.task_done()

    async def _process(self, request: DiarizationRequest) -> None:
        key = request.job_id
        for attempt in range(1, self.config.max_retries + 2):
            processing = replace(
                self._jobs[key],
                status=DiarizationStatus.PROCESSING,
                attempt=attempt,
                error=None,
                updated_at=utc_now(),
            )
            self._jobs[key] = processing
            self.state.retain(processing)
            await self._notify(key, processing)
            started = perf_counter()
            try:
                future = asyncio.get_running_loop().run_in_executor(
                    self._executor,
                    self.embedder.embed,
                    request,
                )
                embedding = await asyncio.wait_for(
                    future,
                    timeout=self.config.timeout_seconds,
                )
            except Exception as exc:
                if attempt <= self.config.max_retries:
                    self._metrics["retries"] += 1
                    continue
                failed = replace(
                    processing,
                    status=DiarizationStatus.FAILED,
                    assignment=None,
                    error=f"{type(exc).__name__}: {exc}",
                    updated_at=utc_now(),
                )
                self._jobs[key] = failed
                self.state.retain(failed)
                self._metrics["failures"] += 1
                self._metrics["unassigned_segments"] += 1
                self._record_latency((perf_counter() - started) * 1000)
                await self._notify(key, failed)
                return
            completed = self.state.assign(processing, embedding)
            self._jobs[key] = completed
            self._metrics["assigned_segments"] += 1
            if (
                completed.assignment is not None
                and completed.assignment.confidence < self.config.low_confidence_threshold
            ):
                self._metrics["low_confidence_assignments"] += 1
            self._record_latency((perf_counter() - started) * 1000)
            await self._notify(key, completed)
            return

    def _record_latency(self, latency_ms: float) -> None:
        self._metrics["processing_latency_total_ms"] += latency_ms
        self._metrics["processed_jobs"] += 1

    async def _notify(self, key: str, snapshot: DiarizationSnapshot) -> None:
        listener = self._listeners.get(key)
        if listener is not None:
            try:
                await listener(snapshot)
            except Exception:
                return


def _normalize(values: tuple[float, ...]) -> tuple[float, ...]:
    if not values:
        raise ValueError("Speaker embedding cannot be empty")
    magnitude = math.sqrt(sum(value * value for value in values))
    if magnitude <= 1e-12:
        raise ValueError("Speaker embedding magnitude is zero")
    return tuple(value / magnitude for value in values)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("Speaker embedding dimensions do not match")
    return sum(a * b for a, b in zip(left, right))

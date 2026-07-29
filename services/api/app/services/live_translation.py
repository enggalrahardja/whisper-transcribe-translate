"""Bounded local text translation for semantic live transcript updates."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from time import perf_counter
from typing import Awaitable, Callable, Protocol

from .glossary import DisabledGlossarySnapshot, GlossarySnapshot, GlossaryTerm
from .live_transcript_state import TranscriptState


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TranslationStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PREVIEW = "preview"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class LiveTranslationConfig:
    model: str = "Helsinki-NLP/opus-mt-id-en"
    model_revision: str = "main"
    source_language: str = "id"
    target_language: str = "en"
    device: str = "auto"
    compute_type: str = "auto"
    beam_size: int = 4
    timeout_seconds: float = 20.0
    max_retries: int = 1
    worker_concurrency: int = 1
    queue_capacity: int = 64
    context_segments: int = 3

    def validate(self) -> None:
        if not self.model.strip() or not self.model_revision.strip():
            raise ValueError("Translation model and revision are required")
        if not self.source_language.strip() or not self.target_language.strip():
            raise ValueError("Translation language pair is required")
        if self.device not in {"auto", "cpu", "cuda"}:
            raise ValueError("Translation device must be auto, cpu, or cuda")
        if self.compute_type not in {"auto", "float16", "float32"}:
            raise ValueError("Translation compute type must be auto, float16, or float32")
        if not 1 <= self.beam_size <= 20:
            raise ValueError("Translation beam size must be between 1 and 20")
        if self.timeout_seconds <= 0 or not 0 <= self.max_retries <= 10:
            raise ValueError("Translation timeout/retry configuration is invalid")
        if not 1 <= self.worker_concurrency <= 8:
            raise ValueError("Translation worker concurrency must be between 1 and 8")
        if self.queue_capacity < self.worker_concurrency:
            raise ValueError("Translation queue capacity must cover worker concurrency")
        if not 0 <= self.context_segments <= 20:
            raise ValueError("Translation context segment count must be between 0 and 20")


GlossaryType = GlossarySnapshot | DisabledGlossarySnapshot | None


@dataclass(frozen=True)
class TranslationRequest:
    session_id: str
    segment_id: str
    source_revision: int
    source_state: TranscriptState
    source_text: str
    source_language: str
    target_language: str
    context_segment_ids: tuple[str, ...] = ()
    context_texts: tuple[str, ...] = ()
    glossary: GlossaryType = field(default=None, compare=False, repr=False)
    start_ms: float | None = None
    end_ms: float | None = None

    @property
    def job_id(self) -> str:
        identity = (
            f"{self.session_id}\0{self.segment_id}\0{self.source_revision}\0"
            f"{self.source_state.value}\0{self.target_language}"
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def validate(self) -> None:
        if not self.session_id or not self.segment_id or not self.source_text.strip():
            raise ValueError("Translation session, segment, and source text are required")
        if self.source_revision < 1:
            raise ValueError("Translation source revision must be positive")
        if self.source_state not in {TranscriptState.STABLE, TranscriptState.FINAL}:
            raise ValueError("Only stable/final transcripts can be translated")
        if len(self.context_segment_ids) != len(self.context_texts):
            raise ValueError("Translation context IDs/texts must align")
        if self.start_ms is not None and self.start_ms < 0:
            raise ValueError("Translation start timestamp cannot be negative")
        if self.end_ms is not None and (
            self.end_ms < 0
            or (self.start_ms is not None and self.end_ms < self.start_ms)
        ):
            raise ValueError("Translation timestamp range is invalid")


@dataclass(frozen=True)
class TranslationMetadata:
    provider: str
    model: str
    checkpoint: str
    locality: str
    source_language: str
    detected_language: str
    target_language: str
    context_segment_ids: tuple[str, ...]
    glossary_version: str | None
    device: str
    compute_type: str
    latency_ms: float
    source_revision: int
    detection_confidence: float | None
    created_at: datetime
    updated_at: datetime
    start_ms: float | None = None
    end_ms: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "checkpoint": self.checkpoint,
            "localCloud": self.locality,
            "sourceLanguage": self.source_language,
            "detectedLanguage": self.detected_language,
            "targetLanguage": self.target_language,
            "contextSegmentIds": list(self.context_segment_ids),
            "glossaryVersion": self.glossary_version,
            "device": self.device,
            "computeType": self.compute_type,
            "latencyMs": round(self.latency_ms, 3),
            "revision": self.source_revision,
            "languageDetectionConfidence": self.detection_confidence,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "startMs": self.start_ms,
            "endMs": self.end_ms,
        }


@dataclass(frozen=True)
class TranslationResult:
    raw_text: str
    text: str
    glossary_terms_applied: tuple[str, ...]
    metadata: TranslationMetadata


@dataclass(frozen=True)
class TranslationSnapshot:
    job_id: str
    session_id: str
    segment_id: str
    source_revision: int
    source_state: TranscriptState
    source_text: str
    status: TranslationStatus
    translation_revision: int
    attempt: int = 0
    result: TranslationResult | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    glossary: GlossaryType = field(default=None, compare=False, repr=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "jobId": self.job_id,
            "sessionId": self.session_id,
            "segmentId": self.segment_id,
            "sourceRevision": self.source_revision,
            "sourceState": self.source_state.value,
            "sourceText": self.source_text,
            "status": self.status.value,
            "revision": self.translation_revision,
            "attempt": self.attempt,
            "translatedText": self.result.text if self.result else None,
            "rawTranslatedText": self.result.raw_text if self.result else None,
            "glossaryTermsApplied": list(self.result.glossary_terms_applied) if self.result else [],
            "metadata": self.result.metadata.as_dict() if self.result else None,
            "error": self.error,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }


class Translator(Protocol):
    model_load_time_ms: float

    def translate(self, request: TranslationRequest, timeout_seconds: float) -> TranslationResult: ...


class PersistentLocalMarianTranslator:
    """Lazy/persistent Transformers Marian runtime; never loaded while flag is off."""

    def __init__(
        self,
        config: LiveTranslationConfig,
        *,
        model_loader: Callable[[], tuple[object, object, str, str, str]] | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.model_load_time_ms = 0.0
        self._model = None
        self._tokenizer = None
        self._device = "cpu"
        self._compute_type = "float32"
        self._checkpoint = config.model_revision
        self._lock = RLock()
        self._inference_lock = RLock()
        self._model_loader = model_loader

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            started = perf_counter()
            if self._model_loader is not None:
                tokenizer, model, device, compute_type, checkpoint = self._model_loader()
                self._tokenizer = tokenizer
                self._model = model
                self._device = device
                self._compute_type = compute_type
                self._checkpoint = checkpoint
                self.model_load_time_ms = (perf_counter() - started) * 1000
                return
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            cuda = bool(torch.cuda.is_available())
            self._device = "cuda" if self.config.device == "cuda" or (
                self.config.device == "auto" and cuda
            ) else "cpu"
            if self.config.device == "cuda" and not cuda:
                raise RuntimeError("CUDA translation requested but CUDA is unavailable")
            self._compute_type = (
                "float16" if self.config.compute_type == "auto" and self._device == "cuda"
                else "float32" if self.config.compute_type == "auto"
                else self.config.compute_type
            )
            if self._device == "cpu" and self._compute_type == "float16":
                raise ValueError("float16 translation requires CUDA")
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.config.model, revision=self.config.model_revision
            )
            self._model = AutoModelForSeq2SeqLM.from_pretrained(
                self.config.model, revision=self.config.model_revision
            )
            if self._compute_type == "float16":
                self._model = self._model.half()
            self._model = self._model.to(self._device).eval()
            commit = getattr(self._model.config, "_commit_hash", None)
            self._checkpoint = str(commit or self.config.model_revision)
            self.model_load_time_ms = (perf_counter() - started) * 1000

    def translate(self, request: TranslationRequest, timeout_seconds: float) -> TranslationResult:
        del timeout_seconds  # queue bounds wall time; generation is synchronous in its worker thread
        self.ensure_loaded()
        import torch

        started = perf_counter()
        detected, confidence = detect_language(request.source_text, request.source_language)
        protected, replacements = protect_glossary_terms(
            request.source_text, request.glossary, request.target_language
        )
        # Previous segments are included in the same local batch to keep runtime context
        # available without altering the source transcript or persisted schemas.
        inputs = [*request.context_texts, protected]
        with self._inference_lock:
            encoded = self._tokenizer(
                inputs,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=512,
            )
            encoded = {key: value.to(self._device) for key, value in encoded.items()}
            with torch.inference_mode():
                generated = self._model.generate(**encoded, num_beams=self.config.beam_size)
            decoded = self._tokenizer.batch_decode(generated, skip_special_tokens=True)
        raw_text = decoded[-1].strip()
        corrected, applied = restore_glossary_terms(raw_text, replacements)
        finished = utc_now()
        return TranslationResult(
            raw_text=raw_text,
            text=corrected,
            glossary_terms_applied=applied,
            metadata=TranslationMetadata(
                provider="transformers-marian",
                model=self.config.model,
                checkpoint=self._checkpoint,
                locality="local",
                source_language=request.source_language,
                detected_language=detected,
                target_language=request.target_language,
                context_segment_ids=request.context_segment_ids,
                glossary_version=getattr(request.glossary, "version", None),
                device=self._device,
                compute_type=self._compute_type,
                latency_ms=(perf_counter() - started) * 1000,
                source_revision=request.source_revision,
                detection_confidence=confidence,
                created_at=finished,
                updated_at=finished,
                start_ms=request.start_ms,
                end_ms=request.end_ms,
            ),
        )


def detect_language(text: str, requested: str) -> tuple[str, float | None]:
    if requested.casefold() != "auto":
        return requested, None
    tokens = re.findall(r"[A-Za-z]+", text.casefold())
    id_hits = sum(token in {"yang", "dan", "untuk", "dengan", "ini", "itu", "dari"} for token in tokens)
    en_hits = sum(token in {"the", "and", "for", "with", "this", "that", "from"} for token in tokens)
    total = id_hits + en_hits
    if total == 0:
        return "und", 0.0
    language = "id" if id_hits >= en_hits else "en"
    return language, round(max(id_hits, en_hits) / total, 3)


def protect_glossary_terms(
    text: str,
    glossary: GlossaryType,
    target_language: str,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    terms: tuple[GlossaryTerm, ...] = getattr(glossary, "terms", ())
    candidates: list[tuple[int, int, GlossaryTerm]] = []
    for term in terms:
        replacement = term.preferred_translation(target_language)
        if not term.do_not_translate and replacement is None:
            continue
        for form in (term.preferred_spelling, *term.aliases):
            escaped = re.escape(form).replace(r"\ ", r"\s+")
            pattern = re.compile(
                rf"(?<![\w]){escaped}(?![\w])",
                re.IGNORECASE | re.UNICODE,
            )
            candidates.extend((match.start(), match.end(), term) for match in pattern.finditer(text))
    selected: list[tuple[int, int, GlossaryTerm]] = []
    for item in sorted(candidates, key=lambda value: (-value[2].priority, -(value[1] - value[0]), value[0])):
        if not any(item[0] < kept[1] and kept[0] < item[1] for kept in selected):
            selected.append(item)
    replacements: list[tuple[str, str]] = []
    protected = text
    for index, (start, end, term) in enumerate(
        sorted(selected, key=lambda item: (item[0], item[1]), reverse=True)
    ):
        marker = f"ZXQTERM{index}QXZ"
        replacement = term.preferred_translation(target_language) or term.preferred_spelling
        protected = protected[:start] + marker + protected[end:]
        replacements.append((marker, replacement))
    return protected, tuple(replacements)


def restore_glossary_terms(
    text: str, replacements: tuple[tuple[str, str], ...]
) -> tuple[str, tuple[str, ...]]:
    corrected = text
    applied: list[str] = []
    for marker, replacement in replacements:
        pattern = re.compile(re.escape(marker), re.IGNORECASE)
        corrected, count = pattern.subn(replacement, corrected)
        if count:
            applied.append(replacement)
    return corrected, tuple(applied)


TranslationListener = Callable[[TranslationSnapshot], Awaitable[None]]


@dataclass(frozen=True)
class EnqueueOutcome:
    snapshot: TranslationSnapshot
    accepted: bool
    reason: str


class LocalLiveTranslationQueue:
    def __init__(self, config: LiveTranslationConfig, translator: Translator) -> None:
        config.validate()
        self.config = config
        self.translator = translator
        self._queue: asyncio.Queue[TranslationRequest | None] = asyncio.Queue(config.queue_capacity)
        self._jobs: dict[str, TranslationSnapshot] = {}
        self._requests: dict[str, TranslationRequest] = {}
        self._listeners: dict[str, TranslationListener] = {}
        self._latest_source_revision: dict[tuple[str, str], int] = {}
        self._latest: dict[tuple[str, str], TranslationSnapshot] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._closed = False
        self._metrics: dict[str, int | float] = {
            "queued_translation_jobs": 0,
            "preview_latency_total_ms": 0.0,
            "preview_count": 0,
            "final_translation_latency_total_ms": 0.0,
            "final_count": 0,
            "completed": 0,
            "failed": 0,
            "retries": 0,
            "glossary_terms_applied": 0,
            "replacement_count": 0,
            "discarded_duplicate": 0,
            "rejected_out_of_order": 0,
            "detection_confidence_total": 0.0,
            "detection_confidence_count": 0,
        }

    async def enqueue(
        self, request: TranslationRequest, listener: TranslationListener
    ) -> EnqueueOutcome:
        request.validate()
        key = (request.session_id, request.segment_id)
        if request.job_id in self._jobs:
            self._metrics["discarded_duplicate"] += 1
            return EnqueueOutcome(self._jobs[request.job_id], False, "duplicate")
        latest_revision = self._latest_source_revision.get(key, 0)
        if request.source_revision < latest_revision:
            self._metrics["rejected_out_of_order"] += 1
            current = self._latest.get(key)
            if current is None:
                raise RuntimeError("Translation revision registry is inconsistent")
            return EnqueueOutcome(current, False, "out_of_order")
        if request.source_revision == latest_revision and latest_revision:
            self._metrics["rejected_out_of_order"] += 1
            current = self._latest.get(key)
            if current is None:
                raise RuntimeError("Translation revision registry is inconsistent")
            return EnqueueOutcome(current, False, "revision_conflict")
        if self._queue.full():
            raise asyncio.QueueFull("Live translation queue capacity reached")
        now = utc_now()
        snapshot = TranslationSnapshot(
            job_id=request.job_id,
            session_id=request.session_id,
            segment_id=request.segment_id,
            source_revision=request.source_revision,
            source_state=request.source_state,
            source_text=request.source_text,
            status=TranslationStatus.PENDING,
            translation_revision=(self._latest.get(key).translation_revision + 1 if key in self._latest else 1),
            created_at=now,
            updated_at=now,
            glossary=request.glossary,
        )
        self._latest_source_revision[key] = request.source_revision
        self._latest[key] = snapshot
        self._jobs[request.job_id] = snapshot
        self._requests[request.job_id] = request
        self._listeners[request.job_id] = listener
        self._metrics["queued_translation_jobs"] += 1
        self._start_workers()
        self._queue.put_nowait(request)
        await self._notify(request.job_id, snapshot)
        return EnqueueOutcome(snapshot, True, "accepted")

    def snapshot(self, session_id: str) -> list[TranslationSnapshot]:
        return sorted(
            (item for (owner, _), item in self._latest.items() if owner == session_id),
            key=lambda item: (item.created_at, item.segment_id),
        )

    def context(self, session_id: str, *, before_segment_id: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        items = [
            item for item in self.snapshot(session_id)
            if item.segment_id != before_segment_id and item.status in {TranslationStatus.PREVIEW, TranslationStatus.COMPLETED}
        ][-self.config.context_segments :]
        return (
            tuple(item.segment_id for item in items),
            tuple(item.source_text for item in items),
        )

    def metrics(self) -> dict[str, int | float]:
        preview_count = int(self._metrics["preview_count"])
        final_count = int(self._metrics["final_count"])
        confidence_count = int(self._metrics["detection_confidence_count"])
        return {
            "queued_translation_jobs": int(self._metrics["queued_translation_jobs"]),
            "preview_latency_ms": round(float(self._metrics["preview_latency_total_ms"]) / preview_count, 3) if preview_count else 0.0,
            "final_translation_latency_ms": round(float(self._metrics["final_translation_latency_total_ms"]) / final_count, 3) if final_count else 0.0,
            "completed": int(self._metrics["completed"]),
            "failed": int(self._metrics["failed"]),
            "retries": int(self._metrics["retries"]),
            "queue_depth": self._queue.qsize(),
            "model_load_time_ms": round(float(self.translator.model_load_time_ms), 3),
            "glossary_terms_applied": int(self._metrics["glossary_terms_applied"]),
            "replacement_count": int(self._metrics["replacement_count"]),
            "discarded_duplicate": int(self._metrics["discarded_duplicate"]),
            "rejected_out_of_order": int(self._metrics["rejected_out_of_order"]),
            "language_detection_confidence": round(float(self._metrics["detection_confidence_total"]) / confidence_count, 3) if confidence_count else 0.0,
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

    async def _process(self, request: TranslationRequest) -> None:
        key = request.job_id
        for attempt in range(1, self.config.max_retries + 2):
            current = self._jobs[key]
            processing = replace(
                current, status=TranslationStatus.PROCESSING, attempt=attempt,
                updated_at=utc_now(), error=None
            )
            self._jobs[key] = processing
            if self._latest.get((request.session_id, request.segment_id), current).job_id == key:
                self._latest[(request.session_id, request.segment_id)] = processing
            await self._notify(key, processing)
            started = perf_counter()
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self.translator.translate, request, self.config.timeout_seconds),
                    timeout=self.config.timeout_seconds,
                )
            except Exception as exc:
                if attempt <= self.config.max_retries:
                    self._metrics["retries"] += 1
                    continue
                failed = replace(
                    processing, status=TranslationStatus.FAILED,
                    updated_at=utc_now(), error=f"{type(exc).__name__}: {exc}"
                )
                self._jobs[key] = failed
                if self._latest.get((request.session_id, request.segment_id), current).job_id == key:
                    self._latest[(request.session_id, request.segment_id)] = failed
                self._metrics["failed"] += 1
                await self._notify(key, failed)
                return
            latency = (perf_counter() - started) * 1000
            status = TranslationStatus.PREVIEW if request.source_state is TranscriptState.STABLE else TranslationStatus.COMPLETED
            completed = replace(processing, status=status, result=result, updated_at=utc_now())
            self._jobs[key] = completed
            latest_key = (request.session_id, request.segment_id)
            is_latest = self._latest_source_revision.get(latest_key) == request.source_revision
            if is_latest:
                replaced_preview = any(
                    item.session_id == request.session_id
                    and item.segment_id == request.segment_id
                    and item.source_state is TranscriptState.STABLE
                    and item.status is TranslationStatus.PREVIEW
                    for item in self._jobs.values()
                )
                if status is TranslationStatus.COMPLETED and replaced_preview:
                    self._metrics["replacement_count"] += 1
                self._latest[latest_key] = completed
            metric_prefix = "preview" if status is TranslationStatus.PREVIEW else "final"
            self._metrics[f"{metric_prefix}_latency_total_ms"] += latency
            self._metrics[f"{metric_prefix}_count"] += 1
            self._metrics["completed"] += 1
            self._metrics["glossary_terms_applied"] += len(result.glossary_terms_applied)
            confidence = result.metadata.detection_confidence
            if confidence is not None:
                self._metrics["detection_confidence_total"] += confidence
                self._metrics["detection_confidence_count"] += 1
            await self._notify(key, completed)
            return

    async def _notify(self, key: str, snapshot: TranslationSnapshot) -> None:
        listener = self._listeners.get(key)
        if listener is not None:
            try:
                await listener(snapshot)
            except Exception:
                return

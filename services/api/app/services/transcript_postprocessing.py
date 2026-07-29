"""Deterministic, bounded post-processing for final transcript revisions."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from time import perf_counter
from typing import Awaitable, Callable, Protocol

from .glossary import DisabledGlossarySnapshot, GlossarySnapshot


GlossaryType = GlossarySnapshot | DisabledGlossarySnapshot | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TranscriptPostprocessStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class TranscriptPostprocessConfig:
    filler_mode: str = "preserve"
    filler_words: tuple[str, ...] = (
        "uh", "um", "erm", "hmm", "eh", "anu", "eee", "mmm",
    )
    paragraph_sentences: int = 3
    timeout_seconds: float = 2.0
    max_retries: int = 1
    worker_concurrency: int = 1
    queue_capacity: int = 64

    def validate(self) -> None:
        if self.filler_mode not in {"preserve", "remove"}:
            raise ValueError("Transcript filler mode must be preserve or remove")
        if self.paragraph_sentences < 1 or self.paragraph_sentences > 20:
            raise ValueError("Transcript paragraph sentence count must be 1-20")
        if self.timeout_seconds <= 0 or not 0 <= self.max_retries <= 10:
            raise ValueError("Transcript post-processing timeout/retry is invalid")
        if not 1 <= self.worker_concurrency <= 8:
            raise ValueError("Transcript post-processing concurrency must be 1-8")
        if self.queue_capacity < self.worker_concurrency:
            raise ValueError("Transcript post-processing capacity must cover concurrency")


@dataclass(frozen=True)
class TranscriptPostprocessRequest:
    session_id: str
    segment_id: str
    source_revision: int
    source_kind: str
    raw_transcript: str
    glossary_corrected_transcript: str
    language: str
    model: str
    sequence_start: int
    sequence_end: int
    start_ms: float
    end_ms: float
    glossary_version: str | None = None
    glossary: GlossaryType = field(default=None, compare=False, repr=False)

    @property
    def job_id(self) -> str:
        digest = hashlib.sha256(
            self.glossary_corrected_transcript.encode("utf-8")
        ).hexdigest()
        return hashlib.sha256(
            f"{self.session_id}\0{self.segment_id}\0{self.source_revision}\0{digest}".encode()
        ).hexdigest()

    def validate(self) -> None:
        if not self.session_id or not self.segment_id:
            raise ValueError("Transcript post-processing sessionId and segmentId are required")
        if self.source_revision < 1 or self.source_kind not in {"final", "accurate_final"}:
            raise ValueError("Transcript post-processing requires a final source revision")
        if not self.glossary_corrected_transcript.strip():
            raise ValueError("Transcript post-processing input cannot be empty")
        if self.sequence_start < 0 or self.sequence_end < self.sequence_start:
            raise ValueError("Transcript sequence range is invalid")
        if self.start_ms < 0 or self.end_ms < self.start_ms:
            raise ValueError("Transcript timestamp range is invalid")


@dataclass(frozen=True)
class TranscriptCorrection:
    rule: str
    before: str
    after: str

    def as_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "before": self.before, "after": self.after}


@dataclass(frozen=True)
class TranscriptPostprocessResult:
    text: str
    corrections: tuple[TranscriptCorrection, ...]
    latency_ms: float
    duplicate_phrases_removed: int
    filler_words_handled: int
    protected_tokens: int


@dataclass(frozen=True)
class TranscriptPostprocessSnapshot:
    job_id: str
    session_id: str
    segment_id: str
    source_revision: int
    source_kind: str
    status: TranscriptPostprocessStatus
    raw_transcript: str
    glossary_corrected_transcript: str
    post_processed_transcript: str
    language: str
    model: str
    sequence_start: int
    sequence_end: int
    start_ms: float
    end_ms: float
    glossary_version: str | None
    attempt: int = 0
    applied_corrections: tuple[TranscriptCorrection, ...] = ()
    latency_ms: float = 0.0
    error: str | None = None
    fallback: bool = False
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, object]:
        return {
            "jobId": self.job_id, "sessionId": self.session_id,
            "segmentId": self.segment_id, "sourceRevision": self.source_revision,
            "sourceKind": self.source_kind, "status": self.status.value,
            "rawTranscript": self.raw_transcript,
            "glossaryCorrectedTranscript": self.glossary_corrected_transcript,
            "postProcessedTranscript": self.post_processed_transcript,
            "language": self.language, "model": self.model,
            "sequenceStart": self.sequence_start, "sequenceEnd": self.sequence_end,
            "startMs": self.start_ms, "endMs": self.end_ms,
            "glossaryVersion": self.glossary_version, "attempt": self.attempt,
            "appliedCorrections": [item.as_dict() for item in self.applied_corrections],
            "latencyMs": round(self.latency_ms, 3), "error": self.error,
            "fallback": self.fallback, "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }


class TranscriptProcessor(Protocol):
    def process(self, request: TranscriptPostprocessRequest) -> TranscriptPostprocessResult: ...


class DeterministicTranscriptProcessor:
    def __init__(self, config: TranscriptPostprocessConfig) -> None:
        config.validate()
        self.config = config

    def process(self, request: TranscriptPostprocessRequest) -> TranscriptPostprocessResult:
        request.validate()
        started = perf_counter()
        current = request.glossary_corrected_transcript
        corrections: list[TranscriptCorrection] = []

        if request.glossary is not None:
            terminology = request.glossary.correct(
                current, language=request.language, record_metrics=False
            ).corrected_text
            _record(corrections, "technical_terms", current, terminology)
            current = terminology

        for rule, transform in (
            ("whitespace", _normalize_whitespace),
            ("number", _normalize_numbers),
            ("date_time", _normalize_date_time),
        ):
            before = current
            current = transform(current)
            _record(corrections, rule, before, current)

        protected, replacements = _protect(current, request.glossary)
        filler_count = 0
        if self.config.filler_mode == "remove":
            before = protected
            protected, filler_count = _remove_fillers(protected, self.config.filler_words)
            _record(corrections, "filler_words", before, protected)

        before = protected
        protected = _normalize_punctuation(protected)
        _record(corrections, "punctuation", before, protected)
        before = protected
        protected, duplicate_count = _remove_repeated_phrases(protected)
        _record(corrections, "repeated_phrase", before, protected)
        before = protected
        protected = _normalize_capitalization(protected)
        _record(corrections, "capitalization", before, protected)
        before = protected
        protected = _segment_paragraphs(protected, self.config.paragraph_sentences)
        _record(corrections, "paragraph", before, protected)
        restored = _restore(protected, replacements)

        violation = _safety_violation(request.glossary_corrected_transcript, restored)
        if violation:
            raise ValueError(f"Transcript safety validation failed: {violation}")
        visible_corrections = tuple(
            TranscriptCorrection(
                item.rule,
                _restore(item.before, replacements),
                _restore(item.after, replacements),
            )
            for item in corrections
        )
        return TranscriptPostprocessResult(
            text=restored, corrections=visible_corrections,
            latency_ms=(perf_counter() - started) * 1000,
            duplicate_phrases_removed=duplicate_count,
            filler_words_handled=filler_count,
            protected_tokens=len(replacements),
        )


def _record(items: list[TranscriptCorrection], rule: str, before: str, after: str) -> None:
    if before != after:
        items.append(TranscriptCorrection(rule, before, after))


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text, flags=re.UNICODE).strip()


def _normalize_numbers(text: str) -> str:
    value = re.sub(r"\b(\d{1,3})\s+(\d{3})\b", r"\1,\2", text)
    return re.sub(r"\s+%", "%", value)


def _normalize_date_time(text: str) -> str:
    value = re.sub(r"\b(\d{4})/(\d{1,2})/(\d{1,2})\b", r"\1-\2-\3", text)
    value = re.sub(r"\b(\d{1,2})\.(\d{2})(?=\s|$)", r"\1:\2", value)
    return value


def _normalize_punctuation(text: str) -> str:
    value = re.sub(r"\s+([,.;:!?])", r"\1", text)
    value = re.sub(r"([,;:!?])(?=[^\s,.;:!?])", r"\1 ", value)
    value = re.sub(r"([!?])\1+", r"\1", value)
    value = re.sub(r"\.{2,}", ".", value)
    value = re.sub(r"\s+", " ", value).strip()
    if value and value[-1] not in ".!?)]}\"'":
        value += "."
    return value


def _normalize_capitalization(text: str) -> str:
    return re.sub(
        r"(^|[.!?]\s+)([a-zà-öø-ÿ])",
        lambda match: match.group(1) + match.group(2).upper(), text,
    )


def _remove_fillers(text: str, words: tuple[str, ...]) -> tuple[str, int]:
    forms = [re.escape(word) for word in words if word.strip()]
    if not forms:
        return text, 0
    value, count = re.subn(
        rf"(?<![\w])(?:{'|'.join(forms)})(?![\w])(?:\s*[,;]\s*)?",
        "", text, flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", value).strip(), count


def _remove_repeated_phrases(text: str) -> tuple[str, int]:
    removed = 0
    chunks = re.findall(r"[^.!?]+[.!?]?", text)
    result: list[str] = []
    previous: str | None = None
    for chunk in chunks:
        key = re.sub(r"\W+", " ", chunk).strip().casefold()
        if key and key == previous:
            removed += 1
            continue
        result.append(chunk)
        previous = key
    value = "".join(result)
    word = r"[\w'-]+"
    for size in range(8, 1, -1):
        pattern = rf"\b({word}(?:\s+{word}){{{size - 1}}})(?:\s+\1\b)+"
        value, count = re.subn(pattern, r"\1", value, flags=re.IGNORECASE)
        removed += count
    return value, removed


_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:https?://|www\.)[^\s<>()]*[^\s<>().,!?;:]", re.IGNORECASE),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:[vV]\d+(?:\.\d+){1,4}|\d+\.\d+\.\d+(?:\.\d+){0,2})\b"),
    re.compile(r"\b(?=[A-Z0-9._-]*[A-Z])(?=[A-Z0-9._-]*\d)[A-Z0-9]+(?:[._-][A-Z0-9]+)*\b"),
    re.compile(r"\b[A-Z]{2,10}\b"),
    re.compile(r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?\b"),
    re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)*(?:%|\s?(?:kg|km|ms|GB|MB|USD|IDR))?(?![\w])"),
    re.compile(r"(?:\[(?:Speaker|Pembicara)\s+[^\]]+\]|(?:Speaker|Pembicara)\s+\w+)\s*:", re.IGNORECASE),
    re.compile(r"\b(?:not|no|never|without|cannot|can't|don't|doesn't|isn't|aren't|tidak|bukan|jangan|belum)\b", re.IGNORECASE),
)


def _term_pattern(value: str) -> re.Pattern[str]:
    escaped = re.escape(value).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![\w]){escaped}(?![\w])", re.IGNORECASE)


def _protect(text: str, glossary: GlossaryType) -> tuple[str, tuple[tuple[str, str], ...]]:
    candidates: list[tuple[int, int, str]] = []
    for pattern in _PATTERNS:
        candidates.extend((m.start(), m.end(), m.group(0)) for m in pattern.finditer(text))
    for term in getattr(glossary, "terms", ()):
        candidates.extend(
            (m.start(), m.end(), m.group(0))
            for m in _term_pattern(term.preferred_spelling).finditer(text)
        )
    selected: list[tuple[int, int, str]] = []
    for item in sorted(candidates, key=lambda value: (-(value[1] - value[0]), value[0])):
        if not any(item[0] < kept[1] and kept[0] < item[1] for kept in selected):
            selected.append(item)
    protected = text
    replacements: list[tuple[str, str]] = []
    for index, (start, end, original) in enumerate(sorted(selected, reverse=True)):
        marker = f"ZXQTRSAFE{index}QXZ"
        protected = protected[:start] + marker + protected[end:]
        replacements.append((marker, original))
    return protected, tuple(replacements)


def _restore(text: str, replacements: tuple[tuple[str, str], ...]) -> str:
    for marker, original in replacements:
        text = re.sub(re.escape(marker), lambda _: original, text, flags=re.IGNORECASE)
    return text


def _segment_paragraphs(text: str, sentences_per_paragraph: int) -> str:
    sentences = [item.strip() for item in re.findall(r"[^.!?]+[.!?]+|[^.!?]+$", text) if item.strip()]
    if not sentences:
        return text.strip()
    paragraphs = [
        " ".join(sentences[index:index + sentences_per_paragraph])
        for index in range(0, len(sentences), sentences_per_paragraph)
    ]
    return "\n\n".join(paragraphs)


def _safety_violation(before: str, after: str) -> str | None:
    if Counter(re.findall(r"\d+", before)) != Counter(re.findall(r"\d+", after)):
        return "numeric digits changed"
    for pattern, label in ((_PATTERNS[0], "URL"), (_PATTERNS[1], "email"), (_PATTERNS[2], "version"), (_PATTERNS[3], "code"), (_PATTERNS[8], "speaker attribution"), (_PATTERNS[9], "negation")):
        before_values = Counter(value.casefold() for value in pattern.findall(before))
        after_values = Counter(value.casefold() for value in pattern.findall(after))
        if before_values != after_values:
            return f"{label} changed"
    return None


PostprocessListener = Callable[[TranscriptPostprocessSnapshot], Awaitable[None]]


@dataclass(frozen=True)
class PostprocessEnqueueOutcome:
    snapshot: TranscriptPostprocessSnapshot
    accepted: bool
    reason: str


class LocalTranscriptPostprocessQueue:
    def __init__(self, config: TranscriptPostprocessConfig, processor: TranscriptProcessor | None = None) -> None:
        config.validate()
        self.config = config
        self.processor = processor or DeterministicTranscriptProcessor(config)
        self._queue: asyncio.Queue[TranscriptPostprocessRequest | None] = asyncio.Queue(config.queue_capacity)
        self._jobs: dict[str, TranscriptPostprocessSnapshot] = {}
        self._latest: dict[tuple[str, str], TranscriptPostprocessSnapshot] = {}
        self._listeners: dict[str, PostprocessListener] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._executor = ThreadPoolExecutor(max_workers=config.worker_concurrency, thread_name_prefix="transcript-postprocess")
        self._closed = False
        self._metrics: dict[str, int | float] = {
            "post_processing_jobs": 0, "completed": 0, "failed": 0,
            "retries": 0, "correction_count": 0,
            "duplicate_phrases_removed": 0, "filler_words_handled": 0,
            "protected_tokens": 0, "processing_latency_total_ms": 0.0,
            "fallback_count": 0, "discarded_duplicate": 0,
            "rejected_out_of_order": 0,
        }

    async def enqueue(self, request: TranscriptPostprocessRequest, listener: PostprocessListener) -> PostprocessEnqueueOutcome:
        request.validate()
        key = (request.session_id, request.segment_id)
        if request.job_id in self._jobs:
            self._metrics["discarded_duplicate"] += 1
            return PostprocessEnqueueOutcome(self._jobs[request.job_id], False, "duplicate")
        current = self._latest.get(key)
        if current is not None and request.source_revision <= current.source_revision:
            self._metrics["rejected_out_of_order"] += 1
            return PostprocessEnqueueOutcome(current, False, "out_of_order")
        if self._queue.full():
            raise asyncio.QueueFull("Transcript post-processing queue capacity reached")
        now = utc_now()
        snapshot = TranscriptPostprocessSnapshot(
            job_id=request.job_id, session_id=request.session_id,
            segment_id=request.segment_id, source_revision=request.source_revision,
            source_kind=request.source_kind, status=TranscriptPostprocessStatus.PENDING,
            raw_transcript=request.raw_transcript,
            glossary_corrected_transcript=request.glossary_corrected_transcript,
            post_processed_transcript=request.glossary_corrected_transcript,
            language=request.language, model=request.model,
            sequence_start=request.sequence_start, sequence_end=request.sequence_end,
            start_ms=request.start_ms, end_ms=request.end_ms,
            glossary_version=request.glossary_version, created_at=now, updated_at=now,
        )
        self._jobs[request.job_id] = snapshot
        self._latest[key] = snapshot
        self._listeners[request.job_id] = listener
        self._metrics["post_processing_jobs"] += 1
        self._start_workers()
        self._queue.put_nowait(request)
        await self._notify(request.job_id, snapshot)
        return PostprocessEnqueueOutcome(snapshot, True, "accepted")

    def snapshot(self, session_id: str) -> list[TranscriptPostprocessSnapshot]:
        return sorted(
            (item for (owner, _), item in self._latest.items() if owner == session_id),
            key=lambda item: (item.sequence_start, item.segment_id),
        )

    def metrics(self) -> dict[str, int | float]:
        processed = int(self._metrics["completed"]) + int(self._metrics["failed"])
        return {
            **{key: int(value) for key, value in self._metrics.items() if key != "processing_latency_total_ms"},
            "processing_latency_ms": round(float(self._metrics["processing_latency_total_ms"]) / processed, 3) if processed else 0.0,
            "queue_depth": self._queue.qsize(),
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

    async def _process(self, request: TranscriptPostprocessRequest) -> None:
        key = request.job_id
        latest_key = (request.session_id, request.segment_id)
        for attempt in range(1, self.config.max_retries + 2):
            processing = replace(self._jobs[key], status=TranscriptPostprocessStatus.PROCESSING, attempt=attempt, updated_at=utc_now(), error=None, fallback=False)
            self._jobs[key] = processing
            if self._latest.get(latest_key, processing).job_id == key:
                self._latest[latest_key] = processing
            await self._notify(key, processing)
            started = perf_counter()
            try:
                future = asyncio.get_running_loop().run_in_executor(self._executor, self.processor.process, request)
                result = await asyncio.wait_for(future, timeout=self.config.timeout_seconds)
            except Exception as exc:
                if attempt <= self.config.max_retries:
                    self._metrics["retries"] += 1
                    continue
                failed = replace(processing, status=TranscriptPostprocessStatus.FAILED, post_processed_transcript=request.glossary_corrected_transcript, error=f"{type(exc).__name__}: {exc}", fallback=True, updated_at=utc_now())
                self._jobs[key] = failed
                if self._latest.get(latest_key, processing).job_id == key:
                    self._latest[latest_key] = failed
                self._metrics["failed"] += 1
                self._metrics["fallback_count"] += 1
                self._metrics["processing_latency_total_ms"] += (perf_counter() - started) * 1000
                await self._notify(key, failed)
                return
            completed = replace(processing, status=TranscriptPostprocessStatus.COMPLETED, post_processed_transcript=result.text, applied_corrections=result.corrections, latency_ms=result.latency_ms, updated_at=utc_now())
            self._jobs[key] = completed
            if self._latest.get(latest_key, processing).job_id == key:
                self._latest[latest_key] = completed
            self._metrics["completed"] += 1
            self._metrics["correction_count"] += len(result.corrections)
            self._metrics["duplicate_phrases_removed"] += result.duplicate_phrases_removed
            self._metrics["filler_words_handled"] += result.filler_words_handled
            self._metrics["protected_tokens"] += result.protected_tokens
            self._metrics["processing_latency_total_ms"] += result.latency_ms
            await self._notify(key, completed)
            return

    async def _notify(self, key: str, snapshot: TranscriptPostprocessSnapshot) -> None:
        listener = self._listeners.get(key)
        if listener is not None:
            try:
                await listener(snapshot)
            except Exception:
                pass

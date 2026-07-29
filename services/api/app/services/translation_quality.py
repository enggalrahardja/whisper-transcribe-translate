"""Deterministic, bounded quality pass for completed local translations."""

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

from .glossary import DisabledGlossarySnapshot, GlossarySnapshot, GlossaryTerm


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class QualityStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class TranslationQualityConfig:
    timeout_seconds: float = 2.0
    max_retries: int = 1
    worker_concurrency: int = 1
    queue_capacity: int = 64

    def validate(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("Quality timeout must be positive")
        if not 0 <= self.max_retries <= 10:
            raise ValueError("Quality max retries must be between 0 and 10")
        if not 1 <= self.worker_concurrency <= 8:
            raise ValueError("Quality worker concurrency must be between 1 and 8")
        if self.queue_capacity < self.worker_concurrency:
            raise ValueError("Quality queue capacity must cover worker concurrency")


GlossaryType = GlossarySnapshot | DisabledGlossarySnapshot | None


@dataclass(frozen=True)
class TranslationQualityRequest:
    session_id: str
    segment_id: str
    translation_revision: int
    source_text: str
    raw_model_translation: str
    final_translation: str
    source_language: str
    target_language: str
    glossary_version: str | None
    start_ms: float | None
    end_ms: float | None
    glossary: GlossaryType = field(default=None, compare=False, repr=False)

    @property
    def job_id(self) -> str:
        value = (
            f"{self.session_id}\0{self.segment_id}\0{self.translation_revision}\0"
            f"{hashlib.sha256(self.final_translation.encode('utf-8')).hexdigest()}"
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def validate(self) -> None:
        if not self.session_id or not self.segment_id:
            raise ValueError("Quality sessionId and segmentId are required")
        if self.translation_revision < 1:
            raise ValueError("Quality translation revision must be positive")
        if not self.final_translation.strip():
            raise ValueError("Quality input must be a completed final translation")
        if not self.source_language or not self.target_language:
            raise ValueError("Quality language metadata is required")
        if self.start_ms is not None and self.start_ms < 0:
            raise ValueError("Quality start timestamp cannot be negative")
        if self.end_ms is not None and (
            self.end_ms < 0 or (self.start_ms is not None and self.end_ms < self.start_ms)
        ):
            raise ValueError("Quality timestamp range is invalid")


@dataclass(frozen=True)
class QualityCorrection:
    rule: str
    before: str
    after: str

    def as_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "before": self.before, "after": self.after}


@dataclass(frozen=True)
class TranslationQualityResult:
    raw_translation: str
    corrected_translation: str
    applied_corrections: tuple[QualityCorrection, ...]
    latency_ms: float
    terminology_corrections: int
    protected_value_count: int


@dataclass(frozen=True)
class TranslationQualitySnapshot:
    job_id: str
    session_id: str
    segment_id: str
    translation_revision: int
    status: QualityStatus
    source_text: str
    raw_model_translation: str
    raw_translation: str
    corrected_translation: str
    source_language: str
    target_language: str
    glossary_version: str | None
    start_ms: float | None
    end_ms: float | None
    attempt: int = 0
    applied_corrections: tuple[QualityCorrection, ...] = ()
    latency_ms: float = 0.0
    error: str | None = None
    fallback: bool = False
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, object]:
        return {
            "jobId": self.job_id,
            "sessionId": self.session_id,
            "segmentId": self.segment_id,
            "translationRevision": self.translation_revision,
            "status": self.status.value,
            "sourceText": self.source_text,
            "rawModelTranslation": self.raw_model_translation,
            "rawTranslation": self.raw_translation,
            "correctedTranslation": self.corrected_translation,
            "sourceLanguage": self.source_language,
            "targetLanguage": self.target_language,
            "glossaryVersion": self.glossary_version,
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "attempt": self.attempt,
            "appliedCorrections": [item.as_dict() for item in self.applied_corrections],
            "latencyMs": round(self.latency_ms, 3),
            "error": self.error,
            "fallback": self.fallback,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
        }


class QualityProcessor(Protocol):
    def process(self, request: TranslationQualityRequest) -> TranslationQualityResult: ...


class DeterministicTranslationQualityProcessor:
    """Allow-listed text normalization with invariant validation and no generation."""

    def process(self, request: TranslationQualityRequest) -> TranslationQualityResult:
        request.validate()
        started = perf_counter()
        current = request.final_translation
        corrections: list[QualityCorrection] = []

        current, terminology_count = _apply_terminology(
            current,
            request.source_text,
            request.target_language,
            request.glossary,
        )
        _record_change(corrections, "terminology", request.final_translation, current)

        terminology_values = _applicable_terminology_values(
            request.source_text,
            request.target_language,
            request.glossary,
        )
        protected, replacements, protection_count = _protect_invariants(
            current,
            terminology_values=terminology_values,
        )
        for rule, transform in (
            ("whitespace", _normalize_whitespace),
            ("punctuation", _normalize_punctuation),
            ("repeated_phrase", _remove_repeated_phrases),
            ("capitalization", _normalize_capitalization),
        ):
            before = protected
            protected = transform(protected)
            _record_change(corrections, rule, before, protected)
        corrected = _restore_invariants(protected, replacements)

        violation = _safety_violation(request.final_translation, corrected)
        if violation is not None:
            raise ValueError(f"Quality safety validation failed: {violation}")
        return TranslationQualityResult(
            raw_translation=request.final_translation,
            corrected_translation=corrected,
            applied_corrections=tuple(corrections),
            latency_ms=(perf_counter() - started) * 1000,
            terminology_corrections=terminology_count,
            protected_value_count=protection_count,
        )


def _record_change(
    corrections: list[QualityCorrection], rule: str, before: str, after: str
) -> None:
    if before != after:
        corrections.append(QualityCorrection(rule, before, after))


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text, flags=re.UNICODE).strip()


def _normalize_punctuation(text: str) -> str:
    value = re.sub(r"\s+([,.;:!?])", r"\1", text)
    value = re.sub(r"([,;:!?])(?=[^\s,.;:!?])", r"\1 ", value)
    value = re.sub(r"!{2,}", "!", value)
    value = re.sub(r"\?{2,}", "?", value)
    value = re.sub(r"\.{2,}", ".", value)
    if value and value[-1] not in ".!?)]}\"'":
        value += "."
    return value


def _normalize_capitalization(text: str) -> str:
    pattern = re.compile(r"(^|[.!?]\s+)([a-zà-öø-ÿ])", re.UNICODE)
    return pattern.sub(lambda match: match.group(1) + match.group(2).upper(), text)


def _remove_repeated_phrases(text: str) -> str:
    chunks = re.findall(r"[^.!?]+[.!?]?", text)
    deduplicated: list[str] = []
    previous_key: str | None = None
    for chunk in chunks:
        key = re.sub(r"\W+", " ", chunk, flags=re.UNICODE).strip().casefold()
        if key and key == previous_key:
            continue
        deduplicated.append(chunk)
        previous_key = key
    value = "".join(deduplicated)
    word = r"[\w'-]+"
    for size in range(8, 1, -1):
        phrase = rf"({word}(?:\s+{word}){{{size - 1}}})"
        value = re.sub(rf"\b{phrase}(?:\s+\1\b)+", r"\1", value, flags=re.IGNORECASE)
    return value


def _term_pattern(value: str) -> re.Pattern[str]:
    escaped = re.escape(value).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![\w]){escaped}(?![\w])", re.IGNORECASE | re.UNICODE)


def _apply_terminology(
    text: str,
    source_text: str,
    target_language: str,
    glossary: GlossaryType,
) -> tuple[str, int]:
    terms: tuple[GlossaryTerm, ...] = getattr(glossary, "terms", ())
    current = text
    count = 0
    for term in sorted(terms, key=lambda item: (-item.priority, item.preferred_spelling.casefold())):
        expected = term.preferred_translation(target_language)
        if expected is None and term.do_not_translate:
            expected = term.preferred_spelling
        if expected is None:
            continue
        source_present = any(
            _term_pattern(form).search(source_text)
            for form in (term.preferred_spelling, *term.aliases)
        )
        if not source_present:
            continue
        target_forms = {
            term.preferred_spelling,
            *term.aliases,
            *(value for _, value in term.preferred_translations),
        }
        for form in sorted(target_forms, key=len, reverse=True):
            if not form or form.casefold() == expected.casefold():
                continue
            current, replacements = _term_pattern(form).subn(expected, current)
            count += replacements
    return current, count


def _applicable_terminology_values(
    source_text: str,
    target_language: str,
    glossary: GlossaryType,
) -> tuple[str, ...]:
    terms: tuple[GlossaryTerm, ...] = getattr(glossary, "terms", ())
    values: list[str] = []
    for term in terms:
        source_present = any(
            _term_pattern(form).search(source_text)
            for form in (term.preferred_spelling, *term.aliases)
        )
        if not source_present:
            continue
        expected = term.preferred_translation(target_language)
        if expected is None and term.do_not_translate:
            expected = term.preferred_spelling
        if expected:
            values.append(expected)
    return tuple(dict.fromkeys(values))


_PROTECTED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("date", re.compile(r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b")),
    ("time", re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[APap][Mm])?\b")),
    ("version", re.compile(r"\b[vV]?\d+(?:\.\d+){1,4}\b")),
    ("code", re.compile(r"\b(?=[A-Z0-9._-]*[A-Z])(?=[A-Z0-9._-]*\d)[A-Z0-9]+(?:[._-][A-Z0-9]+)*\b")),
    ("number", re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)*(?:%|\s?(?:kg|km|ms|GB|MB|USD|IDR))?(?![\w])")),
    ("speaker", re.compile(r"(?:\[(?:Speaker|Pembicara)\s+[^\]]+\]|(?:Speaker|Pembicara)\s+\w+)\s*:", re.IGNORECASE)),
)


def _protect_invariants(
    text: str,
    *,
    terminology_values: tuple[str, ...] = (),
) -> tuple[str, tuple[tuple[str, str], ...], int]:
    candidates: list[tuple[int, int, str, str]] = []
    for kind, pattern in _PROTECTED_PATTERNS:
        candidates.extend((match.start(), match.end(), kind, match.group(0)) for match in pattern.finditer(text))
    for value in terminology_values:
        candidates.extend(
            (match.start(), match.end(), "terminology", match.group(0))
            for match in _term_pattern(value).finditer(text)
        )
    selected: list[tuple[int, int, str, str]] = []
    for item in sorted(candidates, key=lambda value: (-(value[1] - value[0]), value[0])):
        if not any(item[0] < kept[1] and kept[0] < item[1] for kept in selected):
            selected.append(item)
    protected = text
    replacements: list[tuple[str, str]] = []
    protected_count = 0
    for index, (start, end, kind, original) in enumerate(
        sorted(selected, key=lambda value: value[0], reverse=True)
    ):
        marker = f"ZXQSAFE{index}QXZ"
        protected = protected[:start] + marker + protected[end:]
        replacements.append((marker, original))
        if kind not in {"speaker", "terminology"}:
            protected_count += 1
    return protected, tuple(replacements), protected_count


def _restore_invariants(text: str, replacements: tuple[tuple[str, str], ...]) -> str:
    restored = text
    for marker, original in replacements:
        restored = re.sub(re.escape(marker), lambda _: original, restored, flags=re.IGNORECASE)
    return restored


def _safety_violation(before: str, after: str) -> str | None:
    if Counter(re.findall(r"\d+", before)) != Counter(re.findall(r"\d+", after)):
        return "number/date/time/code digits changed"
    negation = re.compile(
        r"\b(?:not|no|never|without|cannot|can't|don't|doesn't|isn't|aren't|"
        r"tidak|bukan|jangan|belum)\b",
        re.IGNORECASE,
    )
    if Counter(item.casefold() for item in negation.findall(before)) != Counter(
        item.casefold() for item in negation.findall(after)
    ):
        return "negation changed"
    speaker = _PROTECTED_PATTERNS[-1][1]
    if speaker.findall(before) != speaker.findall(after):
        return "speaker attribution changed"
    return None


QualityListener = Callable[[TranslationQualitySnapshot], Awaitable[None]]


@dataclass(frozen=True)
class QualityEnqueueOutcome:
    snapshot: TranslationQualitySnapshot
    accepted: bool
    reason: str


class LocalTranslationQualityQueue:
    def __init__(
        self,
        config: TranslationQualityConfig,
        processor: QualityProcessor | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.processor = processor or DeterministicTranslationQualityProcessor()
        self._queue: asyncio.Queue[TranslationQualityRequest | None] = asyncio.Queue(config.queue_capacity)
        self._jobs: dict[str, TranslationQualitySnapshot] = {}
        self._latest: dict[tuple[str, str], TranslationQualitySnapshot] = {}
        self._latest_revision: dict[tuple[str, str], int] = {}
        self._listeners: dict[str, QualityListener] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._executor = ThreadPoolExecutor(
            max_workers=config.worker_concurrency,
            thread_name_prefix="translation-quality",
        )
        self._closed = False
        self._metrics: dict[str, int | float] = {
            "processed_quality_jobs": 0,
            "corrections_applied": 0,
            "failed_jobs": 0,
            "correction_latency_total_ms": 0.0,
            "fallback_count": 0,
            "terminology_corrections": 0,
            "number_date_protection_count": 0,
            "retries": 0,
            "discarded_duplicate": 0,
            "rejected_out_of_order": 0,
        }

    async def enqueue(
        self,
        request: TranslationQualityRequest,
        listener: QualityListener,
    ) -> QualityEnqueueOutcome:
        request.validate()
        key = (request.session_id, request.segment_id)
        if request.job_id in self._jobs:
            self._metrics["discarded_duplicate"] += 1
            return QualityEnqueueOutcome(self._jobs[request.job_id], False, "duplicate")
        latest_revision = self._latest_revision.get(key, 0)
        if request.translation_revision <= latest_revision:
            self._metrics["rejected_out_of_order"] += 1
            return QualityEnqueueOutcome(self._latest[key], False, "out_of_order")
        if self._queue.full():
            raise asyncio.QueueFull("Translation quality queue capacity reached")
        now = utc_now()
        snapshot = TranslationQualitySnapshot(
            job_id=request.job_id,
            session_id=request.session_id,
            segment_id=request.segment_id,
            translation_revision=request.translation_revision,
            status=QualityStatus.PENDING,
            source_text=request.source_text,
            raw_model_translation=request.raw_model_translation,
            raw_translation=request.final_translation,
            corrected_translation=request.final_translation,
            source_language=request.source_language,
            target_language=request.target_language,
            glossary_version=request.glossary_version,
            start_ms=request.start_ms,
            end_ms=request.end_ms,
            created_at=now,
            updated_at=now,
        )
        self._jobs[request.job_id] = snapshot
        self._latest[key] = snapshot
        self._latest_revision[key] = request.translation_revision
        self._listeners[request.job_id] = listener
        self._start_workers()
        self._queue.put_nowait(request)
        await self._notify(request.job_id, snapshot)
        return QualityEnqueueOutcome(snapshot, True, "accepted")

    def snapshot(self, session_id: str) -> list[TranslationQualitySnapshot]:
        return sorted(
            (item for (owner, _), item in self._latest.items() if owner == session_id),
            key=lambda item: (item.created_at, item.segment_id),
        )

    def metrics(self) -> dict[str, int | float]:
        processed = int(self._metrics["processed_quality_jobs"])
        return {
            "processed_quality_jobs": processed,
            "corrections_applied": int(self._metrics["corrections_applied"]),
            "failed_jobs": int(self._metrics["failed_jobs"]),
            "correction_latency_ms": round(float(self._metrics["correction_latency_total_ms"]) / processed, 3) if processed else 0.0,
            "fallback_count": int(self._metrics["fallback_count"]),
            "terminology_corrections": int(self._metrics["terminology_corrections"]),
            "number_date_protection_count": int(self._metrics["number_date_protection_count"]),
            "retries": int(self._metrics["retries"]),
            "queue_depth": self._queue.qsize(),
            "discarded_duplicate": int(self._metrics["discarded_duplicate"]),
            "rejected_out_of_order": int(self._metrics["rejected_out_of_order"]),
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

    async def _process(self, request: TranslationQualityRequest) -> None:
        key = request.job_id
        latest_key = (request.session_id, request.segment_id)
        for attempt in range(1, self.config.max_retries + 2):
            processing = replace(
                self._jobs[key], status=QualityStatus.PROCESSING, attempt=attempt,
                updated_at=utc_now(), error=None, fallback=False,
            )
            self._jobs[key] = processing
            if self._latest.get(latest_key, processing).job_id == key:
                self._latest[latest_key] = processing
            await self._notify(key, processing)
            started = perf_counter()
            try:
                inference = asyncio.get_running_loop().run_in_executor(
                    self._executor,
                    self.processor.process,
                    request,
                )
                result = await asyncio.wait_for(
                    inference,
                    timeout=self.config.timeout_seconds,
                )
            except Exception as exc:
                if attempt <= self.config.max_retries:
                    self._metrics["retries"] += 1
                    continue
                failed = replace(
                    processing,
                    status=QualityStatus.FAILED,
                    corrected_translation=request.final_translation,
                    updated_at=utc_now(),
                    error=f"{type(exc).__name__}: {exc}",
                    fallback=True,
                )
                self._jobs[key] = failed
                if self._latest.get(latest_key, processing).job_id == key:
                    self._latest[latest_key] = failed
                self._metrics["failed_jobs"] += 1
                self._metrics["fallback_count"] += 1
                self._metrics["processed_quality_jobs"] += 1
                self._metrics["correction_latency_total_ms"] += (
                    perf_counter() - started
                ) * 1000
                await self._notify(key, failed)
                return
            completed = replace(
                processing,
                status=QualityStatus.COMPLETED,
                corrected_translation=result.corrected_translation,
                applied_corrections=result.applied_corrections,
                latency_ms=result.latency_ms,
                updated_at=utc_now(),
            )
            self._jobs[key] = completed
            if self._latest_revision.get(latest_key) == request.translation_revision:
                self._latest[latest_key] = completed
            self._metrics["processed_quality_jobs"] += 1
            self._metrics["corrections_applied"] += len(result.applied_corrections)
            self._metrics["correction_latency_total_ms"] += result.latency_ms
            self._metrics["terminology_corrections"] += result.terminology_corrections
            self._metrics["number_date_protection_count"] += result.protected_value_count
            await self._notify(key, completed)
            return

    async def _notify(self, key: str, snapshot: TranslationQualitySnapshot) -> None:
        listener = self._listeners.get(key)
        if listener is not None:
            try:
                await listener(snapshot)
            except Exception:
                return

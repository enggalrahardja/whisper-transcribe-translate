"""Runtime-only semantic state for PCM + VAD live transcription results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import RLock


class TranscriptState(str, Enum):
    PARTIAL = "partial"
    STABLE = "stable"
    FINAL = "final"


@dataclass(frozen=True)
class LiveTranscriptUpdate:
    session_id: str
    segment_id: str
    revision: int
    state: TranscriptState
    sequence_start: int
    sequence_end: int
    start_ms: float
    end_ms: float
    text: str
    language: str
    model: str
    latency_ms: float

    def validate(self) -> None:
        if not self.session_id or not self.segment_id:
            raise ValueError("sessionId and segmentId are required")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        if self.sequence_start < 0 or self.sequence_end < self.sequence_start:
            raise ValueError("invalid sequence range")
        if self.start_ms < 0 or self.end_ms < self.start_ms:
            raise ValueError("invalid transcript time range")
        if self.latency_ms < 0:
            raise ValueError("latencyMs cannot be negative")
        if not self.language or not self.model:
            raise ValueError("language and model are required")

    def as_dict(self) -> dict[str, object]:
        return {
            "sessionId": self.session_id,
            "segmentId": self.segment_id,
            "revision": self.revision,
            "state": self.state.value,
            "sequenceStart": self.sequence_start,
            "sequenceEnd": self.sequence_end,
            "startMs": round(self.start_ms, 3),
            "endMs": round(self.end_ms, 3),
            "text": self.text,
            "language": self.language,
            "model": self.model,
            "latencyMs": round(self.latency_ms, 3),
        }


@dataclass(frozen=True)
class UpdateOutcome:
    accepted: bool
    reason: str
    update: LiveTranscriptUpdate


@dataclass
class _Metrics:
    discarded_duplicate: int = 0
    rejected_out_of_order: int = 0
    finalized_segments: int = 0
    latency_totals: dict[TranscriptState, float] = field(
        default_factory=lambda: {state: 0.0 for state in TranscriptState}
    )
    latency_counts: dict[TranscriptState, int] = field(
        default_factory=lambda: {state: 0 for state in TranscriptState}
    )


class LiveTranscriptStateRegistry:
    """Bounded in-process state; no production persistence schema is changed."""

    def __init__(self, *, max_sessions: int = 128) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        self._max_sessions = max_sessions
        self._sessions: dict[str, dict[str, LiveTranscriptUpdate]] = {}
        self._metrics: dict[str, _Metrics] = {}
        self._lock = RLock()

    def apply(
        self,
        update: LiveTranscriptUpdate,
        *,
        rollback_reason: str | None = None,
    ) -> UpdateOutcome:
        update.validate()
        with self._lock:
            segments = self._get_or_create_session(update.session_id)
            metrics = self._metrics[update.session_id]
            current = segments.get(update.segment_id)

            if current is not None and update == current:
                metrics.discarded_duplicate += 1
                return UpdateOutcome(False, "duplicate", current)

            expected_revision = 1 if current is None else current.revision + 1
            if update.revision != expected_revision:
                metrics.rejected_out_of_order += 1
                return UpdateOutcome(False, "out_of_order", current or update)

            if current is not None and current.state is TranscriptState.FINAL:
                metrics.rejected_out_of_order += 1
                return UpdateOutcome(False, "final_immutable", current)

            if current is not None:
                state_order = {
                    TranscriptState.PARTIAL: 0,
                    TranscriptState.STABLE: 1,
                    TranscriptState.FINAL: 2,
                }
                if state_order[update.state] < state_order[current.state]:
                    metrics.rejected_out_of_order += 1
                    return UpdateOutcome(False, "state_regression", current)
                stable_change = current.state is TranscriptState.STABLE and update.state in {
                    TranscriptState.STABLE,
                    TranscriptState.FINAL,
                }
                if stable_change and not update.text.startswith(current.text) and not rollback_reason:
                    metrics.rejected_out_of_order += 1
                    return UpdateOutcome(False, "stable_rollback_requires_reason", current)

            segments[update.segment_id] = update
            metrics.latency_totals[update.state] += update.latency_ms
            metrics.latency_counts[update.state] += 1
            if update.state is TranscriptState.FINAL:
                metrics.finalized_segments += 1
            return UpdateOutcome(True, "accepted", update)

    def snapshot(self, session_id: str) -> list[LiveTranscriptUpdate]:
        with self._lock:
            segments = self._sessions.get(session_id, {})
            return sorted(
                segments.values(),
                key=lambda item: (item.sequence_start, item.sequence_end, item.segment_id),
            )

    def metrics(self, session_id: str) -> dict[str, object]:
        with self._lock:
            metrics = self._metrics.get(session_id, _Metrics())
            revisions = {
                update.segment_id: update.revision
                for update in self._sessions.get(session_id, {}).values()
            }

            def average(state: TranscriptState) -> float:
                count = metrics.latency_counts[state]
                return metrics.latency_totals[state] / count if count else 0.0

            return {
                "partial_latency_ms": round(average(TranscriptState.PARTIAL), 3),
                "stable_latency_ms": round(average(TranscriptState.STABLE), 3),
                "final_latency_ms": round(average(TranscriptState.FINAL), 3),
                "revisions_per_segment": revisions,
                "discarded_duplicate": metrics.discarded_duplicate,
                "rejected_out_of_order": metrics.rejected_out_of_order,
                "finalized_segments": metrics.finalized_segments,
            }

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._metrics.pop(session_id, None)

    def _get_or_create_session(self, session_id: str) -> dict[str, LiveTranscriptUpdate]:
        segments = self._sessions.get(session_id)
        if segments is not None:
            return segments
        if len(self._sessions) >= self._max_sessions:
            raise RuntimeError("Live transcript runtime session limit reached")
        segments = {}
        self._sessions[session_id] = segments
        self._metrics[session_id] = _Metrics()
        return segments

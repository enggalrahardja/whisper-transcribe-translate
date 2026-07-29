"""Bounded, in-memory PCM16 ingestion state for live sessions."""

from __future__ import annotations

import threading
import re
from math import isfinite
from collections import deque
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

PCM_SAMPLE_RATE = 16_000
PCM_CHANNEL_COUNT = 1
PCM_SAMPLE_WIDTH_BYTES = 2
MIN_CHUNK_DURATION_MS = 100.0
MAX_CHUNK_DURATION_MS = 250.0


class PcmProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class PcmChunkMetadata:
    session_id: str
    sequence: int
    capture_timestamp_ms: float
    sample_rate: int
    channel_count: int
    chunk_duration_ms: float
    byte_length: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PcmChunkMetadata":
        required = {"sessionId", "sequence", "captureTimestampMs", "sampleRate", "channelCount", "chunkDurationMs", "byteLength"}
        allowed = required | {"type"}
        if set(payload) - allowed or not required.issubset(payload):
            raise PcmProtocolError("PCM chunk metadata has missing or unsupported fields")
        if not isinstance(payload["sessionId"], str) or not re.fullmatch(r"[a-f0-9]{32}", payload["sessionId"]):
            raise PcmProtocolError("sessionId must be a 32-character lowercase hexadecimal identifier")
        integer_fields = ("sequence", "sampleRate", "channelCount", "byteLength")
        if any(not isinstance(payload[name], int) or isinstance(payload[name], bool) for name in integer_fields):
            raise PcmProtocolError("PCM integer metadata fields must use exact integers")
        numeric_fields = ("captureTimestampMs", "chunkDurationMs")
        if any(not isinstance(payload[name], (int, float)) or isinstance(payload[name], bool) for name in numeric_fields):
            raise PcmProtocolError("PCM timestamp and duration must be numeric")
        try:
            metadata = cls(
                session_id=str(payload["sessionId"]),
                sequence=int(payload["sequence"]),
                capture_timestamp_ms=float(payload["captureTimestampMs"]),
                sample_rate=int(payload["sampleRate"]),
                channel_count=int(payload["channelCount"]),
                chunk_duration_ms=float(payload["chunkDurationMs"]),
                byte_length=int(payload["byteLength"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PcmProtocolError(f"Invalid PCM chunk metadata: {exc}") from exc
        metadata.validate()
        return metadata

    def validate(self) -> None:
        if not self.session_id:
            raise PcmProtocolError("sessionId is required")
        if self.sequence < 0:
            raise PcmProtocolError("sequence must be non-negative")
        if not isfinite(self.capture_timestamp_ms) or self.capture_timestamp_ms < 0:
            raise PcmProtocolError("captureTimestampMs must be non-negative")
        if self.sample_rate != PCM_SAMPLE_RATE:
            raise PcmProtocolError(f"sampleRate must be {PCM_SAMPLE_RATE}")
        if self.channel_count != PCM_CHANNEL_COUNT:
            raise PcmProtocolError("channelCount must be 1")
        if not isfinite(self.chunk_duration_ms) or not MIN_CHUNK_DURATION_MS <= self.chunk_duration_ms <= MAX_CHUNK_DURATION_MS:
            raise PcmProtocolError(
                f"chunkDurationMs must be between {MIN_CHUNK_DURATION_MS:g} and {MAX_CHUNK_DURATION_MS:g}"
            )
        expected_bytes = round(
            self.sample_rate
            * self.channel_count
            * PCM_SAMPLE_WIDTH_BYTES
            * self.chunk_duration_ms
            / 1000
        )
        if self.byte_length <= 0 or self.byte_length % PCM_SAMPLE_WIDTH_BYTES or self.byte_length != expected_bytes:
            raise PcmProtocolError(
                f"byteLength {self.byte_length} does not match {self.chunk_duration_ms:g} ms PCM16 audio"
            )


@dataclass(frozen=True)
class PcmAudioWindow:
    audio: bytes
    start_sequence: int
    end_sequence: int
    duration_ms: float


@dataclass(frozen=True)
class PcmIngestOutcome:
    sequence: int
    status: str
    expected_sequence: int
    missing_sequences: tuple[int, ...]
    metrics: dict[str, int | float]
    reason: str | None = None

    def acknowledgement(self) -> dict[str, Any]:
        return {
            "transport": "pcm16",
            "sequence": self.sequence,
            "status": self.status,
            "expectedSequence": self.expected_sequence,
            "missingSequences": list(self.missing_sequences),
            "metrics": self.metrics,
            "message": self.reason,
        }


@dataclass
class _BufferedChunk:
    sequence: int
    audio: bytes
    duration_ms: float


@dataclass
class _SessionState:
    expected_sequence: int = 0
    pending: dict[int, _BufferedChunk] = field(default_factory=dict)
    ready: deque[_BufferedChunk] = field(default_factory=deque)
    missing_sequences: set[int] = field(default_factory=set)
    buffered_bytes: int = 0
    buffered_duration_ms: float = 0.0
    chunks_sent: int = 0
    chunks_acknowledged: int = 0
    chunks_lost: int = 0
    duplicate_chunks: int = 0
    out_of_order_chunks: int = 0
    reconnect_count: int = 0
    connection_count: int = 0
    audio_duration_received_seconds: float = 0.0
    backpressure_rejections: int = 0
    last_activity: float = field(default_factory=monotonic)

    def metrics(self) -> dict[str, int | float]:
        return {
            "chunks_sent": self.chunks_sent,
            "chunks_acknowledged": self.chunks_acknowledged,
            "chunks_lost": self.chunks_lost,
            "duplicate_chunks": self.duplicate_chunks,
            "out_of_order_chunks": self.out_of_order_chunks,
            "reconnect_count": self.reconnect_count,
            "audio_duration_received_seconds": round(self.audio_duration_received_seconds, 6),
            "buffer_depth_chunks": len(self.ready) + len(self.pending),
            "buffer_depth_bytes": self.buffered_bytes,
            "buffer_depth_ms": round(self.buffered_duration_ms, 3),
            "backpressure_rejections": self.backpressure_rejections,
        }


class PcmIngestionRegistry:
    """Thread-safe runtime registry. State is isolated and bounded per session."""

    def __init__(
        self,
        *,
        max_buffer_seconds: float = 10.0,
        max_sessions: int = 128,
        max_sequence_gap: int = 128,
        idle_session_seconds: float = 1800.0,
    ) -> None:
        if max_buffer_seconds <= 0 or max_sessions <= 0 or max_sequence_gap <= 0:
            raise ValueError("PCM registry limits must be positive")
        self.max_buffer_bytes = round(
            max_buffer_seconds * PCM_SAMPLE_RATE * PCM_CHANNEL_COUNT * PCM_SAMPLE_WIDTH_BYTES
        )
        self.max_sessions = max_sessions
        self.max_sequence_gap = max_sequence_gap
        self.idle_session_seconds = idle_session_seconds
        self._sessions: dict[str, _SessionState] = {}
        self._lock = threading.RLock()

    def _prune_idle(self) -> None:
        cutoff = monotonic() - self.idle_session_seconds
        expired = [
            session_id
            for session_id, state in self._sessions.items()
            if state.last_activity < cutoff
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)

    def _state(self, session_id: str) -> _SessionState:
        state = self._sessions.get(session_id)
        if state is not None:
            return state
        self._prune_idle()
        if len(self._sessions) >= self.max_sessions:
            raise PcmProtocolError("PCM session capacity reached")
        state = _SessionState()
        self._sessions[session_id] = state
        return state

    def register_connection(self, session_id: str) -> dict[str, int | float]:
        with self._lock:
            state = self._state(session_id)
            if state.connection_count:
                state.reconnect_count += 1
            state.connection_count += 1
            state.last_activity = monotonic()
            return state.metrics()

    def ingest(
        self,
        session_id: str,
        metadata: PcmChunkMetadata,
        audio: bytes,
    ) -> PcmIngestOutcome:
        metadata.validate()
        if metadata.session_id != session_id:
            raise PcmProtocolError("PCM metadata sessionId does not match WebSocket session")
        if len(audio) != metadata.byte_length:
            raise PcmProtocolError("PCM binary frame length does not match byteLength")

        with self._lock:
            state = self._state(session_id)
            state.last_activity = monotonic()
            state.chunks_sent += 1

            if metadata.sequence < state.expected_sequence or metadata.sequence in state.pending:
                state.duplicate_chunks += 1
                state.chunks_acknowledged += 1
                return self._outcome(state, metadata.sequence, "duplicate")

            if metadata.sequence - state.expected_sequence > self.max_sequence_gap:
                state.backpressure_rejections += 1
                state.chunks_acknowledged += 1
                return self._outcome(
                    state,
                    metadata.sequence,
                    "backpressure",
                    reason="sequence gap exceeds the configured limit",
                )

            if state.buffered_bytes + len(audio) > self.max_buffer_bytes:
                state.backpressure_rejections += 1
                state.chunks_acknowledged += 1
                return self._outcome(
                    state,
                    metadata.sequence,
                    "backpressure",
                    reason="session PCM buffer is full",
                )

            chunk = _BufferedChunk(metadata.sequence, bytes(audio), metadata.chunk_duration_ms)
            state.pending[metadata.sequence] = chunk
            state.buffered_bytes += len(audio)
            state.buffered_duration_ms += metadata.chunk_duration_ms
            state.audio_duration_received_seconds += metadata.chunk_duration_ms / 1000

            status = "accepted"
            if metadata.sequence > state.expected_sequence:
                status = "out_of_order"
                state.out_of_order_chunks += 1
                for missing in range(state.expected_sequence, metadata.sequence):
                    if missing not in state.pending:
                        if missing not in state.missing_sequences:
                            state.chunks_lost += 1
                        state.missing_sequences.add(missing)

            while state.expected_sequence in state.pending:
                contiguous = state.pending.pop(state.expected_sequence)
                state.missing_sequences.discard(state.expected_sequence)
                state.ready.append(contiguous)
                state.expected_sequence += 1

            state.chunks_acknowledged += 1
            return self._outcome(state, metadata.sequence, status)

    def _outcome(
        self,
        state: _SessionState,
        sequence: int,
        status: str,
        *,
        reason: str | None = None,
    ) -> PcmIngestOutcome:
        return PcmIngestOutcome(
            sequence=sequence,
            status=status,
            expected_sequence=state.expected_sequence,
            missing_sequences=tuple(sorted(state.missing_sequences)),
            metrics=state.metrics(),
            reason=reason,
        )

    def take_audio(
        self,
        session_id: str,
        *,
        target_duration_ms: float,
        flush: bool = False,
    ) -> PcmAudioWindow | None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or not state.ready:
                return None
            ready_duration = sum(chunk.duration_ms for chunk in state.ready)
            if not flush and ready_duration < target_duration_ms:
                return None

            chunks: list[_BufferedChunk] = []
            duration_ms = 0.0
            while state.ready and (flush or duration_ms < target_duration_ms):
                chunk = state.ready.popleft()
                chunks.append(chunk)
                duration_ms += chunk.duration_ms
                state.buffered_bytes -= len(chunk.audio)
                state.buffered_duration_ms -= chunk.duration_ms
            state.last_activity = monotonic()
            return PcmAudioWindow(
                audio=b"".join(chunk.audio for chunk in chunks),
                start_sequence=chunks[0].sequence,
                end_sequence=chunks[-1].sequence,
                duration_ms=duration_ms,
            )

    def metrics(self, session_id: str) -> dict[str, int | float]:
        with self._lock:
            state = self._sessions.get(session_id)
            return state.metrics() if state is not None else _SessionState().metrics()

    def expected_sequence(self, session_id: str) -> int:
        with self._lock:
            state = self._sessions.get(session_id)
            return state.expected_sequence if state is not None else 0

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

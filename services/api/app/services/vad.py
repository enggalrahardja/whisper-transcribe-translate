"""Local WebRTC VAD detection, speech buffering, and segment finalization."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from time import perf_counter
from typing import Callable, Protocol

from .pcm_ingestion import PCM_SAMPLE_RATE, PCM_SAMPLE_WIDTH_BYTES, PcmAudioWindow

VAD_FRAME_DURATION_MS = 10
VAD_FRAME_BYTES = PCM_SAMPLE_RATE * PCM_SAMPLE_WIDTH_BYTES * VAD_FRAME_DURATION_MS // 1000
DECISION_WINDOW_MS = 100


class VadState(str, Enum):
    IDLE = "idle"
    SPEECH_STARTED = "speech_started"
    SPEECH_ACTIVE = "speech_active"
    SPEECH_ENDED = "speech_ended"


class SpeechDetector(Protocol):
    def is_speech(self, frame: bytes, sample_rate: int) -> bool: ...


class WebRtcSpeechDetector:
    def __init__(self, mode: int = 2) -> None:
        if mode not in {0, 1, 2, 3}:
            raise ValueError("WebRTC VAD mode must be between 0 and 3")
        import webrtcvad

        self._vad = webrtcvad.Vad(mode)

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        return bool(self._vad.is_speech(frame, sample_rate))


@dataclass(frozen=True)
class VadConfig:
    speech_threshold: float = 0.6
    silence_duration_ms: int = 600
    pre_speech_duration_ms: int = 300
    minimum_speech_duration_ms: int = 250
    maximum_segment_duration_ms: int = 20_000
    segment_overlap_ms: int = 500

    def validate(self) -> None:
        if not 0 < self.speech_threshold <= 1:
            raise ValueError("speech threshold must be greater than 0 and at most 1")
        durations = {
            "silence": self.silence_duration_ms,
            "pre-speech": self.pre_speech_duration_ms,
            "minimum speech": self.minimum_speech_duration_ms,
            "maximum segment": self.maximum_segment_duration_ms,
            "segment overlap": self.segment_overlap_ms,
        }
        if any(value < 0 for value in durations.values()):
            raise ValueError("VAD durations must be non-negative")
        if self.silence_duration_ms == 0 or self.minimum_speech_duration_ms == 0:
            raise ValueError("silence and minimum speech durations must be positive")
        if self.maximum_segment_duration_ms <= self.minimum_speech_duration_ms:
            raise ValueError("maximum segment duration must exceed minimum speech duration")
        if self.segment_overlap_ms >= self.maximum_segment_duration_ms:
            raise ValueError("segment overlap must be shorter than maximum segment duration")


@dataclass(frozen=True)
class _Frame:
    audio: bytes
    start_sequence: int
    end_sequence: int
    raw_speech: bool


@dataclass(frozen=True)
class SpeechSegment:
    window: PcmAudioWindow
    reason: str
    forced: bool


@dataclass
class VadMetrics:
    speech_segments: int = 0
    rejected_short_segments: int = 0
    silence_duration_skipped_ms: float = 0.0
    speech_duration_processed_ms: float = 0.0
    forced_segment_finalization: int = 0
    total_segment_duration_ms: float = 0.0
    vad_processing_latency_ms: float = 0.0
    vad_frames_processed: int = 0

    def as_dict(self) -> dict[str, int | float]:
        average = (
            self.total_segment_duration_ms / self.speech_segments
            if self.speech_segments
            else 0.0
        )
        latency = (
            self.vad_processing_latency_ms / self.vad_frames_processed
            if self.vad_frames_processed
            else 0.0
        )
        return {
            "speech_segments": self.speech_segments,
            "rejected_short_segments": self.rejected_short_segments,
            "silence_duration_skipped_ms": round(self.silence_duration_skipped_ms, 3),
            "speech_duration_processed_ms": round(self.speech_duration_processed_ms, 3),
            "forced_segment_finalization": self.forced_segment_finalization,
            "average_segment_duration_ms": round(average, 3),
            "vad_processing_latency_ms": round(latency, 6),
        }


@dataclass
class VadProcessResult:
    state: VadState
    segments: list[SpeechSegment]
    metrics: dict[str, int | float]


class VadSession:
    """Per-session state machine. Input and output remain PCM16 mono 16 kHz."""

    def __init__(self, config: VadConfig, detector: SpeechDetector) -> None:
        config.validate()
        self.config = config
        self.detector = detector
        self.state = VadState.IDLE
        self.metrics = VadMetrics()
        self._raw_parts: deque[tuple[int, bytearray]] = deque()
        self._raw_bytes = 0
        self._decisions: deque[bool] = deque(
            maxlen=max(1, DECISION_WINDOW_MS // VAD_FRAME_DURATION_MS)
        )
        self._pre_speech: deque[_Frame] = deque()
        self._segment: list[_Frame] = []
        self._trailing_silence: list[_Frame] = []
        self._speech_duration_ms = 0.0

    def process(self, window: PcmAudioWindow) -> VadProcessResult:
        if window.audio:
            self._raw_parts.append((window.start_sequence, bytearray(window.audio)))
            self._raw_bytes += len(window.audio)
        segments: list[SpeechSegment] = []
        while self._raw_bytes >= VAD_FRAME_BYTES:
            frame_audio, start_sequence, end_sequence = self._pop_frame()
            started = perf_counter()
            raw_speech = self.detector.is_speech(frame_audio, PCM_SAMPLE_RATE)
            self.metrics.vad_processing_latency_ms += (perf_counter() - started) * 1000
            self.metrics.vad_frames_processed += 1
            self._decisions.append(raw_speech)
            detected = sum(self._decisions) / len(self._decisions) >= self.config.speech_threshold
            frame = _Frame(frame_audio, start_sequence, end_sequence, raw_speech)
            segments.extend(self._consume_frame(frame, detected))
        return VadProcessResult(self.state, segments, self.metrics.as_dict())

    def flush(self) -> VadProcessResult:
        segments: list[SpeechSegment] = []
        if self.state in {VadState.SPEECH_STARTED, VadState.SPEECH_ACTIVE}:
            self.metrics.silence_duration_skipped_ms += self._duration(self._trailing_silence)
            self._trailing_silence.clear()
            segment = self._finalize("session_stop", forced=False)
            if segment is not None:
                segments.append(segment)
        else:
            self.metrics.silence_duration_skipped_ms += self._duration(self._pre_speech)
            self._pre_speech.clear()
        if self._raw_bytes:
            self.metrics.silence_duration_skipped_ms += (
                self._raw_bytes / (PCM_SAMPLE_RATE * PCM_SAMPLE_WIDTH_BYTES) * 1000
            )
            self._raw_parts.clear()
            self._raw_bytes = 0
        return VadProcessResult(self.state, segments, self.metrics.as_dict())

    def _pop_frame(self) -> tuple[bytes, int, int]:
        remaining = VAD_FRAME_BYTES
        blocks: list[bytes] = []
        start_sequence = self._raw_parts[0][0]
        end_sequence = start_sequence
        while remaining:
            sequence, part = self._raw_parts[0]
            take = min(remaining, len(part))
            blocks.append(bytes(part[:take]))
            del part[:take]
            remaining -= take
            self._raw_bytes -= take
            end_sequence = sequence
            if not part:
                self._raw_parts.popleft()
        return b"".join(blocks), start_sequence, end_sequence

    def _consume_frame(self, frame: _Frame, detected: bool) -> list[SpeechSegment]:
        if self.state == VadState.SPEECH_ENDED:
            self.state = VadState.IDLE

        if self.state == VadState.IDLE:
            if not detected:
                self._append_pre_speech(frame)
                return []
            self.state = VadState.SPEECH_STARTED
            self._segment = [*self._pre_speech, frame]
            self._speech_duration_ms = sum(
                VAD_FRAME_DURATION_MS for item in self._segment if item.raw_speech
            )
            self._pre_speech.clear()
            return self._force_if_needed()

        if detected:
            if self._trailing_silence:
                self._segment.extend(self._trailing_silence)
                self._trailing_silence.clear()
            self._segment.append(frame)
            if frame.raw_speech:
                self._speech_duration_ms += VAD_FRAME_DURATION_MS
            if self.state == VadState.SPEECH_STARTED:
                self.state = VadState.SPEECH_ACTIVE
            return self._force_if_needed()

        self._trailing_silence.append(frame)
        if self._duration(self._trailing_silence) < self.config.silence_duration_ms:
            return self._force_if_needed()

        self.metrics.silence_duration_skipped_ms += self._duration(self._trailing_silence)
        self._trailing_silence.clear()
        segment = self._finalize("silence", forced=False)
        self._decisions.clear()
        return [segment] if segment is not None else []

    def _append_pre_speech(self, frame: _Frame) -> None:
        self._pre_speech.append(frame)
        while self._duration(self._pre_speech) > self.config.pre_speech_duration_ms:
            self._pre_speech.popleft()
            self.metrics.silence_duration_skipped_ms += VAD_FRAME_DURATION_MS

    def _force_if_needed(self) -> list[SpeechSegment]:
        if self._duration(self._segment) + self._duration(self._trailing_silence) < self.config.maximum_segment_duration_ms:
            return []
        self.metrics.silence_duration_skipped_ms += self._duration(self._trailing_silence)
        self._trailing_silence.clear()
        segment = self._finalize("maximum_duration", forced=True)
        if segment is None:
            return []
        overlap_frames = self.config.segment_overlap_ms // VAD_FRAME_DURATION_MS
        self._segment = self._segment_tail(segment.window.audio, segment.window.end_sequence, overlap_frames)
        self._speech_duration_ms = self._duration(self._segment)
        self.state = VadState.SPEECH_STARTED
        return [segment]

    def _segment_tail(self, audio: bytes, sequence: int, frame_count: int) -> list[_Frame]:
        if frame_count <= 0:
            return []
        tail = audio[-frame_count * VAD_FRAME_BYTES:]
        return [
            _Frame(
                tail[offset:offset + VAD_FRAME_BYTES],
                sequence,
                sequence,
                True,
            )
            for offset in range(0, len(tail), VAD_FRAME_BYTES)
        ]

    def _finalize(self, reason: str, *, forced: bool) -> SpeechSegment | None:
        frames = self._segment
        duration_ms = self._duration(frames)
        speech_duration_ms = self._speech_duration_ms
        self._segment = []
        self._speech_duration_ms = 0.0
        self.state = VadState.SPEECH_ENDED

        if speech_duration_ms < self.config.minimum_speech_duration_ms or not frames:
            self.metrics.rejected_short_segments += 1
            self.metrics.silence_duration_skipped_ms += duration_ms
            return None

        audio = b"".join(frame.audio for frame in frames)
        window = PcmAudioWindow(
            audio=audio,
            start_sequence=frames[0].start_sequence,
            end_sequence=frames[-1].end_sequence,
            duration_ms=duration_ms,
        )
        self.metrics.speech_segments += 1
        self.metrics.speech_duration_processed_ms += duration_ms
        self.metrics.total_segment_duration_ms += duration_ms
        if forced:
            self.metrics.forced_segment_finalization += 1
        return SpeechSegment(window=window, reason=reason, forced=forced)

    @staticmethod
    def _duration(frames) -> float:
        return len(frames) * VAD_FRAME_DURATION_MS


class VadSessionRegistry:
    def __init__(
        self,
        config: VadConfig,
        detector_factory: Callable[[], SpeechDetector],
        *,
        max_sessions: int = 128,
    ) -> None:
        self.config = config
        self.config.validate()
        self.detector_factory = detector_factory
        self.max_sessions = max_sessions
        self._sessions: dict[str, VadSession] = {}

    def session(self, session_id: str) -> VadSession:
        session = self._sessions.get(session_id)
        if session is not None:
            return session
        if len(self._sessions) >= self.max_sessions:
            raise RuntimeError("VAD session capacity reached")
        session = VadSession(self.config, self.detector_factory())
        self._sessions[session_id] = session
        return session

    def process(self, session_id: str, window: PcmAudioWindow) -> VadProcessResult:
        return self.session(session_id).process(window)

    def flush(self, session_id: str) -> VadProcessResult:
        session = self._sessions.get(session_id)
        if session is None:
            return VadProcessResult(VadState.IDLE, [], VadMetrics().as_dict())
        return session.flush()

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

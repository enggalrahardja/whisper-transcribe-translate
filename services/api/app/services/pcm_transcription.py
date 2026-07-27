"""Bridge from normalized PCM windows to the unchanged local transcription path."""

from __future__ import annotations

import hashlib
import wave
from dataclasses import dataclass
from io import BytesIO
from time import perf_counter

from ..models.live import LiveSessionResponse
from .live_processor import process_live_chunk
from .pcm_ingestion import PCM_CHANNEL_COUNT, PCM_SAMPLE_RATE, PCM_SAMPLE_WIDTH_BYTES, PcmAudioWindow


@dataclass(frozen=True)
class PcmTranscriptionResult:
    session: LiveSessionResponse
    duplicate: bool
    segment_id: str
    sequence_start: int
    sequence_end: int
    text: str
    start_ms: float
    end_ms: float
    latency_ms: float


def pcm_window_to_wav(window: PcmAudioWindow) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(PCM_CHANNEL_COUNT)
        wav_file.setsampwidth(PCM_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(PCM_SAMPLE_RATE)
        wav_file.writeframes(window.audio)
    return output.getvalue()


def transcribe_pcm_window(session_id: str, window: PcmAudioWindow):
    identity = f"pcm16:{session_id}:{window.start_sequence}:{window.end_sequence}"
    return process_live_chunk(
        session_id,
        pcm_window_to_wav(window),
        chunk_identity=identity,
    )


def transcribe_pcm_window_detailed(
    session_id: str,
    window: PcmAudioWindow,
) -> PcmTranscriptionResult:
    identity = f"pcm16:{session_id}:{window.start_sequence}:{window.end_sequence}"
    started = perf_counter()
    session, duplicate = process_live_chunk(
        session_id,
        pcm_window_to_wav(window),
        chunk_identity=identity,
    )
    latency_ms = (perf_counter() - started) * 1000
    prefix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    matching = [
        segment
        for segment in session.segments
        if str(segment.get("id", "")).startswith(f"{prefix}:")
    ]
    fallback_end_ms = session.duration * 1000
    start_ms = min(
        (float(segment.get("start", 0)) * 1000 for segment in matching),
        default=max(0.0, fallback_end_ms - window.duration_ms),
    )
    end_ms = max(
        (float(segment.get("end", 0)) * 1000 for segment in matching),
        default=fallback_end_ms,
    )
    return PcmTranscriptionResult(
        session=session,
        duplicate=duplicate,
        segment_id=f"pcm-{window.start_sequence}-{window.end_sequence}",
        sequence_start=window.start_sequence,
        sequence_end=window.end_sequence,
        text=" ".join(
            str(segment.get("text", "")).strip()
            for segment in matching
            if str(segment.get("text", "")).strip()
        ),
        start_ms=start_ms,
        end_ms=end_ms,
        latency_ms=latency_ms,
    )

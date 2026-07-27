"""Bridge from normalized PCM windows to the unchanged local transcription path."""

from __future__ import annotations

import wave
from io import BytesIO

from .live_processor import process_live_chunk
from .pcm_ingestion import PCM_CHANNEL_COUNT, PCM_SAMPLE_RATE, PCM_SAMPLE_WIDTH_BYTES, PcmAudioWindow


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

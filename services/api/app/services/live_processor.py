import hashlib
import re
import tempfile
import threading
import wave
from io import BytesIO
from pathlib import Path

from ..models.live import LiveSessionResponse
from .application_settings import get_application_settings
from .live_sessions import append_live_result, claim_live_chunk
from .whisper_adapter import WhisperAdapter

_adapter = WhisperAdapter()
_adapter_lock = threading.Lock()


def _normalized_word(word: str) -> str:
    return re.sub(r"[^\w']", "", word, flags=re.UNICODE).casefold()


def merge_transcript(existing: str, addition: str) -> str:
    existing = existing.strip()
    addition = addition.strip()
    if not addition:
        return existing
    if not existing:
        return addition

    existing_words = existing.split()
    addition_words = addition.split()
    normalized_existing = [_normalized_word(word) for word in existing_words]
    normalized_addition = [_normalized_word(word) for word in addition_words]
    overlap = 0
    for size in range(min(24, len(existing_words), len(addition_words)), 0, -1):
        if normalized_existing[-size:] == normalized_addition[:size]:
            overlap = size
            break
    remainder = " ".join(addition_words[overlap:]).strip()
    return existing if not remainder else f"{existing} {remainder}"


def _audio_suffix(audio: bytes) -> str:
    if audio.startswith(b"RIFF"):
        return ".wav"
    if audio.startswith(b"OggS"):
        return ".ogg"
    if audio.startswith(b"\x1aE\xdf\xa3"):
        return ".webm"
    return ".audio"


def _wav_duration(audio: bytes) -> float | None:
    if not audio.startswith(b"RIFF"):
        return None
    try:
        with wave.open(BytesIO(audio), "rb") as wav_file:
            return wav_file.getnframes() / wav_file.getframerate()
    except (EOFError, wave.Error, ZeroDivisionError):
        return None


def process_live_chunk(session_id: str, audio: bytes) -> tuple[LiveSessionResponse, bool]:
    if not audio:
        raise ValueError("Audio chunk is empty")
    chunk_hash = hashlib.sha256(audio).hexdigest()
    document, claimed = claim_live_chunk(session_id, chunk_hash)
    if not claimed:
        return LiveSessionResponse(
            session_id=document["session_id"],
            status=document["status"],
            language=document["language"],
            model=document["model"],
            started_at=document["started_at"],
            ended_at=document.get("ended_at"),
            duration=float(document.get("duration", 0)),
            partial_text=document.get("partial_text", ""),
            final_text=document.get("final_text", ""),
            segments=document.get("segments", []),
            error=document.get("error"),
            created_at=document["created_at"],
            updated_at=document["updated_at"],
        ), True

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="whisper-live-", suffix=_audio_suffix(audio), delete=False) as audio_file:
            audio_file.write(audio)
            temporary_path = Path(audio_file.name)
        with _adapter_lock:
            transcription_settings = get_application_settings().transcription
            result = _adapter.transcribe(
                temporary_path,
                model_name=document["model"],
                language=document["language"],
                fp16=transcription_settings.fp16,
                beam_size=transcription_settings.beam_size,
                temperature=transcription_settings.temperature,
                initial_prompt=transcription_settings.initial_prompt,
                word_timestamps=transcription_settings.word_timestamps,
            )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    chunk_text = str(result.get("text", "")).strip()
    merged_text = merge_transcript(document.get("partial_text", ""), chunk_text)
    cursor = float(document.get("audio_cursor", 0))
    has_overlap = len(document.get("processed_chunk_hashes", [])) > 1
    overlap = get_application_settings().live_transcription.overlap_duration_seconds if has_overlap else 0.0
    segment_cursor = max(0.0, cursor - overlap)
    raw_segments = result.get("segments", [])
    segments = [
        {
            "id": f"{chunk_hash[:12]}:{index}",
            "start": segment_cursor + float(segment.get("start", 0)),
            "end": segment_cursor + float(segment.get("end", 0)),
            "text": str(segment.get("text", "")).strip(),
        }
        for index, segment in enumerate(raw_segments)
        if str(segment.get("text", "")).strip()
    ]
    inferred_duration = max((float(segment.get("end", 0)) for segment in raw_segments), default=0.0)
    chunk_duration = _wav_duration(audio) or inferred_duration
    if has_overlap:
        chunk_duration = max(0.0, chunk_duration - overlap)
    session = append_live_result(session_id, chunk_hash, merged_text, segments, chunk_duration)
    return session, False

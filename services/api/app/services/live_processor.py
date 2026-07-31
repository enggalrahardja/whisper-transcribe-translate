import hashlib
import re
import tempfile
import threading
import wave
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from ..models.live import LiveSessionResponse
from .application_settings import get_application_settings
from .glossary import (
    DisabledGlossarySnapshot,
    GlossaryCorrection,
    GlossarySnapshot,
    combine_prompt,
)
from .live_sessions import append_live_result, claim_live_chunk
from .transcription_backends import TranscriptionBackendManager

_adapter = TranscriptionBackendManager()
_adapter_lock = threading.Lock()


@dataclass(frozen=True)
class LiveChunkDetail:
    raw_text: str
    corrected_text: str
    corrections: tuple[GlossaryCorrection, ...]
    glossary_version: str | None


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


def process_live_chunk(
    session_id: str,
    audio: bytes,
    *,
    chunk_identity: str | None = None,
) -> tuple[LiveSessionResponse, bool]:
    session, duplicate, _ = process_live_chunk_detailed(
        session_id,
        audio,
        chunk_identity=chunk_identity,
    )
    return session, duplicate


def process_live_chunk_detailed(
    session_id: str,
    audio: bytes,
    *,
    chunk_identity: str | None = None,
    glossary: GlossarySnapshot | DisabledGlossarySnapshot | None = None,
) -> tuple[LiveSessionResponse, bool, LiveChunkDetail | None]:
    if not audio:
        raise ValueError("Audio chunk is empty")
    chunk_hash = hashlib.sha256(
        chunk_identity.encode("utf-8") if chunk_identity is not None else audio
    ).hexdigest()
    document, claimed = claim_live_chunk(session_id, chunk_hash)
    if not claimed:
        return LiveSessionResponse(
            session_id=document["session_id"],
            status=document["status"],
            language=document["language"],
            model=document["model"],
            transcription_backend=document.get("transcription_backend", "pytorch"),
            transcription_device=document.get("transcription_device", "auto"),
            transcription_compute_type=document.get("transcription_compute_type", "auto"),
            started_at=document["started_at"],
            ended_at=document.get("ended_at"),
            duration=float(document.get("duration", 0)),
            partial_text=document.get("partial_text", ""),
            final_text=document.get("final_text", ""),
            segments=document.get("segments", []),
            error=document.get("error"),
            created_at=document["created_at"],
            updated_at=document["updated_at"],
        ), True, None

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="whisper-live-", suffix=_audio_suffix(audio), delete=False) as audio_file:
            audio_file.write(audio)
            temporary_path = Path(audio_file.name)
        with _adapter_lock:
            transcription_settings = get_application_settings().transcription
            glossary_context = glossary.prompt_context if glossary is not None else ""
            result = _adapter.transcribe(
                temporary_path,
                backend=document.get("transcription_backend", "pytorch"),
                model_name=document["model"],
                device=document.get("transcription_device", "auto"),
                compute_type=document.get("transcription_compute_type", "auto"),
                language=document["language"],
                beam_size=transcription_settings.beam_size,
                temperature=transcription_settings.temperature,
                initial_prompt=combine_prompt(
                    transcription_settings.initial_prompt,
                    glossary_context,
                ),
                word_timestamps=transcription_settings.word_timestamps,
            )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    raw_chunk_text = str(result.get("text", "")).strip()
    correction = (
        glossary.correct(raw_chunk_text, language=document["language"])
        if glossary is not None
        else None
    )
    chunk_text = correction.corrected_text if correction is not None else raw_chunk_text
    merged_text = merge_transcript(document.get("partial_text", ""), chunk_text)
    cursor = float(document.get("audio_cursor", 0))
    has_overlap = len(document.get("processed_chunk_hashes", [])) > 1
    overlap = get_application_settings().live_transcription.overlap_duration_seconds if has_overlap else 0.0
    segment_cursor = max(0.0, cursor - overlap)
    raw_segments = result.get("segments", [])
    segments = []
    for index, segment in enumerate(raw_segments):
        raw_segment_text = str(segment.get("text", "")).strip()
        if not raw_segment_text:
            continue
        segment_text = (
            glossary.correct(
                raw_segment_text,
                language=document["language"],
                record_metrics=False,
            ).corrected_text
            if glossary is not None
            else raw_segment_text
        )
        segments.append(
            {
                "id": f"{chunk_hash[:12]}:{index}",
                "start": segment_cursor + float(segment.get("start", 0)),
                "end": segment_cursor + float(segment.get("end", 0)),
                "text": segment_text,
            }
        )
    inferred_duration = max((float(segment.get("end", 0)) for segment in raw_segments), default=0.0)
    chunk_duration = _wav_duration(audio) or inferred_duration
    if has_overlap:
        chunk_duration = max(0.0, chunk_duration - overlap)
    session = append_live_result(session_id, chunk_hash, merged_text, segments, chunk_duration)
    detail = LiveChunkDetail(
        raw_text=raw_chunk_text,
        corrected_text=chunk_text,
        corrections=correction.corrections if correction is not None else (),
        glossary_version=correction.glossary_version if correction is not None else None,
    )
    return session, False, detail

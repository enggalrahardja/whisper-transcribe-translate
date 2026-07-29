import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from ..config import get_settings
from ..models.live import CreateLiveSessionRequest, LiveSessionResponse
from ..security import is_allowed_web_origin
from ..services.live_processor import process_live_chunk
from ..services.final_transcription import (
    FinalJobSnapshot,
    FinalJobStatus,
    FinalTranscriptionConfig,
    FinalTranscriptionRequest,
    LocalFinalTranscriptionQueue,
    PersistentLocalFinalTranscriber,
)
from ..services.glossary import (
    DisabledGlossarySnapshot,
    GlossaryManager,
    GlossarySnapshot,
)
from ..services.live_transcript_state import (
    LiveTranscriptStateRegistry,
    LiveTranscriptUpdate,
    TranscriptState,
)
from ..services.live_translation import (
    LiveTranslationConfig,
    LocalLiveTranslationQueue,
    PersistentLocalMarianTranslator,
    TranslationRequest,
    TranslationSnapshot,
    TranslationStatus,
)
from ..services.translation_quality import (
    DeterministicTranslationQualityProcessor,
    LocalTranslationQualityQueue,
    QualityStatus,
    TranslationQualityConfig,
    TranslationQualityRequest,
    TranslationQualitySnapshot,
)
from ..services.speaker_diarization import (
    DiarizationRequest,
    DiarizationSnapshot,
    LocalSpeakerDiarizationQueue,
    PersistentLocalSpeakerEmbedder,
    SpeakerDiarizationConfig,
)
from ..services.transcript_postprocessing import (
    DeterministicTranscriptProcessor,
    LocalTranscriptPostprocessQueue,
    TranscriptPostprocessConfig,
    TranscriptPostprocessRequest,
    TranscriptPostprocessSnapshot,
    TranscriptPostprocessStatus,
)
from ..services.processing_worker import (
    InProcessWorker,
    JobPriority,
    ProcessingJob,
    WorkerBackpressureError,
)
from ..services.live_sessions import (
    create_live_session,
    fail_live_session,
    get_live_session,
    list_live_sessions,
    pause_live_session,
    record_disconnect,
    resume_live_session,
    stop_live_session,
)
from ..services.pcm_ingestion import (
    MAX_CHUNK_DURATION_MS,
    MIN_CHUNK_DURATION_MS,
    PCM_CHANNEL_COUNT,
    PCM_SAMPLE_RATE,
    PcmAudioWindow,
    PcmChunkMetadata,
    PcmIngestionRegistry,
    PcmProtocolError,
)
from ..services.pcm_transcription import (
    PcmTranscriptionResult,
    transcribe_pcm_window,
    transcribe_pcm_window_detailed,
    pcm_window_to_wav,
)
from ..services.vad import (
    VadConfig,
    VadProcessResult,
    VadSessionRegistry,
    WebRtcSpeechDetector,
)

router = APIRouter(tags=["live"])
_connections: dict[str, WebSocket] = {}
_connections_lock = asyncio.Lock()
_pcm_tasks: dict[str, asyncio.Task[None]] = {}
_pcm_task_locks: dict[str, asyncio.Lock] = {}
_pcm_task_registry_lock = asyncio.Lock()
MAX_LIVE_CHUNK_BYTES = 10 * 1024 * 1024
_runtime_settings = get_settings()
_pcm_registry = PcmIngestionRegistry(
    max_buffer_seconds=_runtime_settings.live_pcm_max_buffer_seconds,
    max_sessions=_runtime_settings.live_pcm_max_sessions,
    max_sequence_gap=_runtime_settings.live_pcm_max_sequence_gap,
)
_vad_registry = VadSessionRegistry(
    VadConfig(
        speech_threshold=_runtime_settings.live_vad_speech_threshold,
        silence_duration_ms=_runtime_settings.live_vad_silence_duration_ms,
        pre_speech_duration_ms=_runtime_settings.live_vad_pre_speech_duration_ms,
        minimum_speech_duration_ms=_runtime_settings.live_vad_minimum_speech_duration_ms,
        maximum_segment_duration_ms=_runtime_settings.live_vad_maximum_segment_duration_ms,
        segment_overlap_ms=_runtime_settings.live_vad_segment_overlap_ms,
    ),
    lambda: WebRtcSpeechDetector(_runtime_settings.live_vad_webrtc_mode),
    max_sessions=_runtime_settings.live_pcm_max_sessions,
)
_live_state_registry = LiveTranscriptStateRegistry(
    max_sessions=_runtime_settings.live_pcm_max_sessions,
)
_final_queue: LocalFinalTranscriptionQueue | None = None
_glossary_manager: GlossaryManager | None = None
_translation_queue: LocalLiveTranslationQueue | None = None
_translation_quality_queue: LocalTranslationQualityQueue | None = None
_diarization_queue: LocalSpeakerDiarizationQueue | None = None
_transcript_postprocess_queue: LocalTranscriptPostprocessQueue | None = None
_live_processing_worker: InProcessWorker[tuple, tuple] | None = None
_segment_glossaries: dict[
    tuple[str, str], GlossarySnapshot | DisabledGlossarySnapshot | None
] = {}


def _get_glossary_manager() -> GlossaryManager:
    global _glossary_manager
    if _glossary_manager is None:
        _glossary_manager = GlossaryManager(
            _runtime_settings.live_glossary_path,
            enabled=_runtime_settings.live_glossary_enabled,
            prompt_max_terms=_runtime_settings.live_glossary_prompt_max_terms,
        )
    return _glossary_manager


def _get_final_queue() -> LocalFinalTranscriptionQueue:
    global _final_queue
    if _final_queue is None:
        config = FinalTranscriptionConfig(
            model=_runtime_settings.live_final_model,
            device=_runtime_settings.live_final_device,
            compute_type=_runtime_settings.live_final_compute_type,
            beam_size=_runtime_settings.live_final_beam_size,
            timeout_seconds=_runtime_settings.live_final_timeout_seconds,
            max_retries=_runtime_settings.live_final_max_retries,
            worker_concurrency=_runtime_settings.live_final_worker_concurrency,
            queue_capacity=_runtime_settings.live_final_queue_capacity,
        )
        transcriber = PersistentLocalFinalTranscriber(config)
        _final_queue = LocalFinalTranscriptionQueue(config, transcriber)
    return _final_queue


def _get_translation_queue() -> LocalLiveTranslationQueue:
    global _translation_queue
    if _translation_queue is None:
        config = LiveTranslationConfig(
            model=_runtime_settings.live_translation_model,
            model_revision=_runtime_settings.live_translation_model_revision,
            source_language=_runtime_settings.live_translation_source_language,
            target_language=_runtime_settings.live_translation_target_language,
            device=_runtime_settings.live_translation_device,
            compute_type=_runtime_settings.live_translation_compute_type,
            beam_size=_runtime_settings.live_translation_beam_size,
            timeout_seconds=_runtime_settings.live_translation_timeout_seconds,
            max_retries=_runtime_settings.live_translation_max_retries,
            worker_concurrency=_runtime_settings.live_translation_worker_concurrency,
            queue_capacity=_runtime_settings.live_translation_queue_capacity,
            context_segments=_runtime_settings.live_translation_context_segments,
        )
        _translation_queue = LocalLiveTranslationQueue(
            config, PersistentLocalMarianTranslator(config)
        )
    return _translation_queue


def _get_translation_quality_queue() -> LocalTranslationQualityQueue:
    global _translation_quality_queue
    if _translation_quality_queue is None:
        config = TranslationQualityConfig(
            timeout_seconds=_runtime_settings.live_translation_quality_timeout_seconds,
            max_retries=_runtime_settings.live_translation_quality_max_retries,
            worker_concurrency=_runtime_settings.live_translation_quality_worker_concurrency,
            queue_capacity=_runtime_settings.live_translation_quality_queue_capacity,
        )
        _translation_quality_queue = LocalTranslationQualityQueue(
            config,
            DeterministicTranslationQualityProcessor(),
        )
    return _translation_quality_queue


def _get_diarization_queue() -> LocalSpeakerDiarizationQueue:
    global _diarization_queue
    if _diarization_queue is None:
        config = SpeakerDiarizationConfig(
            model=_runtime_settings.live_diarization_model,
            model_revision=_runtime_settings.live_diarization_model_revision,
            device=_runtime_settings.live_diarization_device,
            compute_type=_runtime_settings.live_diarization_compute_type,
            similarity_threshold=_runtime_settings.live_diarization_similarity_threshold,
            low_confidence_threshold=_runtime_settings.live_diarization_low_confidence_threshold,
            timeout_seconds=_runtime_settings.live_diarization_timeout_seconds,
            max_retries=_runtime_settings.live_diarization_max_retries,
            worker_concurrency=_runtime_settings.live_diarization_worker_concurrency,
            queue_capacity=_runtime_settings.live_diarization_queue_capacity,
        )
        _diarization_queue = LocalSpeakerDiarizationQueue(
            config,
            PersistentLocalSpeakerEmbedder(config),
        )
    return _diarization_queue


def _get_transcript_postprocess_queue() -> LocalTranscriptPostprocessQueue:
    global _transcript_postprocess_queue
    if _transcript_postprocess_queue is None:
        config = TranscriptPostprocessConfig(
            filler_mode=_runtime_settings.live_transcript_postprocess_filler_mode,
            filler_words=tuple(
                word.strip()
                for word in _runtime_settings.live_transcript_postprocess_filler_words.split(",")
                if word.strip()
            ),
            paragraph_sentences=_runtime_settings.live_transcript_postprocess_paragraph_sentences,
            timeout_seconds=_runtime_settings.live_transcript_postprocess_timeout_seconds,
            max_retries=_runtime_settings.live_transcript_postprocess_max_retries,
            worker_concurrency=_runtime_settings.live_transcript_postprocess_worker_concurrency,
            queue_capacity=_runtime_settings.live_transcript_postprocess_queue_capacity,
        )
        _transcript_postprocess_queue = LocalTranscriptPostprocessQueue(
            config, DeterministicTranscriptProcessor(config)
        )
    return _transcript_postprocess_queue


def _get_live_processing_worker() -> InProcessWorker[tuple, tuple]:
    global _live_processing_worker
    if _live_processing_worker is None:
        _live_processing_worker = InProcessWorker(
            "live_transcription",
            _execute_live_transcription_job,
            capacity=_runtime_settings.live_processing_worker_queue_capacity,
            concurrency=_runtime_settings.live_processing_worker_concurrency,
        )
    return _live_processing_worker


async def _execute_live_transcription_job(payload: tuple) -> tuple:
    session_id, window, glossary, detailed_enabled = payload
    if detailed_enabled:
        detailed = await asyncio.to_thread(
            transcribe_pcm_window_detailed, session_id, window, glossary
        )
        return detailed.session, detailed.duplicate, detailed
    session, duplicate = await asyncio.to_thread(transcribe_pcm_window, session_id, window)
    return session, duplicate, None


async def startup_processing_workers() -> None:
    await _get_live_processing_worker().start()
    if _runtime_settings.live_accurate_final_enabled:
        _get_final_queue()
    if _runtime_settings.live_translation_enabled:
        _get_translation_queue()
    if _runtime_settings.live_translation_quality_enabled:
        _get_translation_quality_queue()
    if _runtime_settings.live_diarization_enabled:
        _get_diarization_queue()
    if _runtime_settings.live_transcript_postprocess_enabled:
        _get_transcript_postprocess_queue()


async def shutdown_final_transcription_queue() -> None:
    global _final_queue
    queue, _final_queue = _final_queue, None
    if queue is not None:
        await queue.close()


async def shutdown_live_translation_queue() -> None:
    global _translation_queue
    queue, _translation_queue = _translation_queue, None
    if queue is not None:
        await queue.close()


async def shutdown_translation_quality_queue() -> None:
    global _translation_quality_queue
    queue, _translation_quality_queue = _translation_quality_queue, None
    if queue is not None:
        await queue.close()


async def shutdown_speaker_diarization_queue() -> None:
    global _diarization_queue
    queue, _diarization_queue = _diarization_queue, None
    if queue is not None:
        await queue.close()


async def shutdown_transcript_postprocess_queue() -> None:
    global _transcript_postprocess_queue
    queue, _transcript_postprocess_queue = _transcript_postprocess_queue, None
    if queue is not None:
        await queue.close()


async def shutdown_processing_workers() -> None:
    global _live_processing_worker
    if _pcm_tasks:
        await asyncio.gather(*tuple(_pcm_tasks.values()), return_exceptions=True)
    worker, _live_processing_worker = _live_processing_worker, None
    if worker is not None:
        await worker.shutdown(drain=True)
    session_ids = set(_connections) | set(_pcm_tasks) | set(_pcm_task_locks)
    for session_id in session_ids:
        _pcm_registry.remove(session_id)
        _vad_registry.remove(session_id)
        _live_state_registry.remove(session_id)
    _segment_glossaries.clear()


class SpeakerRenameRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)


def _event(event_type: str, session: LiveSessionResponse | None = None, **extra: object) -> dict:
    payload = {"type": event_type, **extra}
    if session is not None:
        payload["session"] = session.model_dump(mode="json")
    return payload


async def _send_to_current_connection(session_id: str, payload: dict) -> None:
    async with _connections_lock:
        websocket = _connections.get(session_id)
    if websocket is None:
        return
    try:
        await websocket.send_json(payload)
    except (RuntimeError, WebSocketDisconnect):
        return


async def _drain_pcm_transcription(session_id: str, *, flush: bool) -> None:
    lock = _pcm_task_locks.setdefault(session_id, asyncio.Lock())
    target_ms = _runtime_settings.live_pcm_transcription_window_seconds * 1000
    async with lock:
        if _runtime_settings.live_vad_enabled:
            try:
                while True:
                    window = _pcm_registry.take_audio(
                        session_id,
                        target_duration_ms=10,
                        flush=False,
                    )
                    if window is None:
                        break
                    result = _vad_registry.process(session_id, window)
                    await _send_vad_result(session_id, result)
                    if not await _transcribe_vad_segments(session_id, result):
                        return
                if flush:
                    result = _vad_registry.flush(session_id)
                    await _send_vad_result(session_id, result)
                    await _transcribe_vad_segments(session_id, result)
            except Exception as exc:
                failed = await asyncio.to_thread(
                    fail_live_session,
                    session_id,
                    f"{type(exc).__name__}: {exc}",
                )
                await _send_to_current_connection(
                    session_id,
                    _event("error", failed, message=failed.error or str(exc)),
                )
            return

        while True:
            window = _pcm_registry.take_audio(
                session_id,
                target_duration_ms=target_ms,
                flush=flush,
            )
            if window is None:
                return
            try:
                session, duplicate = await asyncio.to_thread(
                    transcribe_pcm_window,
                    session_id,
                    window,
                )
            except Exception as exc:
                failed = await asyncio.to_thread(
                    fail_live_session,
                    session_id,
                    f"{type(exc).__name__}: {exc}",
                )
                await _send_to_current_connection(
                    session_id,
                    _event("error", failed, message=failed.error or str(exc)),
                )
                return
            await _send_to_current_connection(
                session_id,
                _event(
                    "partial",
                    session,
                    duplicate=duplicate,
                    transport="pcm16",
                    sequenceStart=window.start_sequence,
                    sequenceEnd=window.end_sequence,
                ),
            )
            if flush:
                continue


async def _send_vad_result(session_id: str, result: VadProcessResult) -> None:
    await _send_to_current_connection(
        session_id,
        _event(
            "vad_state",
            transport="pcm16",
            state=result.state.value,
            metrics=result.metrics,
        ),
    )


async def _transcribe_vad_segments(
    session_id: str,
    result: VadProcessResult,
) -> bool:
    for segment in result.segments:
        glossary = (
            _get_glossary_manager().snapshot()
            if _runtime_settings.live_glossary_enabled
            else None
        )
        try:
            worker = _get_live_processing_worker()
            await worker.start()
            live_job = ProcessingJob(
                job_id=f"live:{session_id}:{segment.window.start_sequence}:{segment.window.end_sequence}",
                job_type="live_transcription", session_id=session_id,
                segment_id=f"pcm-{segment.window.start_sequence}-{segment.window.end_sequence}",
                revision=1, priority=JobPriority.LIVE, max_retries=0,
                timeout_ms=_runtime_settings.live_processing_worker_timeout_ms,
                payload=(session_id, segment.window, glossary, _runtime_settings.live_transcript_state_enabled),
            )
            session, duplicate, detailed = await worker.submit_and_wait(live_job)
        except WorkerBackpressureError as exc:
            await _send_to_current_connection(
                session_id,
                _event("error", message=str(exc), retryable=True, backpressure=True),
            )
            return False
        except Exception as exc:
            failed = await asyncio.to_thread(
                fail_live_session,
                session_id,
                f"{type(exc).__name__}: {exc}",
            )
            await _send_to_current_connection(
                session_id,
                _event("error", failed, message=failed.error or str(exc)),
            )
            return False
        await _send_to_current_connection(
            session_id,
            _event(
                "partial",
                session,
                duplicate=duplicate,
                transport="pcm16",
                vad=True,
                vadReason=segment.reason,
                forced=segment.forced,
                sequenceStart=segment.window.start_sequence,
                sequenceEnd=segment.window.end_sequence,
            ),
        )
        if detailed is not None and not detailed.duplicate:
            if (
                _runtime_settings.live_accurate_final_enabled
                and (
                    _runtime_settings.live_translation_enabled
                    or _runtime_settings.live_transcript_postprocess_enabled
                )
            ):
                _segment_glossaries[(session_id, detailed.segment_id)] = glossary
            await _send_live_transcript_lifecycle(session_id, detailed, glossary)
            if _runtime_settings.live_diarization_enabled:
                await _enqueue_speaker_diarization(detailed, segment.window)
            if _runtime_settings.live_accurate_final_enabled:
                await _enqueue_accurate_final(
                    session_id,
                    detailed,
                    segment.window,
                    glossary,
                )
    return True


async def _enqueue_speaker_diarization(
    result: PcmTranscriptionResult,
    window: PcmAudioWindow,
) -> None:
    queue = _get_diarization_queue()
    request = DiarizationRequest(
        session_id=result.session.session_id,
        segment_id=result.segment_id,
        sequence_start=result.sequence_start,
        sequence_end=result.sequence_end,
        start_ms=result.start_ms,
        end_ms=result.end_ms,
        audio_pcm16=window.audio,
    )
    try:
        outcome = await queue.enqueue(request, _handle_diarization_status)
    except (asyncio.QueueFull, ValueError) as exc:
        await _send_to_current_connection(
            request.session_id,
            _event(
                "diarization_state",
                jobId=request.job_id,
                sessionId=request.session_id,
                segmentId=request.segment_id,
                status="failed",
                sequenceStart=request.sequence_start,
                sequenceEnd=request.sequence_end,
                startMs=request.start_ms,
                endMs=request.end_ms,
                assignment=None,
                error=str(exc),
                metrics=queue.metrics(),
            ),
        )
        return
    if not outcome.accepted:
        await _send_to_current_connection(
            request.session_id,
            _event(
                "diarization_state",
                **outcome.snapshot.as_dict(),
                duplicate=True,
                metrics=queue.metrics(),
            ),
        )


async def _handle_diarization_status(snapshot: DiarizationSnapshot) -> None:
    queue = _get_diarization_queue()
    await _send_to_current_connection(
        snapshot.session_id,
        _event("diarization_state", **snapshot.as_dict(), metrics=queue.metrics()),
    )


async def _send_live_transcript_lifecycle(
    session_id: str,
    result: PcmTranscriptionResult,
    glossary: GlossarySnapshot | DisabledGlossarySnapshot | None = None,
) -> None:
    common = {
        "session_id": session_id,
        "segment_id": result.segment_id,
        "sequence_start": result.sequence_start,
        "sequence_end": result.sequence_end,
        "start_ms": result.start_ms,
        "end_ms": result.end_ms,
        "text": result.text,
        "language": result.session.language,
        "model": result.session.model,
        "latency_ms": result.latency_ms,
        "raw_text": result.raw_text,
        "glossary_corrections": tuple(
            correction.as_dict() for correction in result.glossary_corrections
        ),
        "glossary_version": result.glossary_version,
    }
    for revision, state in enumerate(TranscriptState, start=1):
        update = LiveTranscriptUpdate(revision=revision, state=state, **common)
        outcome = _live_state_registry.apply(update)
        if not outcome.accepted:
            continue
        await _send_to_current_connection(
            session_id,
            _event(
                "transcript_state",
                **update.as_dict(),
                metrics=_live_state_registry.metrics(session_id),
                glossaryMetrics=(
                    _get_glossary_manager().metrics()
                    if _runtime_settings.live_glossary_enabled
                    else None
                ),
            ),
        )
        if (
            _runtime_settings.live_transcript_postprocess_enabled
            and state is TranscriptState.FINAL
        ):
            await _enqueue_transcript_postprocess(update, glossary, "final")
        if (
            _runtime_settings.live_translation_enabled
            and state in {TranscriptState.STABLE, TranscriptState.FINAL}
        ):
            await _enqueue_live_translation(update, glossary)


async def _enqueue_transcript_postprocess(
    update: LiveTranscriptUpdate,
    glossary: GlossarySnapshot | DisabledGlossarySnapshot | None,
    source_kind: str,
) -> None:
    queue = _get_transcript_postprocess_queue()
    request = TranscriptPostprocessRequest(
        session_id=update.session_id,
        segment_id=update.segment_id,
        source_revision=update.revision,
        source_kind=source_kind,
        raw_transcript=update.raw_text or update.text,
        glossary_corrected_transcript=update.text,
        language=update.language,
        model=update.model,
        sequence_start=update.sequence_start,
        sequence_end=update.sequence_end,
        start_ms=update.start_ms,
        end_ms=update.end_ms,
        glossary_version=update.glossary_version,
        glossary=glossary,
    )
    try:
        outcome = await queue.enqueue(request, _handle_transcript_postprocess_status)
    except (asyncio.QueueFull, ValueError) as exc:
        await _send_to_current_connection(
            update.session_id,
            _event(
                "transcript_postprocess_state",
                jobId=request.job_id, sessionId=request.session_id,
                segmentId=request.segment_id, sourceRevision=request.source_revision,
                sourceKind=request.source_kind,
                status=TranscriptPostprocessStatus.FAILED.value,
                rawTranscript=request.raw_transcript,
                glossaryCorrectedTranscript=request.glossary_corrected_transcript,
                postProcessedTranscript=request.glossary_corrected_transcript,
                language=request.language, model=request.model,
                sequenceStart=request.sequence_start, sequenceEnd=request.sequence_end,
                startMs=request.start_ms, endMs=request.end_ms,
                glossaryVersion=request.glossary_version,
                fallback=True, error=str(exc), metrics=queue.metrics(),
            ),
        )
        return
    if not outcome.accepted:
        await _send_to_current_connection(
            update.session_id,
            _event(
                "transcript_postprocess_state",
                **outcome.snapshot.as_dict(), rejectedReason=outcome.reason,
                metrics=queue.metrics(),
            ),
        )


async def _handle_transcript_postprocess_status(
    snapshot: TranscriptPostprocessSnapshot,
) -> None:
    queue = _get_transcript_postprocess_queue()
    await _send_to_current_connection(
        snapshot.session_id,
        _event("transcript_postprocess_state", **snapshot.as_dict(), metrics=queue.metrics()),
    )


async def _enqueue_live_translation(
    update: LiveTranscriptUpdate,
    glossary: GlossarySnapshot | DisabledGlossarySnapshot | None,
) -> None:
    queue = _get_translation_queue()
    previous = [
        item
        for item in _live_state_registry.snapshot(update.session_id)
        if item.segment_id != update.segment_id
        and (item.sequence_start, item.sequence_end) < (update.sequence_start, update.sequence_end)
    ][-queue.config.context_segments :]
    request = TranslationRequest(
        session_id=update.session_id,
        segment_id=update.segment_id,
        source_revision=update.revision,
        source_state=update.state,
        source_text=update.text,
        source_language=queue.config.source_language,
        target_language=queue.config.target_language,
        context_segment_ids=tuple(item.segment_id for item in previous),
        context_texts=tuple(item.text for item in previous),
        glossary=glossary,
        start_ms=update.start_ms,
        end_ms=update.end_ms,
    )
    try:
        outcome = await queue.enqueue(request, _handle_translation_status)
    except (asyncio.QueueFull, ValueError) as exc:
        await _send_to_current_connection(
            update.session_id,
            _event(
                "translation_state",
                sessionId=update.session_id,
                segmentId=update.segment_id,
                sourceRevision=update.revision,
                sourceState=update.state.value,
                sourceText=update.text,
                status="failed",
                error=str(exc),
                metrics=queue.metrics(),
            ),
        )
        return
    if not outcome.accepted:
        await _send_to_current_connection(
            update.session_id,
            _event(
                "translation_state",
                **outcome.snapshot.as_dict(),
                rejectedReason=outcome.reason,
                metrics=queue.metrics(),
            ),
        )


async def _handle_translation_status(snapshot: TranslationSnapshot) -> None:
    queue = _get_translation_queue()
    await _send_to_current_connection(
        snapshot.session_id,
        _event("translation_state", **snapshot.as_dict(), metrics=queue.metrics()),
    )
    if (
        _runtime_settings.live_translation_quality_enabled
        and snapshot.status is TranslationStatus.COMPLETED
        and snapshot.result is not None
    ):
        await _enqueue_translation_quality(snapshot)


async def _enqueue_translation_quality(snapshot: TranslationSnapshot) -> None:
    if snapshot.result is None:
        return
    queue = _get_translation_quality_queue()
    metadata = snapshot.result.metadata
    request = TranslationQualityRequest(
        session_id=snapshot.session_id,
        segment_id=snapshot.segment_id,
        translation_revision=snapshot.translation_revision,
        source_text=snapshot.source_text,
        raw_model_translation=snapshot.result.raw_text,
        final_translation=snapshot.result.text,
        source_language=metadata.source_language,
        target_language=metadata.target_language,
        glossary_version=metadata.glossary_version,
        start_ms=metadata.start_ms,
        end_ms=metadata.end_ms,
        glossary=snapshot.glossary,
    )
    try:
        outcome = await queue.enqueue(request, _handle_translation_quality_status)
    except (asyncio.QueueFull, ValueError) as exc:
        await _send_to_current_connection(
            snapshot.session_id,
            _event(
                "translation_quality_state",
                jobId=request.job_id,
                sessionId=request.session_id,
                segmentId=request.segment_id,
                translationRevision=request.translation_revision,
                status=QualityStatus.FAILED.value,
                sourceText=request.source_text,
                rawModelTranslation=request.raw_model_translation,
                rawTranslation=request.final_translation,
                correctedTranslation=request.final_translation,
                sourceLanguage=request.source_language,
                targetLanguage=request.target_language,
                glossaryVersion=request.glossary_version,
                startMs=request.start_ms,
                endMs=request.end_ms,
                fallback=True,
                error=str(exc),
                metrics=queue.metrics(),
            ),
        )
        return
    if not outcome.accepted:
        await _send_to_current_connection(
            snapshot.session_id,
            _event(
                "translation_quality_state",
                **outcome.snapshot.as_dict(),
                rejectedReason=outcome.reason,
                metrics=queue.metrics(),
            ),
        )


async def _handle_translation_quality_status(
    snapshot: TranslationQualitySnapshot,
) -> None:
    queue = _get_translation_quality_queue()
    await _send_to_current_connection(
        snapshot.session_id,
        _event(
            "translation_quality_state",
            **snapshot.as_dict(),
            metrics=queue.metrics(),
        ),
    )


async def _enqueue_accurate_final(
    session_id: str,
    live_result: PcmTranscriptionResult,
    window: PcmAudioWindow,
    glossary: GlossarySnapshot | DisabledGlossarySnapshot | None,
) -> None:
    if not _runtime_settings.live_transcript_state_enabled:
        await _send_to_current_connection(
            session_id,
            _event(
                "final_correction",
                sessionId=session_id,
                segmentId=live_result.segment_id,
                status=FinalJobStatus.FAILED.value,
                error="Accurate final transcription requires live transcript state",
            ),
        )
        return
    queue = _get_final_queue()
    request = FinalTranscriptionRequest(
        session_id=session_id,
        segment_id=live_result.segment_id,
        sequence_start=live_result.sequence_start,
        sequence_end=live_result.sequence_end,
        start_ms=live_result.start_ms,
        end_ms=live_result.end_ms,
        language=live_result.session.language,
        audio_wav=pcm_window_to_wav(window),
        glossary=glossary,
    )
    try:
        snapshot, duplicate = await queue.enqueue(request, _handle_final_job_status)
    except asyncio.QueueFull as exc:
        _segment_glossaries.pop((session_id, live_result.segment_id), None)
        await _send_to_current_connection(
            session_id,
            _event(
                "final_correction",
                sessionId=session_id,
                segmentId=live_result.segment_id,
                status=FinalJobStatus.FAILED.value,
                error=str(exc),
                metrics=queue.metrics(),
            ),
        )
        return
    if duplicate:
        await _send_to_current_connection(
            session_id,
            _event(
                "final_correction",
                **snapshot.as_dict(),
                duplicate=True,
                metrics=queue.metrics(),
            ),
        )


async def _handle_final_job_status(snapshot: FinalJobSnapshot) -> None:
    queue = _get_final_queue()
    payload = snapshot.as_dict()
    if snapshot.status is FinalJobStatus.COMPLETED and snapshot.result is not None:
        current = _live_state_registry.latest(snapshot.session_id, snapshot.segment_id)
        if current is not None:
            corrected = LiveTranscriptUpdate(
                session_id=current.session_id,
                segment_id=current.segment_id,
                revision=current.revision + 1,
                state=TranscriptState.FINAL,
                sequence_start=current.sequence_start,
                sequence_end=current.sequence_end,
                start_ms=current.start_ms,
                end_ms=current.end_ms,
                text=snapshot.result.text,
                language=snapshot.result.metadata.language,
                model=snapshot.result.metadata.model,
                latency_ms=snapshot.result.metadata.latency_ms,
                raw_text=snapshot.result.raw_text,
                glossary_corrections=tuple(
                    correction.as_dict()
                    for correction in snapshot.result.glossary_corrections
                ),
                glossary_version=snapshot.result.glossary_version,
            )
            outcome = _live_state_registry.replace_with_accurate_final(corrected)
            if outcome.accepted:
                queue.record_replacement()
                payload["update"] = corrected.as_dict()
                if _runtime_settings.live_transcript_postprocess_enabled:
                    await _enqueue_transcript_postprocess(
                        corrected,
                        _segment_glossaries.get((corrected.session_id, corrected.segment_id)),
                        "accurate_final",
                    )
                if _runtime_settings.live_translation_enabled:
                    await _enqueue_live_translation(
                        corrected,
                        _segment_glossaries.get((corrected.session_id, corrected.segment_id)),
                    )
            else:
                payload["status"] = FinalJobStatus.FAILED.value
                payload["error"] = outcome.reason
    if snapshot.status in {FinalJobStatus.COMPLETED, FinalJobStatus.FAILED}:
        _segment_glossaries.pop((snapshot.session_id, snapshot.segment_id), None)
    await _send_to_current_connection(
        snapshot.session_id,
        _event(
            "final_correction",
            **payload,
            metrics=queue.metrics(),
            glossaryMetrics=(
                _get_glossary_manager().metrics()
                if _runtime_settings.live_glossary_enabled
                else None
            ),
        ),
    )


async def _schedule_pcm_transcription(session_id: str) -> None:
    target_ms = (
        10
        if _runtime_settings.live_vad_enabled
        else _runtime_settings.live_pcm_transcription_window_seconds * 1000
    )
    if float(_pcm_registry.metrics(session_id)["buffer_depth_ms"]) < target_ms:
        return
    async with _pcm_task_registry_lock:
        existing = _pcm_tasks.get(session_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(_drain_pcm_transcription(session_id, flush=False))
        _pcm_tasks[session_id] = task


async def _finish_pcm_session(session_id: str) -> None:
    async with _pcm_task_registry_lock:
        task = _pcm_tasks.get(session_id)
    if task is not None:
        await task
    await _drain_pcm_transcription(session_id, flush=True)
    async with _pcm_task_registry_lock:
        _pcm_tasks.pop(session_id, None)
        _pcm_task_locks.pop(session_id, None)
    _pcm_registry.remove(session_id)
    _vad_registry.remove(session_id)
    if _live_processing_worker is not None:
        await _live_processing_worker.cancel_session(session_id)
    if not _runtime_settings.live_accurate_final_enabled:
        _live_state_registry.remove(session_id)


@router.post("/api/live/sessions", response_model=LiveSessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(payload: CreateLiveSessionRequest) -> LiveSessionResponse:
    return create_live_session(payload)


@router.get("/api/live/sessions", response_model=list[LiveSessionResponse])
def get_sessions(limit: int = Query(default=20, ge=1, le=100)) -> list[LiveSessionResponse]:
    return list_live_sessions(limit)


def _specialized_worker_health(name: str, queue: object | None, enabled: bool) -> dict[str, object]:
    if queue is None:
        return {
            "name": name, "running": False, "ready": not enabled,
            "queueDepth": 0, "capacity": 0, "activeJobs": 0,
            "modelLoaded": False, "lastSuccess": None, "lastFailure": None,
        }
    metrics = queue.metrics()
    config = queue.config
    snapshots = tuple(getattr(queue, "_jobs", {}).values())
    status_values = [getattr(getattr(item, "status", None), "value", "") for item in snapshots]
    successes = [getattr(item, "updated_at", None) for item in snapshots if getattr(getattr(item, "status", None), "value", "") == "completed"]
    failures = [getattr(item, "updated_at", None) for item in snapshots if getattr(getattr(item, "status", None), "value", "") == "failed"]
    model_owner = getattr(queue, "transcriber", None) or getattr(queue, "translator", None) or getattr(queue, "embedder", None)
    model_loaded = any(
        getattr(model_owner, attribute, None) is not None
        for attribute in ("_model", "_classifier")
    ) if model_owner is not None else True
    return {
        "name": name, "running": True, "ready": True,
        "queueDepth": metrics.get("queue_depth", 0),
        "capacity": config.queue_capacity, "activeJobs": status_values.count("processing"),
        "modelLoaded": model_loaded,
        "modelLoadTimeMs": metrics.get("model_load_time_ms", 0),
        "lastSuccess": max(successes).isoformat() if successes else None,
        "lastFailure": max(failures).isoformat() if failures else None,
        "completed": metrics.get("completed", metrics.get("processed_quality_jobs", 0)),
        "failed": metrics.get("failed", metrics.get("failed_jobs", 0)),
        "retried": metrics.get("retries", 0),
    }


@router.get("/api/live/workers/health")
def processing_worker_health() -> dict[str, object]:
    workers = {
        "live_transcription": _get_live_processing_worker().health(),
        "accurate_final_transcription": _specialized_worker_health("accurate_final_transcription", _final_queue, _runtime_settings.live_accurate_final_enabled),
        "translation": _specialized_worker_health("translation", _translation_queue, _runtime_settings.live_translation_enabled),
        "translation_quality": _specialized_worker_health("translation_quality", _translation_quality_queue, _runtime_settings.live_translation_quality_enabled),
        "diarization": _specialized_worker_health("diarization", _diarization_queue, _runtime_settings.live_diarization_enabled),
        "transcript_postprocessing": _specialized_worker_health("transcript_postprocessing", _transcript_postprocess_queue, _runtime_settings.live_transcript_postprocess_enabled),
    }
    return {"ready": all(bool(worker["ready"]) for worker in workers.values()), "workers": workers}


@router.post("/api/live/glossary/reload")
def reload_live_glossary() -> dict[str, object]:
    manager = _get_glossary_manager()
    snapshot = manager.reload()
    return {
        "enabled": manager.enabled,
        "version": snapshot.version,
        "metrics": manager.metrics(),
    }


@router.post("/api/live/sessions/{session_id}/speakers/{speaker_id}/rename")
async def rename_live_speaker(
    session_id: str,
    speaker_id: str,
    payload: SpeakerRenameRequest,
) -> dict[str, object]:
    if not _runtime_settings.live_diarization_enabled:
        raise HTTPException(status_code=409, detail="Live speaker diarization is disabled")
    queue = _get_diarization_queue()
    try:
        assignments = queue.rename(session_id, speaker_id, payload.label)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response = {
        "sessionId": session_id,
        "speakerId": speaker_id,
        "label": " ".join(payload.label.split()).strip(),
        "assignments": [item.as_dict() for item in assignments],
        "metrics": queue.metrics(),
    }
    await _send_to_current_connection(
        session_id,
        _event("diarization_snapshot", **response),
    )
    return response


@router.get("/api/live/sessions/{session_id}", response_model=LiveSessionResponse)
def get_session(session_id: str) -> LiveSessionResponse:
    return get_live_session(session_id)


@router.post("/api/live/sessions/{session_id}/stop", response_model=LiveSessionResponse)
async def stop_session(session_id: str) -> LiveSessionResponse:
    await _finish_pcm_session(session_id)
    session = await asyncio.to_thread(stop_live_session, session_id)
    async with _connections_lock:
        websocket = _connections.pop(session_id, None)
    if websocket is not None:
        try:
            await websocket.send_json(_event("final", session))
            await websocket.send_json(_event("stopped", session))
            await websocket.close(code=1000)
        except RuntimeError:
            pass
    return session


@router.websocket("/ws/live/{session_id}")
async def live_websocket(websocket: WebSocket, session_id: str) -> None:
    if not is_allowed_web_origin(websocket.headers.get("origin")):
        await websocket.close(code=1008, reason="WebSocket origin is not allowed")
        return
    await websocket.accept()
    try:
        session = await asyncio.to_thread(get_live_session, session_id)
    except Exception as exc:
        await websocket.send_json(_event("error", message=str(exc)))
        await websocket.close(code=1008)
        return

    if session.status in {"completed", "failed"}:
        await websocket.send_json(_event("error", session, message="Live session is already finished"))
        await websocket.close(code=1008)
        return

    async with _connections_lock:
        previous = _connections.get(session_id)
        _connections[session_id] = websocket
    if previous is not None and previous is not websocket:
        try:
            await previous.close(code=1012, reason="Session reconnected")
        except RuntimeError:
            pass

    await websocket.send_json(_event("connected", session))
    pending_pcm_metadata: PcmChunkMetadata | None = None
    pcm_registered = False
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1006))

            audio = message.get("bytes")
            if audio is not None:
                if not audio:
                    await websocket.send_json(_event("error", message="Audio chunk is empty"))
                    continue
                if pending_pcm_metadata is not None:
                    metadata = pending_pcm_metadata
                    pending_pcm_metadata = None
                    try:
                        outcome = _pcm_registry.ingest(session_id, metadata, audio)
                    except PcmProtocolError as exc:
                        await websocket.send_json(
                            _event(
                                "error",
                                message=str(exc),
                                transport="pcm16",
                                sequence=metadata.sequence,
                            )
                        )
                        continue
                    await websocket.send_json(
                        _event(
                            "ack",
                            **outcome.acknowledgement(),
                        )
                    )
                    if outcome.status != "backpressure":
                        await _schedule_pcm_transcription(session_id)
                    continue
                if len(audio) > MAX_LIVE_CHUNK_BYTES:
                    await websocket.send_json(_event("error", message="Audio chunk exceeds the 10 MB limit"))
                    continue
                await websocket.send_json(_event("processing"))
                try:
                    session, duplicate = await asyncio.to_thread(process_live_chunk, session_id, audio)
                except HTTPException as exc:
                    current = await asyncio.to_thread(get_live_session, session_id)
                    await websocket.send_json(_event("error", current, message=str(exc.detail)))
                    continue
                except Exception as exc:
                    failed = await asyncio.to_thread(fail_live_session, session_id, f"{type(exc).__name__}: {exc}")
                    await websocket.send_json(_event("error", failed, message=failed.error or str(exc)))
                    await websocket.close(code=1011)
                    return
                await websocket.send_json(_event("partial", session, duplicate=duplicate))
                continue

            text = message.get("text")
            if text is None:
                continue
            try:
                command = json.loads(text)
            except json.JSONDecodeError:
                await websocket.send_json(_event("error", message="Invalid WebSocket command"))
                continue
            if not isinstance(command, dict):
                await websocket.send_json(_event("error", message="WebSocket command must be an object"))
                continue

            command_type = command.get("type")
            try:
                if pending_pcm_metadata is not None:
                    pending_pcm_metadata = None
                    await websocket.send_json(
                        _event("error", message="Expected PCM binary frame after chunk metadata")
                    )
                    continue
                if command_type == "pcm_hello":
                    if not _runtime_settings.live_pcm_streaming_enabled:
                        await websocket.send_json(
                            _event("error", message="PCM streaming is disabled", transport="pcm16")
                        )
                        continue
                    if command.get("sessionId") != session_id:
                        await websocket.send_json(
                            _event("error", message="PCM handshake sessionId mismatch", transport="pcm16")
                        )
                        continue
                    metrics = (
                        _pcm_registry.register_connection(session_id)
                        if not pcm_registered
                        else _pcm_registry.metrics(session_id)
                    )
                    pcm_registered = True
                    await websocket.send_json(
                        _event(
                            "pcm_ready",
                            transport="pcm16",
                            expectedSequence=_pcm_registry.expected_sequence(session_id),
                            sampleRate=PCM_SAMPLE_RATE,
                            channelCount=PCM_CHANNEL_COUNT,
                            chunkDurationMinMs=MIN_CHUNK_DURATION_MS,
                            chunkDurationMaxMs=MAX_CHUNK_DURATION_MS,
                            metrics=metrics,
                        )
                    )
                    if (
                        _runtime_settings.live_vad_enabled
                        and _runtime_settings.live_transcript_state_enabled
                    ):
                        await websocket.send_json(
                            _event(
                                "transcript_state_snapshot",
                                updates=[
                                    update.as_dict()
                                    for update in _live_state_registry.snapshot(session_id)
                                ],
                                metrics=_live_state_registry.metrics(session_id),
                                glossaryMetrics=(
                                    _get_glossary_manager().metrics()
                                    if _runtime_settings.live_glossary_enabled
                                    else None
                                ),
                            )
                        )
                    if _runtime_settings.live_accurate_final_enabled and _final_queue is not None:
                        await websocket.send_json(
                            _event(
                                "final_correction_snapshot",
                                jobs=[
                                    job.as_dict()
                                    for job in _final_queue.snapshot(session_id)
                                ],
                                metrics=_final_queue.metrics(),
                            )
                        )
                    if _runtime_settings.live_translation_enabled and _translation_queue is not None:
                        await websocket.send_json(
                            _event(
                                "translation_state_snapshot",
                                translations=[
                                    item.as_dict()
                                    for item in _translation_queue.snapshot(session_id)
                                ],
                                metrics=_translation_queue.metrics(),
                            )
                        )
                    if (
                        _runtime_settings.live_translation_quality_enabled
                        and _translation_quality_queue is not None
                    ):
                        await websocket.send_json(
                            _event(
                                "translation_quality_snapshot",
                                qualityResults=[
                                    item.as_dict()
                                    for item in _translation_quality_queue.snapshot(session_id)
                                ],
                                metrics=_translation_quality_queue.metrics(),
                            )
                        )
                    if _runtime_settings.live_diarization_enabled and _diarization_queue is not None:
                        await websocket.send_json(
                            _event(
                                "diarization_snapshot",
                                assignments=[
                                    item.as_dict()
                                    for item in _diarization_queue.snapshot(session_id)
                                ],
                                metrics=_diarization_queue.metrics(),
                            )
                        )
                    if (
                        _runtime_settings.live_transcript_postprocess_enabled
                        and _transcript_postprocess_queue is not None
                    ):
                        await websocket.send_json(
                            _event(
                                "transcript_postprocess_snapshot",
                                results=[
                                    item.as_dict()
                                    for item in _transcript_postprocess_queue.snapshot(session_id)
                                ],
                                metrics=_transcript_postprocess_queue.metrics(),
                            )
                        )
                elif command_type == "pcm_chunk":
                    if not _runtime_settings.live_pcm_streaming_enabled or not pcm_registered:
                        await websocket.send_json(
                            _event("error", message="PCM handshake is required", transport="pcm16")
                        )
                        continue
                    try:
                        pending_pcm_metadata = PcmChunkMetadata.from_payload(command)
                        if pending_pcm_metadata.session_id != session_id:
                            raise PcmProtocolError("PCM metadata sessionId mismatch")
                    except PcmProtocolError as exc:
                        pending_pcm_metadata = None
                        await websocket.send_json(
                            _event("error", message=str(exc), transport="pcm16")
                        )
                elif command_type == "pause":
                    session = await asyncio.to_thread(pause_live_session, session_id)
                    await websocket.send_json(_event("connected", session))
                elif command_type == "resume":
                    session = await asyncio.to_thread(resume_live_session, session_id)
                    await websocket.send_json(_event("connected", session))
                elif command_type == "stop":
                    await _finish_pcm_session(session_id)
                    session = await asyncio.to_thread(stop_live_session, session_id)
                    await websocket.send_json(_event("final", session))
                    await websocket.send_json(_event("stopped", session))
                    await websocket.close(code=1000)
                    return
                else:
                    await websocket.send_json(_event("error", message="Unsupported WebSocket command"))
            except HTTPException as exc:
                current = await asyncio.to_thread(get_live_session, session_id)
                await websocket.send_json(_event("error", current, message=str(exc.detail)))
            except PcmProtocolError as exc:
                await websocket.send_json(_event("error", message=str(exc), transport="pcm16"))
    except WebSocketDisconnect:
        await asyncio.to_thread(record_disconnect, session_id)
    finally:
        async with _connections_lock:
            if _connections.get(session_id) is websocket:
                _connections.pop(session_id, None)

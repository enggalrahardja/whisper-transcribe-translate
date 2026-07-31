import asyncio
import hashlib
import json
import platform
from datetime import datetime, timezone
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from ..config import get_settings
from ..models.live import CreateLiveSessionRequest, LiveSessionResponse
from ..security import (
    Principal, allow_bursty_throughput, authorize_owner, enforce_concurrent_limit, is_allowed_web_origin,
    rate_limit_or_raise, require_admin, require_principal,
    safe_error, validate_audio_frame_size, websocket_idle_expired, websocket_principal,
)
from ..services.production_hardening import audit_event
from ..services.live_processor import process_live_chunk
from ..services.final_transcription import (
    FinalJobSnapshot,
    FinalJobStatus,
    FinalTranscriptionConfig,
    FinalTranscriptionRequest,
    LocalFinalTranscriptionQueue,
    PersistentLocalFinalTranscriber,
)
from ..services.transcription_providers import (
    LocalTranscriptionProvider,
    OpenAIProviderConfig,
    OpenAITranscriptionProvider,
    PricingCatalogue,
    ProviderLiveEvent,
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
from ..services.pipeline_persistence import (
    MongoPipelineRepository,
    PipelinePersistenceService,
)
from ..services.pipeline_monitoring import latency_summary, quality_indicators, redact_metrics, resource_metrics, warnings_for
from ..services.live_sessions import (
    create_live_session,
    count_active_sessions,
    delete_live_session,
    elapsed_session_seconds,
    fail_live_session,
    get_live_session,
    get_live_session_owner,
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
_reconnect_attempts: dict[str, int] = {}
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
_persistence_service: PipelinePersistenceService | None = None
_openai_provider: OpenAITranscriptionProvider | None = None
_openai_live_sessions: dict[str, object] = {}
_openai_pending_boundaries: dict[str, list[tuple[PcmAudioWindow, float, object]]] = {}
_openai_item_boundaries: dict[tuple[str, str], tuple[PcmAudioWindow, float, object]] = {}
_segment_glossaries: dict[
    tuple[str, str], GlossarySnapshot | DisabledGlossarySnapshot | None
] = {}


def _enforce_rate(category: str, principal: Principal, limit: int, *, session_id: str | None = None) -> None:
    try:
        rate_limit_or_raise(category, principal.user_id, limit)
    except HTTPException:
        audit_event("rate_limit_rejection", principal=principal, session_id=session_id, outcome="rejected", metadata={"category": category})
        raise


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
        local_transcriber = PersistentLocalFinalTranscriber(config)
        if _runtime_settings.live_final_provider == "openai":
            provider = _get_openai_provider(
                local_fallback=local_transcriber if _runtime_settings.openai_allow_local_fallback else None
            )
            transcriber = provider.final_transcriber
            config = FinalTranscriptionConfig(
                model=config.model, device=config.device, compute_type=config.compute_type,
                beam_size=config.beam_size, timeout_seconds=config.timeout_seconds,
                max_retries=0, worker_concurrency=config.worker_concurrency,
                queue_capacity=config.queue_capacity,
            )
        else:
            transcriber = LocalTranscriptionProvider(local_transcriber)
        _final_queue = LocalFinalTranscriptionQueue(config, transcriber)
    return _final_queue


def _get_openai_provider(*, local_fallback=None) -> OpenAITranscriptionProvider:
    global _openai_provider
    if _openai_provider is None:
        config = OpenAIProviderConfig(
            api_key=_runtime_settings.openai_api_key,
            live_model=_runtime_settings.openai_live_model,
            final_model=_runtime_settings.openai_final_model,
            base_url=_runtime_settings.openai_base_url,
            realtime_url=_runtime_settings.openai_realtime_url,
            timeout_seconds=_runtime_settings.openai_timeout_seconds,
            max_retries=_runtime_settings.openai_max_retries,
            rate_limit_per_minute=_runtime_settings.openai_rate_limit_per_minute,
            external_audio_consent=_runtime_settings.openai_external_audio_consent,
        )
        _openai_provider = OpenAITranscriptionProvider(
            config, PricingCatalogue(_runtime_settings.openai_pricing_catalogue_path),
            local_fallback=local_fallback,
        )
    elif local_fallback is not None:
        _openai_provider.final_transcriber.local_fallback = local_fallback
    return _openai_provider


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


def _get_persistence_service() -> PipelinePersistenceService | None:
    global _persistence_service
    if not _runtime_settings.live_pipeline_persistence_enabled:
        return None
    if _persistence_service is None:
        _persistence_service = PipelinePersistenceService(
            MongoPipelineRepository(),
            capacity=_runtime_settings.live_pipeline_persistence_queue_capacity,
            max_retries=_runtime_settings.live_pipeline_persistence_max_retries,
        )
    return _persistence_service


def _persist(kind: str, value: dict) -> bool:
    service = _get_persistence_service()
    return service.submit(kind, value) if service is not None else False


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
    persistence = _get_persistence_service()
    if persistence is not None:
        try:
            await asyncio.to_thread(persistence.repository.ensure_indexes)
        except Exception:
            pass
        await persistence.start()


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
    global _live_processing_worker, _persistence_service, _openai_provider
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
    openai_sessions = tuple(_openai_live_sessions.values())
    _openai_live_sessions.clear()
    if openai_sessions:
        await asyncio.gather(
            *(session.close() for session in openai_sessions), return_exceptions=True
        )
    _openai_pending_boundaries.clear()
    _openai_item_boundaries.clear()
    _openai_provider = None
    persistence, _persistence_service = _persistence_service, None
    if persistence is not None:
        await persistence.close()


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


async def _handle_openai_live_event(session_id: str, event: ProviderLiveEvent) -> None:
    boundary = _openai_item_boundaries.get((session_id, event.item_id))
    if boundary is None:
        pending = _openai_pending_boundaries.get(session_id, [])
        if not pending:
            return
        boundary = pending.pop(0)
        _openai_item_boundaries[(session_id, event.item_id)] = boundary
    window, committed_at, glossary = boundary
    current = _live_state_registry.latest(session_id, f"pcm-{window.start_sequence}-{window.end_sequence}")
    if current is not None and current.state is TranscriptState.FINAL:
        return
    raw_text = event.text.strip()
    correction = glossary.correct(raw_text, language=event.language) if glossary is not None and raw_text else None
    update = LiveTranscriptUpdate(
        session_id=session_id,
        segment_id=f"pcm-{window.start_sequence}-{window.end_sequence}",
        revision=1 if current is None else current.revision + 1,
        state=TranscriptState(event.state),
        sequence_start=window.start_sequence,
        sequence_end=window.end_sequence,
        start_ms=window.start_ms,
        end_ms=window.end_ms,
        text=correction.text if correction else raw_text,
        raw_text=raw_text,
        language=event.language,
        model=event.model,
        latency_ms=max(0.0, (monotonic() - committed_at) * 1000),
        glossary_corrections=tuple(item.as_dict() for item in correction.corrections) if correction else (),
        glossary_version=correction.glossary_version if correction else None,
    )
    outcome = _live_state_registry.apply(update, rollback_reason="openai_provider_revision")
    if not outcome.accepted:
        return
    _persist("transcript", {
        "sessionId": update.session_id, "segmentId": update.segment_id,
        "revision": update.revision, "state": update.state.value,
        "sourceType": "live", "rawText": update.raw_text,
        "glossaryCorrectedText": update.text, "postProcessedText": None,
        "language": update.language,
        "modelMetadata": {
            "provider": "openai", "model": update.model, "localCloud": "cloud",
            "apiRequestId": event.request_id,
        },
        "glossaryVersion": update.glossary_version,
        "corrections": list(update.glossary_corrections),
        "latencyMs": update.latency_ms,
        "sequenceStart": update.sequence_start, "sequenceEnd": update.sequence_end,
        "startMs": update.start_ms, "endMs": update.end_ms,
    })
    await _send_to_current_connection(
        session_id,
        _event(
            "transcript_state", update=update.as_dict(),
            provider="openai", privacy="audio_sent_to_external_service",
            metrics=_live_state_registry.metrics(session_id),
        ),
    )
    if update.state is TranscriptState.STABLE and _runtime_settings.live_translation_enabled:
        await _enqueue_live_translation(update, glossary)
    if update.state is TranscriptState.FINAL:
        _openai_item_boundaries.pop((session_id, event.item_id), None)
        if _runtime_settings.live_translation_enabled and not _runtime_settings.live_accurate_final_enabled:
            await _enqueue_live_translation(update, glossary)


async def _ensure_openai_live_session(session_id: str) -> object:
    existing = _openai_live_sessions.get(session_id)
    if existing is not None:
        return existing
    provider = _get_openai_provider()
    session = await provider.open_live_session(
        lambda event: _handle_openai_live_event(session_id, event)
    )
    _openai_live_sessions[session_id] = session
    return session


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
        if _runtime_settings.live_transcription_provider == "openai":
            try:
                live_session = await _ensure_openai_live_session(session_id)
                _openai_pending_boundaries.setdefault(session_id, []).append(
                    (segment.window, monotonic(), glossary)
                )
                await live_session.commit()
                persisted_segment_id = f"pcm-{segment.window.start_sequence}-{segment.window.end_sequence}"
                _persist("segment", {
                    "sessionId": session_id, "segmentId": persisted_segment_id,
                    "sequenceStart": segment.window.start_sequence,
                    "sequenceEnd": segment.window.end_sequence,
                    "startMs": segment.window.start_ms, "endMs": segment.window.end_ms,
                    "durationMs": segment.window.end_ms - segment.window.start_ms,
                    "audioReference": f"runtime://{session_id}/{persisted_segment_id}",
                    "audioSha256": hashlib.sha256(segment.window.audio).hexdigest(),
                    "finalizedReason": segment.reason,
                })
                continue
            except Exception as exc:
                if not _runtime_settings.openai_allow_local_fallback:
                    await _send_to_current_connection(
                        session_id,
                        _event("error", message=safe_error(exc), provider="openai", fallback=False),
                    )
                    return False
                await _send_to_current_connection(
                    session_id,
                    _event("provider_fallback", provider="openai", fallbackProvider="local"),
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
            _persist("job", worker._jobs[live_job.job_id].as_dict())
            persisted_segment_id = detailed.segment_id if detailed is not None else live_job.segment_id
            _persist("segment", {
                "sessionId": session_id, "segmentId": persisted_segment_id,
                "sequenceStart": segment.window.start_sequence,
                "sequenceEnd": segment.window.end_sequence,
                "startMs": segment.window.start_ms, "endMs": segment.window.end_ms,
                "durationMs": segment.window.end_ms - segment.window.start_ms,
                "audioReference": f"runtime://{session_id}/{persisted_segment_id}",
                "audioSha256": hashlib.sha256(segment.window.audio).hexdigest(),
                "finalizedReason": segment.reason,
            })
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
        _persist("transcript", {
            "sessionId": update.session_id, "segmentId": update.segment_id,
            "revision": update.revision, "state": update.state.value,
            "sourceType": "live", "rawText": update.raw_text or update.text,
            "glossaryCorrectedText": update.text, "postProcessedText": None,
            "language": update.language,
            "modelMetadata": {"provider": "whisper", "model": update.model, "localCloud": "local"},
            "glossaryVersion": update.glossary_version,
            "corrections": list(update.glossary_corrections),
            "latencyMs": update.latency_ms,
            "sequenceStart": update.sequence_start, "sequenceEnd": update.sequence_end,
            "startMs": update.start_ms, "endMs": update.end_ms,
        })
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
    if snapshot.assignment is not None:
        assignment = snapshot.assignment.as_dict()
        _persist("speaker", {
            "sessionId": snapshot.session_id, "segmentId": snapshot.segment_id,
            "speakerId": assignment["speakerId"], "speakerLabel": assignment["speakerLabel"],
            "confidence": assignment["confidence"],
            "modelMetadata": {
                key: assignment.get(key) for key in
                ("provider", "model", "checkpoint", "localCloud", "device", "computeType", "embeddingVersion")
            },
            "clusteringRevision": assignment["clusteringRevision"],
        })
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
    if (
        snapshot.status is TranscriptPostprocessStatus.COMPLETED
        and (
            snapshot.source_kind == "accurate_final"
            or not _runtime_settings.live_accurate_final_enabled
        )
    ):
        _persist("transcript", {
            "sessionId": snapshot.session_id, "segmentId": snapshot.segment_id,
            "revision": snapshot.source_revision + 1, "state": "final",
            "sourceType": "post_processed",
            "rawText": snapshot.raw_transcript,
            "glossaryCorrectedText": snapshot.glossary_corrected_transcript,
            "postProcessedText": snapshot.post_processed_transcript,
            "language": snapshot.language,
            "modelMetadata": {"provider": "local-rules", "model": snapshot.model, "localCloud": "local"},
            "glossaryVersion": snapshot.glossary_version,
            "corrections": [item.as_dict() for item in snapshot.applied_corrections],
            "latencyMs": snapshot.latency_ms,
            "sequenceStart": snapshot.sequence_start, "sequenceEnd": snapshot.sequence_end,
            "startMs": snapshot.start_ms, "endMs": snapshot.end_ms,
        })
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
    if snapshot.status in {TranslationStatus.PREVIEW, TranslationStatus.COMPLETED, TranslationStatus.FAILED}:
        data = snapshot.as_dict()
        _persist("translation", {
            "sessionId": snapshot.session_id, "segmentId": snapshot.segment_id,
            "revision": snapshot.translation_revision, "status": snapshot.status.value,
            "rawTranslation": data.get("rawTranslatedText"),
            "correctedTranslation": data.get("translatedText"),
            "sourceLanguage": data.get("metadata", {}).get("sourceLanguage") if isinstance(data.get("metadata"), dict) else None,
            "targetLanguage": data.get("metadata", {}).get("targetLanguage") if isinstance(data.get("metadata"), dict) else None,
            "contextSegmentIds": data.get("metadata", {}).get("contextSegmentIds", []) if isinstance(data.get("metadata"), dict) else [],
            "glossaryVersion": data.get("metadata", {}).get("glossaryVersion") if isinstance(data.get("metadata"), dict) else None,
            "modelMetadata": data.get("metadata"), "latencyMs": data.get("latencyMs", 0),
        })
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
                _persist("transcript", {
                    "sessionId": corrected.session_id, "segmentId": corrected.segment_id,
                    "revision": corrected.revision, "state": "final",
                    "sourceType": "accurate_final",
                    "rawText": corrected.raw_text or corrected.text,
                    "glossaryCorrectedText": corrected.text,
                    "postProcessedText": None, "language": corrected.language,
                    "modelMetadata": snapshot.result.metadata.as_dict(),
                    "glossaryVersion": corrected.glossary_version,
                    "corrections": list(corrected.glossary_corrections),
                    "latencyMs": corrected.latency_ms,
                    "sequenceStart": corrected.sequence_start,
                    "sequenceEnd": corrected.sequence_end,
                    "startMs": corrected.start_ms, "endMs": corrected.end_ms,
                })
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
    openai_session = _openai_live_sessions.pop(session_id, None)
    if openai_session is not None:
        try:
            await openai_session.close()
        except Exception:
            pass
    _openai_pending_boundaries.pop(session_id, None)
    for key in [item for item in _openai_item_boundaries if item[0] == session_id]:
        _openai_item_boundaries.pop(key, None)
    if _live_processing_worker is not None:
        await _live_processing_worker.cancel_session(session_id)
    if not _runtime_settings.live_accurate_final_enabled:
        _live_state_registry.remove(session_id)


@router.post("/api/live/sessions", response_model=LiveSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(payload: CreateLiveSessionRequest, principal: Principal = Depends(require_principal)) -> LiveSessionResponse:
    _enforce_rate("session_create", principal, _runtime_settings.rate_session_create_per_minute)
    try:
        enforce_concurrent_limit(count_active_sessions(), _runtime_settings.limit_concurrent_sessions)
        enforce_concurrent_limit(count_active_sessions(owner_id=principal.user_id), _runtime_settings.limit_concurrent_sessions)
    except HTTPException:
        audit_event("rate_limit_rejection", principal=principal, outcome="rejected", metadata={"category": "concurrent_sessions"})
        raise
    session = create_live_session(payload, owner_id=principal.user_id)
    audit_event("session_start", principal=principal, session_id=session.session_id)
    now = session.created_at
    _persist("session", {
        "sessionId": session.session_id, "status": session.status,
        "sourceType": "live_microphone", "sourceLanguage": session.language,
        "targetLanguage": _runtime_settings.live_translation_target_language if _runtime_settings.live_translation_enabled else None,
        "startedAt": now, "endedAt": None, "createdAt": now, "updatedAt": now,
        "featureFlags": {
            "pcm": _runtime_settings.live_pcm_streaming_enabled,
            "vad": _runtime_settings.live_vad_enabled,
            "accurateFinal": _runtime_settings.live_accurate_final_enabled,
            "translation": _runtime_settings.live_translation_enabled,
            "translationQuality": _runtime_settings.live_translation_quality_enabled,
            "diarization": _runtime_settings.live_diarization_enabled,
            "transcriptPostprocess": _runtime_settings.live_transcript_postprocess_enabled,
        },
        "configuration": {
            "liveModel": session.model, "finalModel": _runtime_settings.live_final_model,
            "providerSnapshot": {
                "live": _runtime_settings.live_transcription_provider,
                "accurateFinal": _runtime_settings.live_final_provider,
                "externalAudio": "openai" in {
                    _runtime_settings.live_transcription_provider,
                    _runtime_settings.live_final_provider,
                },
                "consent": _runtime_settings.openai_external_audio_consent,
            },
            "translationModel": _runtime_settings.live_translation_model,
            "diarizationModel": _runtime_settings.live_diarization_model,
        },
        "hardware": {"platform": platform.platform(), "processor": platform.processor()},
    })
    return session


@router.get("/api/live/sessions", response_model=list[LiveSessionResponse])
def get_sessions(limit: int = Query(default=20, ge=1, le=100), principal: Principal = Depends(require_principal)) -> list[LiveSessionResponse]:
    return list_live_sessions(limit, owner_id=None if principal.is_admin else principal.user_id)


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


def production_pipeline_readiness() -> tuple[bool, bool, bool]:
    worker = _live_processing_worker
    health = worker.health() if worker is not None else {}
    worker_ready = bool(health.get("ready"))
    queue_ready = worker is not None and int(health.get("queueDepth", 0)) < int(health.get("capacity", 1))
    persistence_ready = not _runtime_settings.live_pipeline_persistence_enabled or (
        _persistence_service is not None and int(_persistence_service.metrics().get("degraded_sessions", 0)) == 0
    )
    return worker_ready, queue_ready, persistence_ready


@router.get("/api/live/workers/health")
def processing_worker_health(principal: Principal = Depends(require_principal)) -> dict[str, object]:
    require_admin(principal)
    workers = {
        "live_transcription": _get_live_processing_worker().health(),
        "accurate_final_transcription": _specialized_worker_health("accurate_final_transcription", _final_queue, _runtime_settings.live_accurate_final_enabled),
        "translation": _specialized_worker_health("translation", _translation_queue, _runtime_settings.live_translation_enabled),
        "translation_quality": _specialized_worker_health("translation_quality", _translation_quality_queue, _runtime_settings.live_translation_quality_enabled),
        "diarization": _specialized_worker_health("diarization", _diarization_queue, _runtime_settings.live_diarization_enabled),
        "transcript_postprocessing": _specialized_worker_health("transcript_postprocessing", _transcript_postprocess_queue, _runtime_settings.live_transcript_postprocess_enabled),
    }
    return {"ready": all(bool(worker["ready"]) for worker in workers.values()), "workers": workers}


@router.get("/api/live/monitoring")
def live_monitoring(session_id: str | None = Query(default=None), principal: Principal = Depends(require_principal)) -> dict[str, object]:
    _enforce_rate("monitoring", principal, _runtime_settings.rate_monitoring_per_minute, session_id=session_id)
    if session_id:
        authorize_owner(principal, get_live_session_owner(session_id))
    else:
        require_admin(principal)
    audit_event("monitoring_access", principal=principal, session_id=session_id)
    health = processing_worker_health(principal)
    sessions = list_live_sessions(100)
    persistence = _persistence_service.metrics() if _persistence_service is not None else {}
    session_metrics: dict[str, object] | None = None
    if session_id:
        pcm = _pcm_registry.metrics(session_id)
        transcript = _live_state_registry.metrics(session_id)
        session_metrics = {
            "audioDurationReceivedSeconds": pcm.get("audio_duration_received_seconds", 0),
            "chunksAcknowledged": pcm.get("chunks_acknowledged", 0), "chunksLost": pcm.get("chunks_lost", 0),
            "duplicateChunks": pcm.get("duplicate_chunks", 0), "outOfOrderChunks": pcm.get("out_of_order_chunks", 0),
            "segmentCount": len(_live_state_registry.snapshot(session_id)),
            "latency": latency_summary([transcript.get("partial_latency_ms",0), transcript.get("stable_latency_ms",0), transcript.get("final_latency_ms",0)]),
            "detectedSpeakers": _diarization_queue.state.clusterer.speaker_count(session_id) if _diarization_queue else 0,
        }
        session_metrics["quality"] = quality_indicators({"segmentCount":session_metrics["segmentCount"],"chunksLost":session_metrics["chunksLost"],"chunksSent":pcm.get("chunks_received",0),"audioSeconds":session_metrics["audioDurationReceivedSeconds"]})
    response = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "system": {"activeSessions": sum(item.status in {"active","paused"} for item in sessions), "totalSessions": len(sessions), "connectedClients": len(_connections), "workerReadiness": health["ready"], "resources": resource_metrics(), "degradedPersistenceSessions": persistence.get("degraded_sessions",0)},
        "workers": health["workers"], "persistence": persistence,
        "warnings": warnings_for(health["workers"], persistence_degraded=int(persistence.get("degraded_sessions",0))),
        "session": session_metrics,
    }
    return redact_metrics(response)


@router.post("/api/live/glossary/reload")
def reload_live_glossary(principal: Principal = Depends(require_principal)) -> dict[str, object]:
    require_admin(principal)
    _enforce_rate("glossary_reload", principal, _runtime_settings.rate_glossary_reload_per_minute)
    manager = _get_glossary_manager()
    snapshot = manager.reload()
    audit_event("glossary_reload", principal=principal, metadata={"version": snapshot.version})
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
    principal: Principal = Depends(require_principal),
) -> dict[str, object]:
    authorize_owner(principal, get_live_session_owner(session_id))
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
    persistence = _get_persistence_service()
    if persistence is not None:
        await persistence.rename_speaker(session_id, speaker_id, response["label"])
    audit_event("speaker_rename", principal=principal, session_id=session_id, metadata={"speakerId": speaker_id})
    await _send_to_current_connection(
        session_id,
        _event("diarization_snapshot", **response),
    )
    return response


@router.get("/api/live/sessions/{session_id}", response_model=LiveSessionResponse)
def get_session(session_id: str, principal: Principal = Depends(require_principal)) -> LiveSessionResponse:
    authorize_owner(principal, get_live_session_owner(session_id))
    return get_live_session(session_id)


@router.delete("/api/live/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: str, principal: Principal = Depends(require_principal)) -> Response:
    authorize_owner(principal, get_live_session_owner(session_id))
    delete_live_session(session_id)
    _live_state_registry.remove(session_id)
    for key in [item for item in _segment_glossaries if item[0] == session_id]:
        _segment_glossaries.pop(key, None)
    audit_event("session_delete", principal=principal, session_id=session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/live/sessions/{session_id}/stop", response_model=LiveSessionResponse)
async def stop_session(session_id: str, principal: Principal = Depends(require_principal)) -> LiveSessionResponse:
    authorize_owner(principal, get_live_session_owner(session_id))
    await _finish_pcm_session(session_id)
    session = await asyncio.to_thread(stop_live_session, session_id)
    _persist("session", {
        "sessionId": session.session_id, "status": session.status,
        "endedAt": session.ended_at, "updatedAt": session.updated_at,
        "qualityMetrics": {
            "translationQuality": _translation_quality_queue.metrics() if _translation_quality_queue is not None else None,
            "transcriptPostprocess": _transcript_postprocess_queue.metrics() if _transcript_postprocess_queue is not None else None,
            "diarization": _diarization_queue.metrics() if _diarization_queue is not None else None,
            "persistence": _persistence_service.metrics() if _persistence_service is not None else None,
        },
    })
    async with _connections_lock:
        websocket = _connections.pop(session_id, None)
    if websocket is not None:
        try:
            await websocket.send_json(_event("final", session))
            await websocket.send_json(_event("stopped", session))
            await websocket.close(code=1000)
        except RuntimeError:
            pass
    audit_event("session_stop", principal=principal, session_id=session_id)
    _reconnect_attempts.pop(session_id, None)
    return session


@router.websocket("/ws/live/{session_id}")
async def live_websocket(websocket: WebSocket, session_id: str) -> None:
    if not is_allowed_web_origin(websocket.headers.get("origin")):
        audit_event("auth_failure", outcome="rejected", metadata={"transport": "websocket", "reason": "origin"})
        await websocket.close(code=1008, reason="WebSocket origin is not allowed")
        return
    try:
        principal = websocket_principal(websocket)
    except HTTPException:
        try:
            audit_event("auth_failure", outcome="rejected", metadata={"transport": "websocket"})
        except Exception:
            pass
        await websocket.close(code=1008, reason="WebSocket authentication failed")
        return
    try:
        _enforce_rate("websocket_connect", principal, _runtime_settings.rate_websocket_connect_per_minute, session_id=session_id)
        authorize_owner(principal, await asyncio.to_thread(get_live_session_owner, session_id))
    except HTTPException:
        await websocket.close(code=1008, reason="WebSocket access denied")
        return
    attempts = _reconnect_attempts.get(session_id, -1) + 1
    _reconnect_attempts[session_id] = attempts
    if attempts > _runtime_settings.limit_reconnect_attempts:
        audit_event("rate_limit_rejection", principal=principal, session_id=session_id, outcome="rejected", metadata={"category": "reconnect"})
        await websocket.close(code=1008, reason="Reconnect limit reached")
        return
    requested_protocols = [item.strip().lower() for item in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    await websocket.accept(subprotocol="bearer" if requested_protocols and requested_protocols[0] == "bearer" else None)
    try:
        session = await asyncio.to_thread(get_live_session, session_id)
    except Exception as exc:
        await websocket.send_json(_event("error", message=safe_error(exc)))
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
    last_client_activity = monotonic()
    try:
        while True:
            if elapsed_session_seconds(session.started_at) > _runtime_settings.limit_session_duration_seconds:
                await _finish_pcm_session(session_id)
                await asyncio.to_thread(stop_live_session, session_id)
                audit_event("session_stop", principal=principal, session_id=session_id, metadata={"reason": "duration_limit"})
                _reconnect_attempts.pop(session_id, None)
                await websocket.close(code=1000, reason="Session duration limit reached")
                return
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout=_runtime_settings.websocket_heartbeat_seconds)
            except asyncio.TimeoutError:
                if websocket_idle_expired(last_client_activity, monotonic(), _runtime_settings.websocket_idle_timeout_seconds):
                    await _finish_pcm_session(session_id)
                    await asyncio.to_thread(stop_live_session, session_id)
                    audit_event("session_stop", principal=principal, session_id=session_id, metadata={"reason": "idle_timeout"})
                    _reconnect_attempts.pop(session_id, None)
                    await websocket.close(code=1001, reason="Idle timeout")
                    return
                await websocket.send_json(_event("heartbeat", timestamp=datetime.now(timezone.utc).isoformat()))
                continue
            last_client_activity = monotonic()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1006))

            audio = message.get("bytes")
            if audio is not None:
                if not audio:
                    await websocket.send_json(_event("error", message="Audio chunk is empty"))
                    continue
                try:
                    validate_audio_frame_size(len(audio), _runtime_settings.limit_audio_chunk_bytes)
                except ValueError as exc:
                    await websocket.send_json(_event("error", message=str(exc)))
                    continue
                if not allow_bursty_throughput(
                    "audio_throughput",
                    principal.user_id,
                    _runtime_settings.rate_audio_bytes_per_second,
                    cost=len(audio),
                    maximum_burst=_runtime_settings.limit_audio_chunk_bytes,
                ):
                    audit_event("rate_limit_rejection", principal=principal, session_id=session_id, outcome="rejected", metadata={"category": "audio_throughput"})
                    await websocket.close(code=1008, reason="Audio throughput limit reached")
                    return
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
                        if _runtime_settings.live_transcription_provider == "openai":
                            try:
                                provider_session = await _ensure_openai_live_session(session_id)
                                await provider_session.append_pcm16(audio, sample_rate=metadata.sample_rate)
                            except Exception as exc:
                                if not _runtime_settings.openai_allow_local_fallback:
                                    await websocket.send_json(
                                        _event("error", message=safe_error(exc), provider="openai", fallback=False)
                                    )
                                    continue
                        await _schedule_pcm_transcription(session_id)
                    continue
                await websocket.send_json(_event("processing"))
                try:
                    session, duplicate = await asyncio.to_thread(process_live_chunk, session_id, audio)
                except HTTPException as exc:
                    current = await asyncio.to_thread(get_live_session, session_id)
                    await websocket.send_json(_event("error", current, message=str(exc.detail)))
                    continue
                except Exception as exc:
                    failed = await asyncio.to_thread(fail_live_session, session_id, safe_error(exc))
                    await websocket.send_json(_event("error", failed, message=safe_error(exc)))
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
                    if _runtime_settings.live_transcription_provider == "openai":
                        if not (_runtime_settings.live_vad_enabled and _runtime_settings.live_transcript_state_enabled):
                            await websocket.send_json(
                                _event("error", message="OpenAI live transcription requires PCM VAD and semantic transcript state")
                            )
                            continue
                        try:
                            await _ensure_openai_live_session(session_id)
                        except Exception as exc:
                            if not _runtime_settings.openai_allow_local_fallback:
                                await websocket.send_json(
                                    _event("error", message=safe_error(exc), provider="openai", fallback=False)
                                )
                                continue
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
                            provider=_runtime_settings.live_transcription_provider,
                            privacyWarning=(
                                "Audio is sent to OpenAI for transcription."
                                if _runtime_settings.live_transcription_provider == "openai"
                                else None
                            ),
                        )
                    )
                    if (
                        _runtime_settings.live_vad_enabled
                        and _runtime_settings.live_transcript_state_enabled
                    ):
                        runtime_updates = [
                            update.as_dict()
                            for update in _live_state_registry.snapshot(session_id)
                        ]
                        if not runtime_updates:
                            persistence = _get_persistence_service()
                            if persistence is not None:
                                try:
                                    restored = await persistence.restore(session_id)
                                    latest: dict[str, dict] = {}
                                    for item in restored.get("transcriptRevisions", []):
                                        current = latest.get(item["segmentId"])
                                        if current is None or item["revision"] > current["revision"]:
                                            latest[item["segmentId"]] = item
                                    runtime_updates = [
                                        {
                                            "sessionId": item["sessionId"], "segmentId": item["segmentId"],
                                            "revision": item["revision"], "state": item["state"],
                                            "sequenceStart": item.get("sequenceStart", 0), "sequenceEnd": item.get("sequenceEnd", 0),
                                            "startMs": item.get("startMs", 0), "endMs": item.get("endMs", 0),
                                            "text": item.get("postProcessedText") or item.get("glossaryCorrectedText") or item.get("rawText", ""),
                                            "rawText": item.get("rawText"), "language": item.get("language", "auto"),
                                            "model": item.get("modelMetadata", {}).get("model", "base"),
                                            "latencyMs": item.get("latencyMs", 0),
                                            "glossaryVersion": item.get("glossaryVersion"),
                                            "glossaryCorrections": item.get("corrections", []),
                                        }
                                        for item in latest.values()
                                    ]
                                except Exception:
                                    runtime_updates = []
                        await websocket.send_json(
                            _event(
                                "transcript_state_snapshot",
                                updates=runtime_updates,
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
                elif command_type == "ping":
                    await websocket.send_json(_event("pong", timestamp=datetime.now(timezone.utc).isoformat()))
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
                    audit_event("session_stop", principal=principal, session_id=session_id)
                    _reconnect_attempts.pop(session_id, None)
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
        pass
    finally:
        await asyncio.to_thread(record_disconnect, session_id)
        async with _connections_lock:
            if _connections.get(session_id) is websocket:
                _connections.pop(session_id, None)

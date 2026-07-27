import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status

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
from ..services.live_transcript_state import (
    LiveTranscriptStateRegistry,
    LiveTranscriptUpdate,
    TranscriptState,
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


async def shutdown_final_transcription_queue() -> None:
    global _final_queue
    queue, _final_queue = _final_queue, None
    if queue is not None:
        await queue.close()


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
        try:
            detailed: PcmTranscriptionResult | None = None
            if _runtime_settings.live_transcript_state_enabled:
                detailed = await asyncio.to_thread(
                    transcribe_pcm_window_detailed,
                    session_id,
                    segment.window,
                )
                session, duplicate = detailed.session, detailed.duplicate
            else:
                session, duplicate = await asyncio.to_thread(
                    transcribe_pcm_window,
                    session_id,
                    segment.window,
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
            await _send_live_transcript_lifecycle(session_id, detailed)
            if _runtime_settings.live_accurate_final_enabled:
                await _enqueue_accurate_final(session_id, detailed, segment.window)
    return True


async def _send_live_transcript_lifecycle(
    session_id: str,
    result: PcmTranscriptionResult,
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
            ),
        )


async def _enqueue_accurate_final(
    session_id: str,
    live_result: PcmTranscriptionResult,
    window: PcmAudioWindow,
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
    )
    try:
        snapshot, duplicate = await queue.enqueue(request, _handle_final_job_status)
    except asyncio.QueueFull as exc:
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
            )
            outcome = _live_state_registry.replace_with_accurate_final(corrected)
            if outcome.accepted:
                queue.record_replacement()
                payload["update"] = corrected.as_dict()
            else:
                payload["status"] = FinalJobStatus.FAILED.value
                payload["error"] = outcome.reason
    await _send_to_current_connection(
        snapshot.session_id,
        _event("final_correction", **payload, metrics=queue.metrics()),
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
    if not _runtime_settings.live_accurate_final_enabled:
        _live_state_registry.remove(session_id)


@router.post("/api/live/sessions", response_model=LiveSessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(payload: CreateLiveSessionRequest) -> LiveSessionResponse:
    return create_live_session(payload)


@router.get("/api/live/sessions", response_model=list[LiveSessionResponse])
def get_sessions(limit: int = Query(default=20, ge=1, le=100)) -> list[LiveSessionResponse]:
    return list_live_sessions(limit)


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

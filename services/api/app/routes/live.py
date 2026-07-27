import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from ..models.live import CreateLiveSessionRequest, LiveSessionResponse
from ..security import is_allowed_web_origin
from ..services.live_processor import process_live_chunk
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

router = APIRouter(tags=["live"])
_connections: dict[str, WebSocket] = {}
_connections_lock = asyncio.Lock()
MAX_LIVE_CHUNK_BYTES = 10 * 1024 * 1024


def _event(event_type: str, session: LiveSessionResponse | None = None, **extra: object) -> dict:
    payload = {"type": event_type, **extra}
    if session is not None:
        payload["session"] = session.model_dump(mode="json")
    return payload


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

            command_type = command.get("type")
            try:
                if command_type == "pause":
                    session = await asyncio.to_thread(pause_live_session, session_id)
                    await websocket.send_json(_event("connected", session))
                elif command_type == "resume":
                    session = await asyncio.to_thread(resume_live_session, session_id)
                    await websocket.send_json(_event("connected", session))
                elif command_type == "stop":
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
    except WebSocketDisconnect:
        await asyncio.to_thread(record_disconnect, session_id)
    finally:
        async with _connections_lock:
            if _connections.get(session_id) is websocket:
                _connections.pop(session_id, None)

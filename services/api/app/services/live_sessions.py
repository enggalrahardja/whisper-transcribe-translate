from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, status
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from ..database import get_database
from ..models.live import CreateLiveSessionRequest, LiveSessionResponse
from .whisper_models import whisper_model_usage

COLLECTION_NAME = "live_sessions"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def ensure_live_session_indexes() -> None:
    collection = get_database()[COLLECTION_NAME]
    collection.create_index([("session_id", ASCENDING)], unique=True, name="unique_live_session_id")
    collection.create_index([("created_at", DESCENDING)])


def _serialize(document: dict) -> LiveSessionResponse:
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
    )


def _get_document(session_id: str) -> dict:
    document = get_database()[COLLECTION_NAME].find_one({"session_id": session_id})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Live session not found")
    return document


def create_live_session(payload: CreateLiveSessionRequest) -> LiveSessionResponse:
    with whisper_model_usage(payload.model, "live-session-create"):
        now = utc_now()
        document = {
            "session_id": uuid4().hex,
            "status": "active",
            "language": payload.language.strip().lower(),
            "model": payload.model,
            "started_at": now,
            "ended_at": None,
            "duration": 0.0,
            "partial_text": "",
            "final_text": "",
            "segments": [],
            "error": None,
            "audio_cursor": 0.0,
            "processed_chunk_hashes": [],
            "paused_seconds": 0.0,
            "created_at": now,
            "updated_at": now,
        }
        get_database()[COLLECTION_NAME].insert_one(document)
    return _serialize(document)


def get_live_session(session_id: str) -> LiveSessionResponse:
    return _serialize(_get_document(session_id))


def list_live_sessions(limit: int = 20) -> list[LiveSessionResponse]:
    documents = get_database()[COLLECTION_NAME].find().sort("created_at", DESCENDING).limit(limit)
    return [_serialize(document) for document in documents]


def claim_live_chunk(session_id: str, chunk_hash: str) -> tuple[dict, bool]:
    collection = get_database()[COLLECTION_NAME]
    now = utc_now()
    document = collection.find_one_and_update(
        {
            "session_id": session_id,
            "status": "active",
            "processed_chunk_hashes": {"$ne": chunk_hash},
        },
        {
            "$addToSet": {"processed_chunk_hashes": chunk_hash},
            "$set": {"updated_at": now},
        },
        return_document=ReturnDocument.AFTER,
    )
    if document is not None:
        return document, True

    current = _get_document(session_id)
    if chunk_hash in current.get("processed_chunk_hashes", []):
        return current, False
    if current["status"] == "paused":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Live session is paused")
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Live session is already finished")


def append_live_result(
    session_id: str,
    chunk_hash: str,
    partial_text: str,
    segments: list[dict],
    chunk_duration: float,
) -> LiveSessionResponse:
    now = utc_now()
    document = get_database()[COLLECTION_NAME].find_one_and_update(
        {"session_id": session_id, "status": "active", "processed_chunk_hashes": chunk_hash},
        {
            "$set": {
                "partial_text": partial_text,
                "updated_at": now,
            },
            "$push": {"segments": {"$each": segments}},
            "$inc": {"audio_cursor": max(0.0, chunk_duration)},
        },
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        return get_live_session(session_id)
    return _serialize(document)


def pause_live_session(session_id: str) -> LiveSessionResponse:
    now = utc_now()
    document = get_database()[COLLECTION_NAME].find_one_and_update(
        {"session_id": session_id, "status": "active"},
        {"$set": {"status": "paused", "paused_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        current = _get_document(session_id)
        if current["status"] != "paused":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Live session is already finished")
        document = current
    return _serialize(document)


def resume_live_session(session_id: str) -> LiveSessionResponse:
    now = utc_now()
    current = _get_document(session_id)
    if current["status"] == "active":
        return _serialize(current)
    if current["status"] != "paused":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Live session is already finished")
    paused_at = _as_utc(current.get("paused_at") or now)
    paused_seconds = max(0.0, (now - paused_at).total_seconds())
    document = get_database()[COLLECTION_NAME].find_one_and_update(
        {"session_id": session_id, "status": "paused"},
        {
            "$set": {"status": "active", "updated_at": now},
            "$inc": {"paused_seconds": paused_seconds},
            "$unset": {"paused_at": ""},
        },
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(document or _get_document(session_id))


def stop_live_session(session_id: str) -> LiveSessionResponse:
    current = _get_document(session_id)
    if current["status"] in {"completed", "failed"}:
        return _serialize(current)

    now = utc_now()
    paused_seconds = float(current.get("paused_seconds", 0))
    if current["status"] == "paused" and current.get("paused_at"):
        paused_seconds += max(0.0, (now - _as_utc(current["paused_at"])).total_seconds())
    duration = max(0.0, (now - _as_utc(current["started_at"])).total_seconds() - paused_seconds)
    document = get_database()[COLLECTION_NAME].find_one_and_update(
        {"session_id": session_id, "status": {"$in": ["active", "paused"]}},
        {
            "$set": {
                "status": "completed",
                "final_text": current.get("partial_text", ""),
                "ended_at": now,
                "duration": duration,
                "paused_seconds": paused_seconds,
                "updated_at": now,
            },
            "$unset": {"paused_at": ""},
        },
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(document or _get_document(session_id))


def fail_live_session(session_id: str, error: str) -> LiveSessionResponse:
    now = utc_now()
    current = _get_document(session_id)
    if current["status"] in {"completed", "failed"}:
        return _serialize(current)
    duration = max(0.0, (now - _as_utc(current["started_at"])).total_seconds() - float(current.get("paused_seconds", 0)))
    document = get_database()[COLLECTION_NAME].find_one_and_update(
        {"session_id": session_id, "status": {"$in": ["active", "paused"]}},
        {
            "$set": {
                "status": "failed",
                "error": error,
                "final_text": current.get("partial_text", ""),
                "ended_at": now,
                "duration": duration,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(document or _get_document(session_id))


def record_disconnect(session_id: str) -> None:
    get_database()[COLLECTION_NAME].update_one(
        {"session_id": session_id, "status": {"$in": ["active", "paused"]}},
        {"$set": {"last_disconnected_at": utc_now(), "updated_at": utc_now()}},
    )

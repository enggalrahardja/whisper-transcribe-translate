import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from bson import ObjectId
from fastapi import HTTPException, status
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from ..config import get_settings
from ..database import get_database
from ..models.subtitle import (
    CreateSubtitleProjectRequest,
    SubtitleProjectResponse,
    SubtitleSegment,
    UpdateSubtitleProjectRequest,
)
from .jobs import COLLECTION_NAME as JOBS_COLLECTION
from .media_files import COLLECTION_NAME as MEDIA_COLLECTION
from .transcripts import COLLECTION_NAME as TRANSCRIPTS_COLLECTION
from .storage import resolve_storage_file

COLLECTION_NAME = "subtitle_projects"
BURNS_COLLECTION = "subtitle_burn_jobs"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_subtitle_project_indexes() -> None:
    collection = get_database()[COLLECTION_NAME]
    collection.create_index([("project_id", ASCENDING)], unique=True, name="unique_subtitle_project_id")
    collection.create_index([("created_at", DESCENDING)])


def normalize_segments(segments: list[SubtitleSegment | dict]) -> list[dict]:
    normalized: list[dict] = []
    for sequence, raw_segment in enumerate(segments, start=1):
        segment = raw_segment if isinstance(raw_segment, SubtitleSegment) else SubtitleSegment.model_validate(raw_segment)
        normalized.append(
            {
                "sequence": sequence,
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": segment.text.strip(),
                "duration": round(float(segment.end - segment.start), 3),
            }
        )
    for current, following in zip(normalized, normalized[1:]):
        if following["start"] < current["end"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Segment {following['sequence']} overlaps segment {current['sequence']}",
            )
    return normalized


def _serialize(document: dict) -> SubtitleProjectResponse:
    return SubtitleProjectResponse(
        project_id=document["project_id"],
        job_id=str(document["job_id"]),
        media_file_id=str(document["media_file_id"]),
        source_type=document["source_type"],
        language=document["language"],
        segments=document.get("segments", []),
        version=document["version"],
        file_name=document["file_name"],
        media_type=document["media_type"],
        content_type=document.get("content_type"),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def get_subtitle_document(project_id: str) -> dict:
    document = get_database()[COLLECTION_NAME].find_one({"project_id": project_id})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subtitle project not found")
    return document


def _source_segments(transcript: dict, source_type: str) -> tuple[str, list[dict]]:
    if source_type == "translation_original":
        return (
            str(transcript.get("source_language") or transcript.get("language") or "unknown"),
            transcript.get("original_segments") or transcript.get("segments") or [],
        )
    if source_type == "translation_translated":
        language = str(transcript.get("target_language") or "unknown")
        translated_segments = transcript.get("translated_segments") or []
        if translated_segments:
            return language, translated_segments
        translated_text = str(transcript.get("translated_text") or transcript.get("text") or "").strip()
        original_segments = transcript.get("original_segments") or transcript.get("segments") or []
        if not translated_text:
            return language, []
        start = float(original_segments[0].get("start", 0)) if original_segments else 0.0
        end = max((float(segment.get("end", 0)) for segment in original_segments), default=start + 2.0)
        return language, [{"start": start, "end": max(end, start + 0.001), "text": translated_text}]
    return str(transcript.get("language") or "unknown"), transcript.get("segments") or []


def create_subtitle_project(payload: CreateSubtitleProjectRequest) -> SubtitleProjectResponse:
    if not ObjectId.is_valid(payload.job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completed job not found")
    database = get_database()
    job_id = ObjectId(payload.job_id)
    job = database[JOBS_COLLECTION].find_one({"_id": job_id, "status": "completed"})
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completed job not found")
    if payload.source_type.startswith("translation_") and job.get("task") != "translate":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Translation source requires a translate job")
    transcript = database[TRANSCRIPTS_COLLECTION].find_one({"job_id": job_id})
    if transcript is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript result not found")
    media_file_id = job.get("media_file_id")
    media = database[MEDIA_COLLECTION].find_one({"_id": media_file_id}) if isinstance(media_file_id, ObjectId) else None
    if media is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media file not found")

    language, source_segments = _source_segments(transcript, payload.source_type)
    prepared_segments = [
        SubtitleSegment(
            sequence=index,
            start=max(0.0, float(segment.get("start", 0))),
            end=float(segment.get("end", 0)),
            text=str(segment.get("text", "")),
        )
        for index, segment in enumerate(source_segments, start=1)
        if float(segment.get("end", 0)) > max(0.0, float(segment.get("start", 0)))
    ]
    now = utc_now()
    document = {
        "project_id": uuid4().hex,
        "job_id": job_id,
        "media_file_id": media_file_id,
        "source_type": payload.source_type,
        "language": language,
        "segments": normalize_segments(prepared_segments),
        "version": 1,
        "file_name": str(job.get("file_name") or media.get("original_name") or "subtitle"),
        "media_type": str(job.get("media_type") or media.get("media_type") or "audio"),
        "content_type": media.get("content_type"),
        "created_at": now,
        "updated_at": now,
    }
    database[COLLECTION_NAME].insert_one(document)
    return _serialize(document)


def list_subtitle_projects(limit: int = 100) -> list[SubtitleProjectResponse]:
    documents = get_database()[COLLECTION_NAME].find().sort("updated_at", DESCENDING).limit(limit)
    return [_serialize(document) for document in documents]


def get_subtitle_project(project_id: str) -> SubtitleProjectResponse:
    return _serialize(get_subtitle_document(project_id))


def update_subtitle_project(project_id: str, payload: UpdateSubtitleProjectRequest) -> SubtitleProjectResponse:
    segments = normalize_segments(payload.segments)
    document = get_database()[COLLECTION_NAME].find_one_and_update(
        {"project_id": project_id, "version": payload.version},
        {"$set": {"segments": segments, "updated_at": utc_now()}, "$inc": {"version": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if document is not None:
        return _serialize(document)
    current = get_database()[COLLECTION_NAME].find_one({"project_id": project_id}, {"version": 1})
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subtitle project not found")
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Version conflict: current version is {current['version']}",
    )


def delete_subtitle_project(project_id: str) -> bool:
    database = get_database()
    active_burn = database[BURNS_COLLECTION].find_one(
        {"project_id": project_id, "status": {"$in": ["queued", "processing"]}}, {"_id": 1}
    )
    if active_burn is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot delete a project while subtitle burn is active")
    document = database[COLLECTION_NAME].find_one_and_delete({"project_id": project_id})
    if document is None:
        return False
    burns = list(database[BURNS_COLLECTION].find({"project_id": project_id}, {"output_path": 1}))
    storage_root = Path(get_settings().storage_root).resolve()
    for burn in burns:
        if burn.get("output_path"):
            output_path = Path(burn["output_path"]).resolve()
            if output_path.is_relative_to(storage_root):
                output_path.unlink(missing_ok=True)
    database[BURNS_COLLECTION].delete_many({"project_id": project_id})
    return True


def safe_export_name(file_name: str, project_id: str, extension: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(file_name).stem).strip(".-") or "subtitle"
    return f"{stem}-{project_id[:8]}.{extension}"


def format_subtitle_timestamp(seconds: float, separator: str) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{milliseconds:03d}"


def render_subtitle(document: dict, export_format: str) -> str:
    segments = normalize_segments(document.get("segments", []))
    if export_format == "txt":
        return "\n".join(segment["text"] for segment in segments) + ("\n" if segments else "")
    blocks: list[str] = []
    separator = "," if export_format == "srt" else "."
    for segment in segments:
        timing = (
            f"{format_subtitle_timestamp(segment['start'], separator)} --> "
            f"{format_subtitle_timestamp(segment['end'], separator)}"
        )
        prefix = f"{segment['sequence']}\n" if export_format == "srt" else ""
        blocks.append(f"{prefix}{timing}\n{segment['text']}")
    content = "\n\n".join(blocks) + ("\n" if blocks else "")
    return f"WEBVTT\n\n{content}" if export_format == "vtt" else content


def get_project_media(project_id: str) -> tuple[dict, Path]:
    project = get_subtitle_document(project_id)
    media = get_database()[MEDIA_COLLECTION].find_one({"_id": project["media_file_id"]})
    if media is None or not media.get("stored_path"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media file not found")
    try:
        path = resolve_storage_file(media["stored_path"])
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media file is missing on disk")
    return media, path

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException, status
from pymongo import ASCENDING

from ..database import get_database
from ..models.settings import DeleteLocalFileResponse, LocalFileResponse
from .storage import resolve_storage_file

COLLECTION_NAME = "media_files"


def ensure_media_file_indexes() -> None:
    get_database()[COLLECTION_NAME].create_index([("created_at", ASCENDING)])


def create_media_file(media: dict[str, str | int]) -> dict:
    document = {
        "original_name": media["file_name"],
        "stored_name": media["stored_name"],
        "stored_path": media["storage_path"],
        "file_size": media["file_size"],
        "content_type": media["content_type"],
        "media_type": media["media_type"],
        "created_at": datetime.now(timezone.utc),
    }
    result = get_database()[COLLECTION_NAME].insert_one(document)
    document["_id"] = result.inserted_id
    return document


def get_media_file(media_file_id: ObjectId) -> dict | None:
    return get_database()[COLLECTION_NAME].find_one({"_id": media_file_id})


def _object_id_or_404(media_file_id: str) -> ObjectId:
    if not ObjectId.is_valid(media_file_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local file not found")
    return ObjectId(media_file_id)


def _file_usage(database, media_file_id: ObjectId) -> tuple[int, int, int]:
    jobs = database["transcription_jobs"]
    job_count = jobs.count_documents({"media_file_id": media_file_id})
    active_job_count = jobs.count_documents({
        "media_file_id": media_file_id,
        "status": {"$in": ["queued", "processing"]},
    })
    subtitle_project_count = database["subtitle_projects"].count_documents({"media_file_id": media_file_id})
    return job_count, active_job_count, subtitle_project_count


def list_local_files(limit: int = 500) -> list[LocalFileResponse]:
    database = get_database()
    results: list[LocalFileResponse] = []
    cursor = database[COLLECTION_NAME].find({"stored_path": {"$nin": [None, ""]}}).sort("created_at", -1).limit(limit)
    for media in cursor:
        try:
            path = resolve_storage_file(media["stored_path"], must_exist=False)
        except (KeyError, TypeError, ValueError):
            continue
        if not path.is_file():
            continue
        job_count, active_job_count, subtitle_project_count = _file_usage(database, media["_id"])
        protection_reason = None
        if active_job_count:
            protection_reason = "File is used by a queued or processing job"
        elif subtitle_project_count:
            protection_reason = "File is used by a subtitle project"
        results.append(LocalFileResponse(
            id=str(media["_id"]),
            original_name=str(media.get("original_name") or media.get("stored_name") or media["_id"]),
            media_type=str(media.get("media_type") or "unknown"),
            content_type=media.get("content_type"),
            file_size=int(path.stat().st_size),
            created_at=media["created_at"],
            job_count=job_count,
            active_job_count=active_job_count,
            subtitle_project_count=subtitle_project_count,
            deletable=protection_reason is None,
            protection_reason=protection_reason,
        ))
    return results


def delete_local_file(media_file_id: str) -> DeleteLocalFileResponse:
    object_id = _object_id_or_404(media_file_id)
    database = get_database()
    media_collection = database[COLLECTION_NAME]
    media = media_collection.find_one({"_id": object_id})
    if media is None or not media.get("stored_path"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local file not found")
    _, active_job_count, subtitle_project_count = _file_usage(database, object_id)
    if active_job_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Local file cannot be deleted while its job is queued or processing",
        )
    if subtitle_project_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Local file cannot be deleted while it is used by a subtitle project",
        )
    try:
        path = resolve_storage_file(media["stored_path"], must_exist=False)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Local file path is outside storage") from exc
    try:
        bytes_deleted = path.stat().st_size if path.is_file() else 0
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Local file could not be deleted") from exc
    media_collection.update_one(
        {"_id": object_id, "stored_path": media["stored_path"]},
        {
            "$set": {
                "stored_path": None,
                "local_file_deleted_at": datetime.now(timezone.utc),
            }
        },
    )
    return DeleteLocalFileResponse(
        id=media_file_id,
        original_name=str(media.get("original_name") or media.get("stored_name") or media_file_id),
        bytes_deleted=bytes_deleted,
    )

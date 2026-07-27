from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException, status
from pymongo import DESCENDING, ReturnDocument

from ..database import get_database
from ..models.job import CreateJobRequest, JobResponse, JobStatus
from .media_files import COLLECTION_NAME as MEDIA_FILES_COLLECTION
from .translation_adapter import normalize_target_language
from .transcripts import COLLECTION_NAME as TRANSCRIPTS_COLLECTION
from .storage import resolve_storage_file

SUBTITLE_PROJECTS_COLLECTION = "subtitle_projects"

COLLECTION_NAME = "transcription_jobs"


def ensure_job_indexes() -> None:
    collection = get_database()[COLLECTION_NAME]
    collection.create_index([("created_at", DESCENDING)])
    collection.create_index([("status", 1), ("created_at", 1)])


def _serialize_job(document: dict) -> JobResponse:
    return JobResponse(
        id=str(document["_id"]),
        file_name=document["file_name"],
        media_type=document["media_type"],
        language=document["language"],
        model=document["model"],
        task=document["task"],
        target_language=document.get("target_language"),
        status=document["status"],
        progress=document.get("progress", 0),
        file_size=document.get("file_size"),
        content_type=document.get("content_type"),
        error=document.get("error"),
        cancellation_requested=document.get("cancellation_requested", False),
        worker_id=document.get("worker_id"),
        transcript_id=str(document["transcript_id"]) if document.get("transcript_id") else None,
        started_at=document.get("started_at"),
        completed_at=document.get("completed_at"),
        heartbeat_at=document.get("heartbeat_at"),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def _insert_job(document: dict) -> JobResponse:
    now = datetime.now(timezone.utc)
    document.update(
        status=JobStatus.QUEUED.value,
        progress=0,
        error=None,
        created_at=now,
        updated_at=now,
    )
    result = get_database()[COLLECTION_NAME].insert_one(document)
    document["_id"] = result.inserted_id
    return _serialize_job(document)


def create_job(payload: CreateJobRequest) -> JobResponse:
    document = payload.model_dump()
    if payload.task == "translate":
        try:
            document["target_language"] = normalize_target_language(payload.target_language)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _insert_job(document)


def create_uploaded_job(
    media: dict[str, str | int],
    media_file_id: ObjectId,
    language: str,
    model: str,
    task: str,
    target_language: str | None = None,
) -> JobResponse:
    if task == "translate":
        try:
            target_language = normalize_target_language(target_language)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _insert_job(
        {
            **media,
            "media_file_id": media_file_id,
            "language": language,
            "model": model,
            "task": task,
            "target_language": target_language,
        }
    )


def list_jobs(limit: int = 20) -> list[JobResponse]:
    cursor = (
        get_database()[COLLECTION_NAME]
        .find()
        .sort("created_at", DESCENDING)
        .limit(limit)
    )
    return [_serialize_job(document) for document in cursor]


def get_job(job_id: str) -> JobResponse:
    if not ObjectId.is_valid(job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    document = get_database()[COLLECTION_NAME].find_one({"_id": ObjectId(job_id)})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return _serialize_job(document)


def get_job_summary() -> dict[str, int]:
    collection = get_database()[COLLECTION_NAME]
    summary = {"total": collection.count_documents({})}
    for item in JobStatus:
        summary[item.value] = collection.count_documents({"status": item.value})
    return summary


def _object_id_or_404(job_id: str) -> ObjectId:
    if not ObjectId.is_valid(job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return ObjectId(job_id)


def _current_job_or_404(job_object_id: ObjectId) -> dict:
    document = get_database()[COLLECTION_NAME].find_one({"_id": job_object_id})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return document


def retry_job(job_id: str) -> JobResponse:
    job_object_id = _object_id_or_404(job_id)
    now = datetime.now(timezone.utc)
    collection = get_database()[COLLECTION_NAME]
    document = collection.find_one_and_update(
        {"_id": job_object_id, "status": {"$in": [JobStatus.FAILED.value, JobStatus.CANCELLED.value]}},
        {
            "$set": {
                "status": JobStatus.QUEUED.value,
                "progress": 0,
                "error": None,
                "updated_at": now,
            },
            "$unset": {
                "cancellation_requested": "",
                "worker_id": "",
                "heartbeat_at": "",
                "started_at": "",
                "completed_at": "",
                "transcript_id": "",
                "recovered_at": "",
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if document is not None:
        get_database()[TRANSCRIPTS_COLLECTION].delete_many({"job_id": job_object_id})
        return _serialize_job(document)

    current = _current_job_or_404(job_object_id)
    if current["status"] == JobStatus.QUEUED.value:
        return _serialize_job(current)
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Only failed or cancelled jobs can be retried",
    )


def cancel_job(job_id: str) -> JobResponse:
    job_object_id = _object_id_or_404(job_id)
    now = datetime.now(timezone.utc)
    collection = get_database()[COLLECTION_NAME]

    document = collection.find_one_and_update(
        {"_id": job_object_id, "status": JobStatus.QUEUED.value},
        {
            "$set": {
                "status": JobStatus.CANCELLED.value,
                "progress": 0,
                "error": None,
                "completed_at": now,
                "updated_at": now,
            },
            "$unset": {"cancellation_requested": "", "worker_id": "", "heartbeat_at": ""},
        },
        return_document=ReturnDocument.AFTER,
    )
    if document is not None:
        return _serialize_job(document)

    document = collection.find_one_and_update(
        {"_id": job_object_id, "status": JobStatus.PROCESSING.value},
        {"$set": {"cancellation_requested": True, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if document is not None:
        return _serialize_job(document)

    current = _current_job_or_404(job_object_id)
    if current["status"] == JobStatus.CANCELLED.value:
        return _serialize_job(current)
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Only queued or processing jobs can be cancelled",
    )


def delete_job(job_id: str) -> bool:
    job_object_id = _object_id_or_404(job_id)
    database = get_database()
    jobs = database[COLLECTION_NAME]
    document = jobs.find_one_and_delete(
        {
            "_id": job_object_id,
            "status": {
                "$in": [
                    JobStatus.COMPLETED.value,
                    JobStatus.FAILED.value,
                    JobStatus.CANCELLED.value,
                ]
            },
        }
    )
    if document is None:
        current = jobs.find_one({"_id": job_object_id}, {"status": 1})
        if current is None:
            return False
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Queued or processing jobs cannot be deleted",
        )

    database[TRANSCRIPTS_COLLECTION].delete_many({"job_id": job_object_id})
    media_file_id = document.get("media_file_id")
    if not isinstance(media_file_id, ObjectId):
        return True
    if jobs.count_documents({"media_file_id": media_file_id}, limit=1) > 0:
        return True
    if database[SUBTITLE_PROJECTS_COLLECTION].count_documents({"media_file_id": media_file_id}, limit=1) > 0:
        return True

    media_files = database[MEDIA_FILES_COLLECTION]
    media = media_files.find_one({"_id": media_file_id})
    if media is None:
        return True
    if not media.get("stored_path"):
        media_files.delete_one({"_id": media_file_id})
        return True

    try:
        stored_path = resolve_storage_file(media["stored_path"], must_exist=False)
    except ValueError:
        return True
    if jobs.count_documents({"media_file_id": media_file_id}, limit=1) == 0:
        deleted = media_files.delete_one({"_id": media_file_id})
        if deleted.deleted_count == 1:
            stored_path.unlink(missing_ok=True)
    return True

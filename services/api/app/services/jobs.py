from datetime import datetime, timezone

from bson import ObjectId
from fastapi import HTTPException, status
from pymongo import DESCENDING

from ..database import get_database
from ..models.job import CreateJobRequest, JobResponse, JobStatus

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
        status=document["status"],
        progress=document.get("progress", 0),
        error=document.get("error"),
        created_at=document["created_at"],
        updated_at=document["updated_at"],
    )


def create_job(payload: CreateJobRequest) -> JobResponse:
    now = datetime.now(timezone.utc)
    document = {
        **payload.model_dump(),
        "status": JobStatus.QUEUED.value,
        "progress": 0,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    result = get_database()[COLLECTION_NAME].insert_one(document)
    document["_id"] = result.inserted_id
    return _serialize_job(document)


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

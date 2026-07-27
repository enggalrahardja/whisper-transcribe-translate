from bson import ObjectId
from fastapi import HTTPException, status
from pymongo import ASCENDING

from ..database import get_database
from ..models.job import TranscriptResponse

COLLECTION_NAME = "transcripts"


def ensure_transcript_indexes() -> None:
    get_database()[COLLECTION_NAME].create_index(
        [("job_id", ASCENDING)],
        unique=True,
        name="unique_transcript_per_job",
    )


def _serialize_transcript(document: dict) -> TranscriptResponse:
    return TranscriptResponse(
        id=str(document["_id"]),
        job_id=str(document["job_id"]),
        media_file_id=str(document["media_file_id"]),
        text=document["text"],
        language=document["language"],
        segments=document.get("segments", []),
        original_text=document.get("original_text", document.get("text", "")),
        translated_text=document.get("translated_text"),
        source_language=document.get("source_language", document.get("language")),
        target_language=document.get("target_language"),
        original_segments=document.get("original_segments", document.get("segments", [])),
        translated_segments=document.get("translated_segments"),
        created_at=document["created_at"],
    )


def get_job_result(job_id: str) -> TranscriptResponse:
    if not ObjectId.is_valid(job_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript result not found")

    document = get_database()[COLLECTION_NAME].find_one({"job_id": ObjectId(job_id)})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript result not found")
    return _serialize_transcript(document)

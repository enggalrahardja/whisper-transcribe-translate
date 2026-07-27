from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ASCENDING

from ..database import get_database

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

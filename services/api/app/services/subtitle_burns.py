import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, status
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..config import get_settings
from ..database import get_database
from ..models.subtitle import SubtitleBurnResponse
from .subtitle_projects import (
    BURNS_COLLECTION,
    get_project_media,
    get_subtitle_document,
    render_subtitle,
    safe_export_name,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_subtitle_burn_indexes() -> None:
    collection = get_database()[BURNS_COLLECTION]
    collection.create_index([("burn_id", ASCENDING)], unique=True, name="unique_subtitle_burn_id")
    collection.create_index([("created_at", DESCENDING)])
    collection.create_index([("project_id", ASCENDING), ("created_at", DESCENDING)])
    collection.create_index([("active_key", ASCENDING)], unique=True, sparse=True, name="one_active_burn_per_project")


def recover_interrupted_subtitle_burns() -> int:
    now = utc_now()
    result = get_database()[BURNS_COLLECTION].update_many(
        {"status": {"$in": ["queued", "processing"]}},
        {
            "$set": {
                "status": "failed",
                "error": "Subtitle burn was interrupted by an API restart; start it again",
                "completed_at": now,
                "updated_at": now,
            },
            "$unset": {"active_key": ""},
        },
    )
    return result.modified_count


def _serialize(document: dict) -> SubtitleBurnResponse:
    return SubtitleBurnResponse(
        burn_id=document["burn_id"],
        project_id=document["project_id"],
        status=document["status"],
        output_file_name=document.get("output_file_name"),
        error=document.get("error"),
        created_at=document["created_at"],
        started_at=document.get("started_at"),
        completed_at=document.get("completed_at"),
        updated_at=document["updated_at"],
    )


def get_subtitle_burn_document(burn_id: str) -> dict:
    document = get_database()[BURNS_COLLECTION].find_one({"burn_id": burn_id})
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subtitle burn job not found")
    return document


def get_subtitle_burn(burn_id: str) -> SubtitleBurnResponse:
    return _serialize(get_subtitle_burn_document(burn_id))


def list_subtitle_burns(limit: int = 100) -> list[SubtitleBurnResponse]:
    documents = get_database()[BURNS_COLLECTION].find().sort("created_at", DESCENDING).limit(limit)
    return [_serialize(document) for document in documents]


def create_subtitle_burn(project_id: str) -> SubtitleBurnResponse:
    project = get_subtitle_document(project_id)
    if project.get("media_type") != "video":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Subtitle burn requires a video project")
    get_project_media(project_id)
    now = utc_now()
    document = {
        "burn_id": uuid4().hex,
        "project_id": project_id,
        "active_key": project_id,
        "project_version": project["version"],
        "status": "queued",
        "output_file_name": None,
        "output_path": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        get_database()[BURNS_COLLECTION].insert_one(document)
    except DuplicateKeyError:
        existing = get_database()[BURNS_COLLECTION].find_one({"active_key": project_id})
        if existing is None:
            raise
        return _serialize(existing)
    return _serialize(document)


def process_subtitle_burn(burn_id: str) -> None:
    collection = get_database()[BURNS_COLLECTION]
    now = utc_now()
    burn = collection.find_one_and_update(
        {"burn_id": burn_id, "status": "queued"},
        {"$set": {"status": "processing", "started_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if burn is None:
        return

    temporary_subtitle: Path | None = None
    temporary_output: Path | None = None
    try:
        project = get_subtitle_document(burn["project_id"])
        _, input_path = get_project_media(burn["project_id"])
        exports_directory = Path(get_settings().storage_root).resolve() / "exports"
        exports_directory.mkdir(parents=True, exist_ok=True)
        output_name = safe_export_name(project["file_name"], project["project_id"], "mp4")
        output_path = exports_directory / output_name
        temporary_output = exports_directory / f".{burn_id}.tmp.mp4"

        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="whisper-subtitle-", suffix=".srt", delete=False
        ) as subtitle_file:
            subtitle_file.write(render_subtitle(project, "srt"))
            temporary_subtitle = Path(subtitle_file.name)

        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(input_path),
            "-vf", f"subtitles={temporary_subtitle}",
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-movflags", "+faststart",
            str(temporary_output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)
        if result.returncode != 0 or not temporary_output.is_file():
            detail = (result.stderr or "FFmpeg did not create an output file").strip()[-2000:]
            raise RuntimeError(f"FFmpeg subtitle burn failed: {detail}")
        temporary_output.replace(output_path)
        temporary_output = None
        completed_at = utc_now()
        updated = collection.update_one(
            {"burn_id": burn_id, "status": "processing"},
            {
                "$set": {
                    "status": "completed",
                    "output_file_name": output_name,
                    "output_path": str(output_path),
                    "completed_at": completed_at,
                    "updated_at": completed_at,
                    "error": None,
                },
                "$unset": {"active_key": ""},
            },
        )
        if updated.modified_count == 0:
            output_path.unlink(missing_ok=True)
    except Exception as exc:
        failed_at = utc_now()
        collection.update_one(
            {"burn_id": burn_id, "status": "processing"},
            {
                "$set": {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "completed_at": failed_at, "updated_at": failed_at},
                "$unset": {"active_key": ""},
            },
        )
    finally:
        if temporary_subtitle is not None:
            temporary_subtitle.unlink(missing_ok=True)
        if temporary_output is not None:
            temporary_output.unlink(missing_ok=True)


def get_burn_output(burn_id: str) -> tuple[dict, Path]:
    burn = get_subtitle_burn_document(burn_id)
    if burn["status"] != "completed" or not burn.get("output_path"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Burned subtitle output is not available")
    output_path = Path(burn["output_path"])
    storage_root = Path(get_settings().storage_root).resolve()
    resolved_path = output_path.resolve()
    if not resolved_path.is_relative_to(storage_root) or not resolved_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Burned subtitle output is missing")
    return burn, resolved_path

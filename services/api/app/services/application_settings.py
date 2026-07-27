import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

from fastapi import HTTPException, status
from pymongo import ReturnDocument

from ..config import get_settings
from ..database import get_database
from ..models.settings import (
    ApplicationSettingsResponse,
    ApplicationSettingsValues,
    CleanupResponse,
    StorageUsageSummary,
    UpdateApplicationSettingsRequest,
    WorkerRuntimeResponse,
)

COLLECTION_NAME = "application_settings"
RUNTIME_COLLECTION = "worker_runtime"
ACTIVE_DOCUMENT_ID = "active"
RESTART_REQUIRED_FIELDS = [
    "transcription.device",
    "transcription.maximum_concurrent_transcription_jobs",
]
CACHE_TTL_SECONDS = 5.0

_cache_lock = threading.Lock()
_cached_settings: ApplicationSettingsResponse | None = None
_cached_at = 0.0


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _defaults() -> ApplicationSettingsValues:
    environment = get_settings()
    values = ApplicationSettingsValues()
    values.worker_processing.polling_interval_seconds = environment.worker_poll_interval_seconds
    values.worker_processing.stale_heartbeat_threshold_seconds = environment.worker_stale_after_seconds
    return values


def _response(document: dict) -> ApplicationSettingsResponse:
    values = ApplicationSettingsValues.model_validate(document)
    return ApplicationSettingsResponse(
        **values.model_dump(),
        version=int(document["version"]),
        updated_at=document["updated_at"],
        restart_required_fields=RESTART_REQUIRED_FIELDS,
    )


def ensure_application_settings() -> ApplicationSettingsResponse:
    collection = get_database()[COLLECTION_NAME]
    defaults = _defaults()
    now = utc_now()
    collection.update_one(
        {"_id": ACTIVE_DOCUMENT_ID},
        {
            "$setOnInsert": {
                **defaults.model_dump(),
                "version": 1,
                "created_at": now,
                "updated_at": now,
            }
        },
        upsert=True,
    )
    document = collection.find_one({"_id": ACTIVE_DOCUMENT_ID})
    if document is None:
        raise RuntimeError("Active application settings could not be initialized")
    return _response(document)


def get_application_settings(force: bool = False) -> ApplicationSettingsResponse:
    global _cached_at, _cached_settings
    now = monotonic()
    with _cache_lock:
        if not force and _cached_settings is not None and now - _cached_at < CACHE_TTL_SECONDS:
            return _cached_settings
        _cached_settings = ensure_application_settings()
        _cached_at = now
        return _cached_settings


def invalidate_settings_cache() -> None:
    global _cached_at, _cached_settings
    with _cache_lock:
        _cached_settings = None
        _cached_at = 0.0


def update_application_settings(payload: UpdateApplicationSettingsRequest) -> ApplicationSettingsResponse:
    values = ApplicationSettingsValues.model_validate(payload.model_dump(exclude={"version"}))
    document = get_database()[COLLECTION_NAME].find_one_and_update(
        {"_id": ACTIVE_DOCUMENT_ID, "version": payload.version},
        {
            "$set": {**values.model_dump(), "updated_at": utc_now()},
            "$inc": {"version": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        current = get_database()[COLLECTION_NAME].find_one({"_id": ACTIVE_DOCUMENT_ID}, {"version": 1})
        if current is None:
            ensure_application_settings()
            current = get_database()[COLLECTION_NAME].find_one({"_id": ACTIVE_DOCUMENT_ID}, {"version": 1})
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Version conflict: current version is {current['version'] if current else 'unknown'}",
        )
    invalidate_settings_cache()
    return _response(document)


def storage_usage_summary() -> StorageUsageSummary:
    root = Path(get_settings().storage_root).resolve()
    totals = {"uploads": 0, "exports": 0, "other": 0}
    count = 0
    if root.is_dir():
        for path in root.rglob("*"):
            try:
                resolved = path.resolve()
                if not resolved.is_relative_to(root) or not resolved.is_file():
                    continue
                size = resolved.stat().st_size
            except OSError:
                continue
            relative = resolved.relative_to(root)
            category = relative.parts[0] if relative.parts and relative.parts[0] in {"uploads", "exports"} else "other"
            totals[category] += size
            count += 1
    return StorageUsageSummary(
        total_bytes=sum(totals.values()),
        uploads_bytes=totals["uploads"],
        exports_bytes=totals["exports"],
        other_bytes=totals["other"],
        file_count=count,
    )


def get_runtime_status() -> WorkerRuntimeResponse:
    database = get_database()
    settings = get_application_settings()
    worker_settings = settings.worker_processing
    cutoff = utc_now() - timedelta(seconds=max(10, worker_settings.stale_heartbeat_threshold_seconds))
    runtime_documents = list(
        database[RUNTIME_COLLECTION]
        .find({"status": {"$ne": "stopped"}, "last_heartbeat": {"$gte": cutoff}})
        .sort("last_heartbeat", -1)
    )
    primary = runtime_documents[0] if runtime_documents else database[RUNTIME_COLLECTION].find_one(sort=[("last_heartbeat", -1)])
    jobs = database["transcription_jobs"]
    pending_fields: list[str] = []
    if primary:
        if primary.get("effective_device_setting") != settings.transcription.device:
            pending_fields.append("transcription.device")
        if primary.get("configured_concurrency") != settings.transcription.maximum_concurrent_transcription_jobs:
            pending_fields.append("transcription.maximum_concurrent_transcription_jobs")
    status_value = "disabled" if not worker_settings.worker_enabled else "online" if runtime_documents else "offline"
    current_jobs = [item.get("current_job") for item in runtime_documents if item.get("current_job")]
    return WorkerRuntimeResponse(
        worker_status=status_value,
        worker_id=primary.get("worker_id") if primary else None,
        last_heartbeat=primary.get("last_heartbeat") if primary else None,
        current_job=current_jobs[0] if current_jobs else None,
        active_workers=len(runtime_documents),
        queued_jobs=jobs.count_documents({"status": "queued"}),
        processing_jobs=jobs.count_documents({"status": "processing"}),
        completed_jobs=jobs.count_documents({"status": "completed"}),
        failed_jobs=jobs.count_documents({"status": "failed"}),
        effective_device=primary.get("effective_device") if primary else None,
        configured_concurrency=settings.transcription.maximum_concurrent_transcription_jobs,
        pending_restart=bool(pending_fields),
        pending_restart_fields=pending_fields,
        settings_version=settings.version,
        storage_usage=storage_usage_summary(),
    )


def _safe_file(path_value: str | Path, root: Path) -> Path | None:
    try:
        path = Path(path_value).resolve()
    except (OSError, RuntimeError):
        return None
    return path if path.is_relative_to(root) and path.is_file() else None


def run_retention_cleanup() -> CleanupResponse:
    database = get_database()
    settings = get_application_settings(force=True).storage_retention
    root = Path(get_settings().storage_root).resolve()
    now = utc_now()
    media_cutoff = now - timedelta(days=settings.media_retention_days)
    export_cutoff = now - timedelta(days=settings.export_retention_days)
    result = CleanupResponse(
        media_files_deleted=0,
        export_files_deleted=0,
        orphan_files_deleted=0,
        bytes_reclaimed=0,
        protected_active_files=0,
        protected_project_files=0,
        errors=[],
    )
    jobs = database["transcription_jobs"]
    media_collection = database["media_files"]
    active_media_ids = set(jobs.distinct("media_file_id", {"status": {"$in": ["queued", "processing"]}}))
    project_media_ids = set(database["subtitle_projects"].distinct("media_file_id"))
    referenced_paths: set[Path] = set()
    for media in media_collection.find({}, {"stored_path": 1}):
        if media.get("stored_path"):
            path = _safe_file(media["stored_path"], root)
            if path:
                referenced_paths.add(path)

    for media in media_collection.find({"created_at": {"$lt": media_cutoff}}):
        media_id = media["_id"]
        if media_id in active_media_ids:
            result.protected_active_files += 1
            continue
        if media_id in project_media_ids:
            result.protected_project_files += 1
            continue
        path = _safe_file(media.get("stored_path", ""), root)
        try:
            size = path.stat().st_size if path else 0
            deleted = media_collection.delete_one({"_id": media_id, "created_at": {"$lt": media_cutoff}})
            if deleted.deleted_count == 1:
                if path:
                    path.unlink(missing_ok=True)
                result.media_files_deleted += 1
                result.bytes_reclaimed += size
        except OSError as exc:
            result.errors.append(f"Media {media_id}: {exc}")

    burns = database["subtitle_burn_jobs"]
    for burn in burns.find({"status": "completed", "completed_at": {"$lt": export_cutoff}, "output_path": {"$ne": None}}):
        path = _safe_file(burn.get("output_path", ""), root)
        try:
            size = path.stat().st_size if path else 0
            updated = burns.update_one(
                {"_id": burn["_id"], "status": "completed", "output_path": burn.get("output_path")},
                {"$set": {"output_path": None, "output_file_name": None, "expired_at": now, "updated_at": now}},
            )
            if updated.modified_count == 1:
                if path:
                    path.unlink(missing_ok=True)
                result.export_files_deleted += 1
                result.bytes_reclaimed += size
        except OSError as exc:
            result.errors.append(f"Export {burn.get('burn_id')}: {exc}")

    uploads = root / "uploads"
    if uploads.is_dir():
        for candidate in uploads.iterdir():
            path = _safe_file(candidate, root)
            if not path or path in referenced_paths:
                continue
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                if modified >= media_cutoff:
                    continue
                size = path.stat().st_size
                path.unlink()
                result.orphan_files_deleted += 1
                result.bytes_reclaimed += size
            except OSError as exc:
                result.errors.append(f"Orphan {candidate.name}: {exc}")
    return result

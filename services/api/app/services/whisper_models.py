import hashlib
import os
import stat
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from uuid import uuid4

from pymongo import ASCENDING, ReturnDocument

from ..config import get_settings
from ..database import get_database
from ..models.settings import AvailableWhisperModelResponse, WhisperModelResponse
from .whisper_model_metadata import (
    SUPPORTED_WHISPER_MODELS,
    WHISPER_MODEL_METADATA,
    WhisperModelMetadata,
)

COLLECTION_NAME = "whisper_models"
HASH_CHUNK_SIZE = 1024 * 1024
USAGE_LEASE_SECONDS = 300
REGISTRY_ENSURE_INTERVAL_SECONDS = 5.0
VALID_STATUSES = {
    "not_downloaded", "downloading", "available", "failed", "corrupted", "deleting"
}
_registry_ensure_lock = threading.Lock()
_registry_ensured_at = 0.0


class WhisperModelActionConflict(RuntimeError):
    pass


class WhisperModelUnavailableError(RuntimeError):
    pass


def whisper_model_unavailable_message(model: str) -> str:
    return (
        f'Whisper model "{model}" is not available. '
        "Download it from Settings → Whisper Models."
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def whisper_model_directory() -> Path:
    directory = get_settings().whisper_model_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _base_document(metadata: WhisperModelMetadata) -> dict:
    file_path = whisper_model_directory() / metadata.file_name
    return {
        "model": metadata.model,
        "status": "not_downloaded",
        "file_name": metadata.file_name,
        "file_path": str(file_path),
        "expected_size_bytes": metadata.expected_size_bytes,
        "expected_checksum": metadata.expected_checksum,
        "actual_size_bytes": None,
        "checksum": None,
        "checksum_valid": None,
        "downloaded_at": None,
        "last_verified_at": None,
        "last_error": None,
        "downloaded_bytes": 0,
        "progress": 0,
        "download_started_at": None,
        "download_completed_at": None,
        "download_heartbeat_at": None,
        "download_worker_id": None,
        "cancel_requested": False,
        "attempt": 0,
        "download_restart_requested": False,
        "usage_leases": [],
        "operation_started_at": None,
    }


def ensure_whisper_model_registry(*, force: bool = False) -> None:
    global _registry_ensured_at
    now_monotonic = monotonic()
    if not force and now_monotonic - _registry_ensured_at < REGISTRY_ENSURE_INTERVAL_SECONDS:
        return
    with _registry_ensure_lock:
        now_monotonic = monotonic()
        if not force and now_monotonic - _registry_ensured_at < REGISTRY_ENSURE_INTERVAL_SECONDS:
            return
        _reconcile_whisper_model_registry()
        _registry_ensured_at = monotonic()


def _reconcile_whisper_model_registry() -> None:
    collection = get_database()[COLLECTION_NAME]
    duplicates = collection.aggregate(
        [
            {"$match": {"model": {"$in": list(SUPPORTED_WHISPER_MODELS)}}},
            {"$sort": {"updated_at": -1, "_id": 1}},
            {"$group": {"_id": "$model", "ids": {"$push": "$_id"}, "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
        ]
    )
    for duplicate in duplicates:
        collection.delete_many({"_id": {"$in": duplicate["ids"][1:]}})
    collection.create_index(
        [("model", ASCENDING)], unique=True, name="unique_whisper_model"
    )
    now = utc_now()
    for metadata in WHISPER_MODEL_METADATA.values():
        base = _base_document(metadata)
        managed_metadata = {
            "file_name": base.pop("file_name"),
            "file_path": base.pop("file_path"),
            "expected_checksum": base.pop("expected_checksum"),
        }
        expected_size = base.pop("expected_size_bytes")
        if expected_size is not None:
            managed_metadata["expected_size_bytes"] = expected_size
        collection.update_one(
            {"model": metadata.model},
            {
                "$set": managed_metadata,
                "$setOnInsert": {**base, "created_at": now, "updated_at": now},
            },
            upsert=True,
        )
    defaults = {
        "expected_size_bytes": None,
        "downloaded_bytes": 0,
        "progress": 0,
        "download_started_at": None,
        "download_completed_at": None,
        "download_heartbeat_at": None,
        "download_worker_id": None,
        "cancel_requested": False,
        "attempt": 0,
        "download_restart_requested": False,
        "usage_leases": [],
        "operation_started_at": None,
    }
    for field, default in defaults.items():
        collection.update_many({field: {"$exists": False}}, {"$set": {field: default}})
    collection.update_many(
        {"usage_leases": {"$not": {"$type": "array"}}},
        {"$set": {"usage_leases": []}},
    )
    collection.update_many(
        {
            "model": {"$in": list(SUPPORTED_WHISPER_MODELS)},
            "status": {"$nin": list(VALID_STATUSES)},
        },
        {
            "$set": {
                "status": "not_downloaded",
                "download_worker_id": None,
                "cancel_requested": False,
                "progress": 0,
                "downloaded_bytes": 0,
                "last_error": "Invalid registry state was normalized",
                "updated_at": now,
            }
        },
    )
    collection.update_many(
        {}, {"$pull": {"usage_leases": {"expires_at": {"$lte": now}}}}
    )
    collection.update_many(
        {"status": "downloading", "progress": {"$gte": 100}},
        {"$set": {"progress": 99}},
    )
    collection.update_many(
        {"status": {"$in": ["failed", "corrupted"]}, "progress": {"$gte": 100}},
        {"$set": {"progress": 99}},
    )
    deleting_cutoff = now - timedelta(seconds=get_settings().whisper_download_stale_seconds)
    for document in collection.find(
        {
            "model": {"$in": list(SUPPORTED_WHISPER_MODELS)},
            "status": "deleting",
            "updated_at": {"$lt": deleting_cutoff},
        },
        {"model": 1, "updated_at": 1},
    ):
        metadata = WHISPER_MODEL_METADATA[document["model"]]
        exists = (whisper_model_directory() / metadata.file_name).is_file()
        collection.update_one(
            {"_id": document["_id"], "status": "deleting", "updated_at": document["updated_at"]},
            {
                "$set": {
                    "status": "failed" if exists else "not_downloaded",
                    "progress": 0,
                    "downloaded_bytes": 0 if not exists else document.get("downloaded_bytes", 0),
                    "operation_started_at": None,
                    "last_error": "Interrupted deletion recovered" if exists else None,
                    "updated_at": now,
                }
            },
        )


def acquire_whisper_model_usage(model: str, owner: str) -> str:
    if model not in WHISPER_MODEL_METADATA:
        raise WhisperModelUnavailableError(whisper_model_unavailable_message(model))
    ensure_whisper_model_registry()
    collection = get_database()[COLLECTION_NAME]
    now = utc_now()
    collection.update_one(
        {"model": model},
        {"$pull": {"usage_leases": {"expires_at": {"$lte": now}}}},
    )
    lease_id = uuid4().hex
    lease = {
        "lease_id": lease_id,
        "owner": owner[:128],
        "created_at": now,
        "expires_at": now + timedelta(seconds=USAGE_LEASE_SECONDS),
    }
    document = collection.find_one_and_update(
        {"model": model, "status": "available"},
        {"$push": {"usage_leases": lease}},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise WhisperModelUnavailableError(whisper_model_unavailable_message(model))
    return lease_id


def release_whisper_model_usage(model: str, lease_id: str) -> None:
    get_database()[COLLECTION_NAME].update_one(
        {"model": model},
        {"$pull": {"usage_leases": {"lease_id": lease_id}}},
    )


def refresh_whisper_model_usage(model: str, lease_id: str) -> bool:
    result = get_database()[COLLECTION_NAME].update_one(
        {"model": model, "usage_leases.lease_id": lease_id},
        {
            "$set": {
                "usage_leases.$.expires_at": utc_now()
                + timedelta(seconds=USAGE_LEASE_SECONDS)
            }
        },
    )
    return result.matched_count == 1


@contextmanager
def whisper_model_usage(model: str, owner: str):
    lease_id = acquire_whisper_model_usage(model, owner)
    stop_refresh = threading.Event()

    def refresh_loop() -> None:
        while not stop_refresh.wait(USAGE_LEASE_SECONDS / 3):
            try:
                if not refresh_whisper_model_usage(model, lease_id):
                    return
            except Exception:
                # A temporary database outage is covered by the current expiry;
                # the next loop retries if the operation is still alive.
                continue

    refresh_thread = threading.Thread(
        target=refresh_loop,
        name=f"whisper-usage-{model}",
        daemon=True,
    )
    refresh_thread.start()
    try:
        yield lease_id
    finally:
        stop_refresh.set()
        refresh_thread.join(timeout=1)
        try:
            release_whisper_model_usage(model, lease_id)
        except Exception:
            # The lease expires automatically; never mask a completed caller
            # operation solely because MongoDB disappeared during cleanup.
            pass


def _serialize(document: dict) -> WhisperModelResponse:
    return WhisperModelResponse.model_validate(document)


def list_whisper_models() -> list[WhisperModelResponse]:
    ensure_whisper_model_registry()
    collection = get_database()[COLLECTION_NAME]
    documents = {
        document["model"]: document
        for document in collection.find({"model": {"$in": list(SUPPORTED_WHISPER_MODELS)}})
    }
    return [_serialize(documents[model]) for model in SUPPORTED_WHISPER_MODELS]


def _stream_sha256(path: Path) -> tuple[str, int, tuple[int, int, int]]:
    digest = hashlib.sha256()
    size = 0
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    opened_stat = os.fstat(descriptor)
    if not stat.S_ISREG(opened_stat.st_mode):
        os.close(descriptor)
        raise OSError("Model path is not a regular file")
    with os.fdopen(descriptor, "rb") as model_file:
        for chunk in iter(lambda: model_file.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
            size += len(chunk)
    signature = (opened_stat.st_dev, opened_stat.st_ino, opened_stat.st_mtime_ns)
    return digest.hexdigest(), size, signature


def _verification_values(
    metadata: WhisperModelMetadata, *, missing_is_error: bool
) -> dict:
    path = whisper_model_directory() / metadata.file_name
    verified_at = utc_now()
    if path.is_symlink():
        return {
            "status": "corrupted",
            "actual_size_bytes": None,
            "checksum": None,
            "checksum_valid": False,
            "last_verified_at": verified_at,
            "last_error": "Symbolic links are not valid Whisper model files",
        }
    if not path.exists():
        return {
            "status": "not_downloaded",
            "actual_size_bytes": None,
            "checksum": None,
            "checksum_valid": None,
            "downloaded_at": None,
            "last_verified_at": verified_at,
            "last_error": "Model file not found" if missing_is_error else None,
        }

    if not path.is_file():
        return {
            "status": "corrupted",
            "actual_size_bytes": None,
            "checksum": None,
            "checksum_valid": False,
            "last_verified_at": verified_at,
            "last_error": "Model path is not a regular file",
        }

    try:
        stat_size = path.stat().st_size
    except OSError:
        stat_size = None

    try:
        checksum, actual_size, opened_signature = _stream_sha256(path)
    except OSError as exc:
        return {
            "status": "corrupted",
            "actual_size_bytes": stat_size,
            "checksum": None,
            "checksum_valid": False,
            "last_verified_at": verified_at,
            "last_error": f"Could not read model file: {exc}",
        }

    try:
        final_stat = path.lstat()
        final_signature = (final_stat.st_dev, final_stat.st_ino, final_stat.st_mtime_ns)
    except OSError as exc:
        return {
            "status": "corrupted",
            "actual_size_bytes": actual_size,
            "checksum": checksum,
            "checksum_valid": False,
            "last_verified_at": verified_at,
            "last_error": f"Model file changed during verification: {exc}",
        }
    if final_signature != opened_signature or not stat.S_ISREG(final_stat.st_mode):
        return {
            "status": "corrupted",
            "actual_size_bytes": actual_size,
            "checksum": checksum,
            "checksum_valid": False,
            "last_verified_at": verified_at,
            "last_error": "Model file changed during verification",
        }

    checksum_valid = checksum == metadata.expected_checksum
    values = {
        "status": "available" if checksum_valid else "corrupted",
        "actual_size_bytes": actual_size,
        "checksum": checksum,
        "checksum_valid": checksum_valid,
        "last_verified_at": verified_at,
        "last_error": None if checksum_valid else "SHA-256 checksum does not match",
    }
    if checksum_valid:
        values["downloaded_at"] = datetime.fromtimestamp(
            final_stat.st_mtime, tz=timezone.utc
        )
    return values


def verify_whisper_model(
    model: str, *, missing_is_error: bool = True
) -> WhisperModelResponse:
    metadata = WHISPER_MODEL_METADATA.get(model)
    if metadata is None:
        raise ValueError(f"Unsupported Whisper model: {model}")

    ensure_whisper_model_registry()
    collection = get_database()[COLLECTION_NAME]
    current = collection.find_one({"model": model})
    if current and current.get("status") in {"downloading", "deleting"}:
        raise WhisperModelActionConflict(
            f"Whisper model {model} cannot be verified while {current['status']}"
        )
    starting_status = current.get("status") if current else None
    starting_updated_at = current.get("updated_at") if current else None
    values = _verification_values(metadata, missing_is_error=missing_is_error)
    values["updated_at"] = utc_now()
    conditional = {"model": model, "status": starting_status}
    if starting_updated_at is not None:
        conditional["updated_at"] = starting_updated_at
    collection.update_one(conditional, {"$set": values})
    document = collection.find_one({"model": model})
    if document is None:
        raise RuntimeError(f"Whisper model registry entry disappeared: {model}")
    return _serialize(document)


def scan_whisper_models() -> list[WhisperModelResponse]:
    ensure_whisper_model_registry()
    collection = get_database()[COLLECTION_NAME]
    responses: list[WhisperModelResponse] = []
    for model in SUPPORTED_WHISPER_MODELS:
        current = collection.find_one({"model": model})
        if current and current.get("status") in {"downloading", "deleting"}:
            responses.append(_serialize(current))
        else:
            responses.append(verify_whisper_model(model, missing_is_error=False))
    return responses


def is_whisper_model_available(model: str) -> bool:
    if model not in WHISPER_MODEL_METADATA:
        return False
    ensure_whisper_model_registry()
    return (
        get_database()[COLLECTION_NAME].count_documents(
            {"model": model, "status": "available"}, limit=1
        )
        == 1
    )


def require_whisper_model_available(model: str) -> dict:
    if model not in WHISPER_MODEL_METADATA:
        raise WhisperModelUnavailableError(whisper_model_unavailable_message(model))
    ensure_whisper_model_registry()
    document = get_database()[COLLECTION_NAME].find_one(
        {"model": model, "status": "available"}
    )
    if document is None:
        raise WhisperModelUnavailableError(whisper_model_unavailable_message(model))
    return document


def list_available_whisper_models() -> list[AvailableWhisperModelResponse]:
    ensure_whisper_model_registry()
    documents = {
        document["model"]: document
        for document in get_database()[COLLECTION_NAME].find(
            {"model": {"$in": list(SUPPORTED_WHISPER_MODELS)}, "status": "available"}
        )
    }
    return [
        AvailableWhisperModelResponse(
            model=model,
            file_name=documents[model]["file_name"],
            file_path=documents[model]["file_path"],
            actual_size_bytes=int(documents[model]["actual_size_bytes"]),
            last_verified_at=documents[model].get("last_verified_at"),
        )
        for model in SUPPORTED_WHISPER_MODELS
        if model in documents and documents[model].get("actual_size_bytes") is not None
    ]


def resolve_available_whisper_model_path(model: str) -> Path:
    require_whisper_model_available(model)
    verified = verify_whisper_model(model)
    if verified.status != "available":
        raise WhisperModelUnavailableError(whisper_model_unavailable_message(model))

    root = whisper_model_directory().resolve()
    path = (root / verified.file_name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise WhisperModelUnavailableError(whisper_model_unavailable_message(model))
    return path


def whisper_download_directory() -> Path:
    root = whisper_model_directory().resolve()
    directory = root / ".downloads"
    if directory.is_symlink():
        raise ValueError("Whisper download directory cannot be a symbolic link")
    directory.mkdir(parents=True, exist_ok=True)
    resolved = directory.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Whisper download directory is outside model storage")
    try:
        resolved.chmod(0o700)
    except OSError:
        pass
    return resolved


def whisper_partial_path(model: str) -> Path:
    if model not in WHISPER_MODEL_METADATA:
        raise ValueError(f"Unsupported Whisper model: {model}")
    return whisper_download_directory() / f"{model}.part"


def _registry_document(model: str) -> dict:
    ensure_whisper_model_registry()
    document = get_database()[COLLECTION_NAME].find_one({"model": model})
    if document is None:
        raise ValueError(f"Unsupported Whisper model: {model}")
    return document


def request_whisper_model_download(
    model: str, *, retry: bool = False
) -> WhisperModelResponse:
    if model not in WHISPER_MODEL_METADATA:
        raise ValueError(f"Unsupported Whisper model: {model}")
    collection = get_database()[COLLECTION_NAME]
    current = _registry_document(model)
    if current["status"] == "downloading":
        if retry:
            raise WhisperModelActionConflict(
                f"Whisper model {model} can only retry from failed, corrupted, or not_downloaded"
            )
        return _serialize(current)
    if current["status"] == "available":
        raise WhisperModelActionConflict(f"Whisper model {model} is already available")
    if current["status"] == "deleting":
        raise WhisperModelActionConflict(f"Whisper model {model} is being deleted")
    allowed = {"not_downloaded", "failed", "corrupted"}
    if current["status"] not in allowed:
        action = "retry" if retry else "download"
        raise WhisperModelActionConflict(
            f"Whisper model {model} cannot {action} from status {current['status']}"
        )

    now = utc_now()
    document = collection.find_one_and_update(
        {"model": model, "status": current["status"]},
        {
            "$set": {
                "status": "downloading",
                "progress": 0,
                "download_started_at": None,
                "download_completed_at": None,
                "download_heartbeat_at": None,
                "download_worker_id": None,
                "cancel_requested": False,
                "last_error": None,
                "download_restart_requested": current["status"] == "corrupted",
                "operation_started_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        document = collection.find_one({"model": model})
    if document is None:
        raise RuntimeError(f"Whisper model registry entry disappeared: {model}")
    return _serialize(document)


def cancel_whisper_model_download(model: str) -> WhisperModelResponse:
    current = _registry_document(model)
    if current["status"] != "downloading":
        raise WhisperModelActionConflict(
            f"Whisper model {model} is not queued or downloading"
        )
    document = get_database()[COLLECTION_NAME].find_one_and_update(
        {"model": model, "status": "downloading"},
        {"$set": {"cancel_requested": True, "updated_at": utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise WhisperModelActionConflict(f"Whisper model {model} is no longer downloading")
    return _serialize(document)


def delete_whisper_model(model: str) -> WhisperModelResponse:
    metadata = WHISPER_MODEL_METADATA.get(model)
    if metadata is None:
        raise ValueError(f"Unsupported Whisper model: {model}")

    ensure_whisper_model_registry()
    model_directory = whisper_model_directory().resolve()
    model_path = model_directory / metadata.file_name
    resolved_path = model_path.resolve(strict=False)
    if not resolved_path.is_relative_to(model_directory):
        raise ValueError("Whisper model path is outside the configured model directory")

    if model_path.is_symlink():
        raise ValueError("Symbolic links are not valid Whisper model files")

    database = get_database()
    collection = database[COLLECTION_NAME]
    now = utc_now()
    collection.update_one(
        {"model": model},
        {"$pull": {"usage_leases": {"expires_at": {"$lte": now}}}},
    )
    if database["transcription_jobs"].count_documents(
        {"model": model, "status": {"$in": ["queued", "processing"]}}, limit=1
    ):
        raise WhisperModelActionConflict(
            f"Whisper model {model} is used by an active transcription job"
        )
    if database["live_sessions"].count_documents(
        {"model": model, "status": {"$in": ["active", "paused"]}}, limit=1
    ):
        raise WhisperModelActionConflict(
            f"Whisper model {model} is used by an active live session"
        )

    current_registry = collection.find_one({"model": model})
    if current_registry is None:
        raise RuntimeError(f"Whisper model registry entry disappeared: {model}")
    previous_status = current_registry.get("status")
    if previous_status in {"downloading", "deleting"}:
        raise WhisperModelActionConflict(
            f"Whisper model {model} cannot be deleted while {previous_status}"
        )
    deleting = collection.find_one_and_update(
        {
            "model": model,
            "status": previous_status,
            "usage_leases": {"$size": 0},
        },
        {
            "$set": {
                "status": "deleting",
                "operation_started_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if deleting is None:
        current = collection.find_one({"model": model})
        raise WhisperModelActionConflict(
            f"Whisper model {model} cannot be deleted while active or {current['status'] if current else 'unknown'}"
        )
    if database["transcription_jobs"].count_documents(
        {"model": model, "status": {"$in": ["queued", "processing"]}}, limit=1
    ) or database["live_sessions"].count_documents(
        {"model": model, "status": {"$in": ["active", "paused"]}}, limit=1
    ):
        collection.update_one(
            {"model": model, "status": "deleting"},
            {"$set": {"status": previous_status, "operation_started_at": None, "updated_at": utc_now()}},
        )
        raise WhisperModelActionConflict(
            f"Whisper model {model} became active while deletion was starting"
        )
    try:
        if model_path.is_symlink():
            raise ValueError("Symbolic links are not valid Whisper model files")
        try:
            model_path.unlink()
        except FileNotFoundError:
            pass
        try:
            whisper_partial_path(model).unlink()
        except FileNotFoundError:
            pass
    except (OSError, ValueError) as exc:
        collection.update_one(
            {"model": model, "status": "deleting"},
            {
                "$set": {
                    "status": "failed",
                    "last_error": f"Model deletion failed: {exc}",
                    "operation_started_at": None,
                    "progress": min(99, float(deleting.get("progress", 0))),
                    "updated_at": utc_now(),
                }
            },
        )
        raise

    now = utc_now()
    collection.update_one(
        {"model": model},
        {
            "$set": {
                "status": "not_downloaded",
                "actual_size_bytes": None,
                "checksum": None,
                "checksum_valid": None,
                "downloaded_at": None,
                "last_verified_at": now,
                "last_error": None,
                "downloaded_bytes": 0,
                "progress": 0,
                "download_started_at": None,
                "download_completed_at": None,
                "download_heartbeat_at": None,
                "download_worker_id": None,
                "cancel_requested": False,
                "download_restart_requested": False,
                "operation_started_at": None,
                "updated_at": now,
            }
        },
    )
    document = collection.find_one({"model": model})
    if document is None:
        raise RuntimeError(f"Whisper model registry entry disappeared: {model}")
    return _serialize(document)

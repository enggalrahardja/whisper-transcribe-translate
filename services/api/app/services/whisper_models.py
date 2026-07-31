import hashlib
import os
import shutil
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
    MODEL_REGISTRY_METADATA,
    SUPPORTED_MODEL_BACKENDS,
    SUPPORTED_WHISPER_MODELS,
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


def canonical_registry_model(model: str) -> str:
    return "large-v3" if model == "large" else model


def registry_identity(backend: str, model: str) -> dict[str, str]:
    canonical_model = canonical_registry_model(model)
    if backend not in SUPPORTED_MODEL_BACKENDS:
        raise ValueError(f"Unsupported model backend: {backend}")
    if canonical_model not in SUPPORTED_WHISPER_MODELS:
        raise ValueError(f"Unsupported Whisper model: {model}")
    return {"backend": backend, "model": canonical_model}


def model_metadata(backend: str, model: str) -> WhisperModelMetadata:
    identity = registry_identity(backend, model)
    return MODEL_REGISTRY_METADATA[(identity["backend"], identity["model"])]


def whisper_model_unavailable_message(model: str, backend: str = "pytorch") -> str:
    return (
        f'Whisper model "{canonical_registry_model(model)}" is not available for {backend}. '
        "Download it from Settings → Models."
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def whisper_model_directory(backend: str = "pytorch") -> Path:
    if backend not in SUPPORTED_MODEL_BACKENDS:
        raise ValueError(f"Unsupported model backend: {backend}")
    settings = get_settings()
    directory = (
        settings.whisper_model_dir
        if backend == "pytorch"
        else settings.faster_whisper_model_dir
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def whisper_model_path(backend: str, model: str) -> Path:
    metadata = model_metadata(backend, model)
    return whisper_model_directory(backend) / metadata.storage_name


def _base_document(metadata: WhisperModelMetadata) -> dict:
    model_path = whisper_model_path(metadata.backend, metadata.model)
    return {
        "backend": metadata.backend,
        "model": metadata.model,
        "backend_model_id": metadata.backend_model_id,
        "status": "not_downloaded",
        "storage_kind": metadata.storage_kind,
        "file_name": metadata.storage_name,
        "file_path": str(model_path),
        "expected_size_bytes": metadata.expected_size_bytes,
        "expected_checksum": metadata.expected_checksum,
        "actual_size_bytes": None,
        "checksum": None,
        "checksum_valid": None,
        "validation_status": "not_verified",
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
        "cache_import_blocked": False,
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


def _drop_legacy_index(collection: object) -> None:
    try:
        indexes = collection.index_information()
    except (AttributeError, NotImplementedError):
        return
    if "unique_whisper_model" in indexes:
        collection.drop_index("unique_whisper_model")


def _reconcile_whisper_model_registry() -> None:
    collection = get_database()[COLLECTION_NAME]
    now = utc_now()
    # Documents written by the original PyTorch-only registry remain in place.
    _drop_legacy_index(collection)
    collection.update_many({"backend": {"$exists": False}}, {"$set": {"backend": "pytorch"}})
    for legacy in collection.find({"backend": "pytorch", "model": "large"}):
        canonical = collection.find_one({"backend": "pytorch", "model": "large-v3"})
        if canonical:
            if legacy.get("status") == "available" and canonical.get("status") != "available":
                preserved = {
                    key: value
                    for key, value in legacy.items()
                    if key not in {"_id", "backend", "model", "file_name", "file_path"}
                }
                collection.update_one({"_id": canonical["_id"]}, {"$set": preserved})
            collection.delete_one({"_id": legacy["_id"]})
        else:
            collection.update_one(
                {"_id": legacy["_id"]},
                {"$set": {"model": "large-v3", "updated_at": now}},
            )
    duplicates = collection.aggregate(
        [
            {"$match": {
                "backend": {"$in": list(SUPPORTED_MODEL_BACKENDS)},
                "model": {"$in": list(SUPPORTED_WHISPER_MODELS)},
            }},
            {"$sort": {"updated_at": -1, "_id": 1}},
            {"$group": {
                "_id": {"backend": "$backend", "model": "$model"},
                "ids": {"$push": "$_id"},
                "count": {"$sum": 1},
            }},
            {"$match": {"count": {"$gt": 1}}},
        ]
    )
    for duplicate in duplicates:
        collection.delete_many({"_id": {"$in": duplicate["ids"][1:]}})
    collection.create_index(
        [("backend", ASCENDING), ("model", ASCENDING)],
        unique=True,
        name="unique_whisper_backend_model",
    )
    for metadata in MODEL_REGISTRY_METADATA.values():
        base = _base_document(metadata)
        managed_keys = (
            "backend_model_id", "storage_kind", "file_name", "file_path",
            "expected_checksum", "expected_size_bytes",
        )
        managed = {key: base.pop(key) for key in managed_keys}
        collection.update_one(
            registry_identity(metadata.backend, metadata.model),
            {
                "$set": managed,
                "$setOnInsert": {**base, "created_at": now, "updated_at": now},
            },
            upsert=True,
        )
    defaults = {
        "validation_status": "not_verified",
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
        "cache_import_blocked": False,
        "usage_leases": [],
        "operation_started_at": None,
    }
    for field, default in defaults.items():
        collection.update_many({field: {"$exists": False}}, {"$set": {field: default}})
    collection.update_many({"usage_leases": {"$not": {"$type": "array"}}}, {"$set": {"usage_leases": []}})
    collection.update_many(
        {
            "backend": {"$in": list(SUPPORTED_MODEL_BACKENDS)},
            "model": {"$in": list(SUPPORTED_WHISPER_MODELS)},
            "status": {"$nin": list(VALID_STATUSES)},
        },
        {"$set": {
            "status": "not_downloaded", "download_worker_id": None,
            "cancel_requested": False, "progress": 0, "downloaded_bytes": 0,
            "last_error": "Invalid registry state was normalized", "updated_at": now,
        }},
    )
    collection.update_many({}, {"$pull": {"usage_leases": {"expires_at": {"$lte": now}}}})
    collection.update_many({"status": "downloading", "progress": {"$gte": 100}}, {"$set": {"progress": 99}})
    collection.update_many({"status": {"$in": ["failed", "corrupted"]}, "progress": {"$gte": 100}}, {"$set": {"progress": 99}})
    deleting_cutoff = now - timedelta(seconds=get_settings().whisper_download_stale_seconds)
    for document in collection.find(
        {
            "backend": {"$in": list(SUPPORTED_MODEL_BACKENDS)},
            "model": {"$in": list(SUPPORTED_WHISPER_MODELS)},
            "status": "deleting", "updated_at": {"$lt": deleting_cutoff},
        }
    ):
        exists = whisper_model_path(document["backend"], document["model"]).exists()
        collection.update_one(
            {"_id": document["_id"], "status": "deleting", "updated_at": document["updated_at"]},
            {"$set": {
                "status": "failed" if exists else "not_downloaded",
                "progress": 0,
                "downloaded_bytes": document.get("downloaded_bytes", 0) if exists else 0,
                "operation_started_at": None,
                "last_error": "Interrupted deletion recovered" if exists else None,
                "updated_at": now,
            }},
        )


def acquire_whisper_model_usage(model: str, owner: str, backend: str = "pytorch") -> str:
    identity = registry_identity(backend, model)
    ensure_whisper_model_registry()
    collection = get_database()[COLLECTION_NAME]
    now = utc_now()
    collection.update_one(identity, {"$pull": {"usage_leases": {"expires_at": {"$lte": now}}}})
    lease_id = uuid4().hex
    lease = {
        "lease_id": lease_id, "owner": owner[:128], "created_at": now,
        "expires_at": now + timedelta(seconds=USAGE_LEASE_SECONDS),
    }
    document = collection.find_one_and_update(
        {**identity, "status": "available"},
        {"$push": {"usage_leases": lease}},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise WhisperModelUnavailableError(whisper_model_unavailable_message(model, backend))
    return lease_id


def release_whisper_model_usage(model: str, lease_id: str, backend: str = "pytorch") -> None:
    get_database()[COLLECTION_NAME].update_one(
        registry_identity(backend, model), {"$pull": {"usage_leases": {"lease_id": lease_id}}}
    )


def refresh_whisper_model_usage(model: str, lease_id: str, backend: str = "pytorch") -> bool:
    result = get_database()[COLLECTION_NAME].update_one(
        {**registry_identity(backend, model), "usage_leases.lease_id": lease_id},
        {"$set": {"usage_leases.$.expires_at": utc_now() + timedelta(seconds=USAGE_LEASE_SECONDS)}},
    )
    return result.matched_count == 1


@contextmanager
def whisper_model_usage(model: str, owner: str, backend: str = "pytorch"):
    canonical_model = canonical_registry_model(model)
    lease_id = acquire_whisper_model_usage(canonical_model, owner, backend)
    stop_refresh = threading.Event()

    def refresh_loop() -> None:
        while not stop_refresh.wait(USAGE_LEASE_SECONDS / 3):
            try:
                if not refresh_whisper_model_usage(canonical_model, lease_id, backend):
                    return
            except Exception:
                continue

    refresh_thread = threading.Thread(
        target=refresh_loop, name=f"whisper-usage-{backend}-{canonical_model}", daemon=True
    )
    refresh_thread.start()
    try:
        yield lease_id
    finally:
        stop_refresh.set()
        refresh_thread.join(timeout=1)
        try:
            release_whisper_model_usage(canonical_model, lease_id, backend)
        except Exception:
            pass


def _serialize(document: dict) -> WhisperModelResponse:
    return WhisperModelResponse.model_validate(document)


def list_whisper_models(backend: str = "pytorch") -> list[WhisperModelResponse]:
    registry_identity(backend, "base")
    ensure_whisper_model_registry()
    documents = {
        document["model"]: document
        for document in get_database()[COLLECTION_NAME].find({
            "backend": backend, "model": {"$in": list(SUPPORTED_WHISPER_MODELS)}
        })
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


def directory_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if child.is_symlink():
            raise OSError("Symbolic links are not valid in a CTranslate2 model directory")
        if child.is_file():
            total += child.stat().st_size
    return total


def validate_ctranslate2_directory(path: Path) -> tuple[bool, str | None]:
    try:
        import ctranslate2
    except ImportError:
        return False, "CTranslate2 is not installed"
    try:
        if not ctranslate2.contains_model(str(path)):
            return False, "Directory does not contain a valid CTranslate2 model"
    except Exception as exc:
        return False, f"Could not validate CTranslate2 model: {exc}"
    return True, None


def _link_or_copy_model_file(source: str, destination: str) -> None:
    resolved_source = Path(source).resolve()
    try:
        os.link(resolved_source, destination)
    except OSError:
        shutil.copy2(resolved_source, destination)


def materialize_cached_faster_whisper_model(metadata: WhisperModelMetadata) -> bool:
    """Import a valid Hugging Face cache snapshot without downloading it again."""
    target = whisper_model_path(metadata.backend, metadata.model)
    if target.exists():
        return False
    try:
        from faster_whisper.utils import download_model

        cached = Path(
            download_model(metadata.backend_model_id, local_files_only=True)
        )
    except Exception:
        return False
    valid, _ = validate_ctranslate2_directory(cached)
    if not valid:
        return False
    import_root = whisper_download_directory(metadata.backend)
    temporary = import_root / f"{metadata.model}.import-{uuid4().hex}"
    try:
        shutil.copytree(cached, temporary, copy_function=_link_or_copy_model_file)
        if target.exists():
            shutil.rmtree(temporary)
            return False
        os.replace(temporary, target)
        return True
    except OSError:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        return False


def _missing_values(missing_is_error: bool, storage_label: str) -> dict:
    return {
        "status": "not_downloaded", "actual_size_bytes": None, "checksum": None,
        "checksum_valid": None, "validation_status": "not_verified",
        "downloaded_at": None, "last_verified_at": utc_now(),
        "last_error": f"Model {storage_label} not found" if missing_is_error else None,
    }


def _verify_pytorch(metadata: WhisperModelMetadata, *, missing_is_error: bool) -> dict:
    path = whisper_model_path(metadata.backend, metadata.model)
    verified_at = utc_now()
    if path.is_symlink():
        return {"status": "corrupted", "actual_size_bytes": None, "checksum": None,
                "checksum_valid": False, "validation_status": "invalid", "last_verified_at": verified_at,
                "last_error": "Symbolic links are not valid Whisper model files"}
    if not path.exists():
        return _missing_values(missing_is_error, "file")
    if not path.is_file():
        return {"status": "corrupted", "actual_size_bytes": None, "checksum": None,
                "checksum_valid": False, "validation_status": "invalid", "last_verified_at": verified_at,
                "last_error": "Model path is not a regular file"}
    try:
        checksum, actual_size, opened_signature = _stream_sha256(path)
        final_stat = path.lstat()
    except OSError as exc:
        return {"status": "corrupted", "actual_size_bytes": None, "checksum": None,
                "checksum_valid": False, "validation_status": "invalid", "last_verified_at": verified_at,
                "last_error": f"Could not read model file: {exc}"}
    final_signature = (final_stat.st_dev, final_stat.st_ino, final_stat.st_mtime_ns)
    if final_signature != opened_signature or not stat.S_ISREG(final_stat.st_mode):
        return {"status": "corrupted", "actual_size_bytes": actual_size, "checksum": checksum,
                "checksum_valid": False, "validation_status": "invalid", "last_verified_at": verified_at,
                "last_error": "Model file changed during verification"}
    checksum_valid = checksum == metadata.expected_checksum
    values = {
        "status": "available" if checksum_valid else "corrupted",
        "actual_size_bytes": actual_size, "checksum": checksum,
        "checksum_valid": checksum_valid,
        "validation_status": "valid" if checksum_valid else "invalid",
        "last_verified_at": verified_at,
        "last_error": None if checksum_valid else "SHA-256 checksum does not match",
    }
    if checksum_valid:
        values["downloaded_at"] = datetime.fromtimestamp(final_stat.st_mtime, tz=timezone.utc)
    return values


def _verify_faster_whisper(
    metadata: WhisperModelMetadata, *, missing_is_error: bool, allow_cache_import: bool = True
) -> dict:
    path = whisper_model_path(metadata.backend, metadata.model)
    verified_at = utc_now()
    if path.is_symlink():
        return {"status": "corrupted", "actual_size_bytes": None, "checksum": None,
                "checksum_valid": None, "validation_status": "invalid", "last_verified_at": verified_at,
                "last_error": "Symbolic links are not valid CTranslate2 model directories"}
    if not path.exists() and not (
        allow_cache_import and materialize_cached_faster_whisper_model(metadata)
    ):
        return _missing_values(missing_is_error, "directory")
    if not path.is_dir():
        return {"status": "corrupted", "actual_size_bytes": None, "checksum": None,
                "checksum_valid": None, "validation_status": "invalid", "last_verified_at": verified_at,
                "last_error": "Model path is not a directory"}
    valid, error = validate_ctranslate2_directory(path)
    try:
        actual_size = directory_size(path)
        model_bin = path / "model.bin"
        checksum = _stream_sha256(model_bin)[0] if model_bin.is_file() else None
    except OSError as exc:
        valid, error, actual_size, checksum = False, f"Could not read model directory: {exc}", None, None
    values = {
        "status": "available" if valid else "corrupted",
        "actual_size_bytes": actual_size, "checksum": checksum, "checksum_valid": None,
        "validation_status": "valid" if valid else "invalid",
        "last_verified_at": verified_at, "last_error": error,
    }
    if valid:
        values["downloaded_at"] = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return values


def _verification_values(
    metadata: WhisperModelMetadata, *, missing_is_error: bool, allow_cache_import: bool = True
) -> dict:
    if metadata.backend == "pytorch":
        return _verify_pytorch(metadata, missing_is_error=missing_is_error)
    return _verify_faster_whisper(
        metadata,
        missing_is_error=missing_is_error,
        allow_cache_import=allow_cache_import,
    )


def verify_whisper_model(
    model: str, *, backend: str = "pytorch", missing_is_error: bool = True
) -> WhisperModelResponse:
    metadata = model_metadata(backend, model)
    identity = registry_identity(backend, model)
    ensure_whisper_model_registry()
    collection = get_database()[COLLECTION_NAME]
    current = collection.find_one(identity)
    if current and current.get("status") in {"downloading", "deleting"}:
        raise WhisperModelActionConflict(
            f"Whisper model {identity['model']} cannot be verified while {current['status']}"
        )
    starting_status = current.get("status") if current else None
    starting_updated_at = current.get("updated_at") if current else None
    values = _verification_values(
        metadata,
        missing_is_error=missing_is_error,
        allow_cache_import=not bool(current and current.get("cache_import_blocked")),
    )
    values["updated_at"] = utc_now()
    conditional: dict = {**identity, "status": starting_status}
    if starting_updated_at is not None:
        conditional["updated_at"] = starting_updated_at
    collection.update_one(conditional, {"$set": values})
    document = collection.find_one(identity)
    if document is None:
        raise RuntimeError(f"Whisper model registry entry disappeared: {backend}:{model}")
    return _serialize(document)


def scan_whisper_models(backend: str = "pytorch") -> list[WhisperModelResponse]:
    registry_identity(backend, "base")
    ensure_whisper_model_registry()
    collection = get_database()[COLLECTION_NAME]
    responses = []
    for model in SUPPORTED_WHISPER_MODELS:
        current = collection.find_one(registry_identity(backend, model))
        if current and current.get("status") in {"downloading", "deleting"}:
            responses.append(_serialize(current))
        else:
            responses.append(verify_whisper_model(model, backend=backend, missing_is_error=False))
    return responses


def is_whisper_model_available(model: str, backend: str = "pytorch") -> bool:
    try:
        identity = registry_identity(backend, model)
    except ValueError:
        return False
    ensure_whisper_model_registry()
    return get_database()[COLLECTION_NAME].count_documents({**identity, "status": "available"}, limit=1) == 1


def require_whisper_model_available(model: str, backend: str = "pytorch") -> dict:
    identity = registry_identity(backend, model)
    ensure_whisper_model_registry()
    document = get_database()[COLLECTION_NAME].find_one({**identity, "status": "available"})
    if document is None:
        raise WhisperModelUnavailableError(whisper_model_unavailable_message(model, backend))
    return document


def list_available_whisper_models(backend: str = "pytorch") -> list[AvailableWhisperModelResponse]:
    registry_identity(backend, "base")
    ensure_whisper_model_registry()
    documents = {
        document["model"]: document
        for document in get_database()[COLLECTION_NAME].find({
            "backend": backend, "model": {"$in": list(SUPPORTED_WHISPER_MODELS)}, "status": "available"
        })
    }
    return [
        AvailableWhisperModelResponse(
            backend=backend, model=model, file_name=documents[model]["file_name"],
            file_path=documents[model]["file_path"],
            actual_size_bytes=int(documents[model]["actual_size_bytes"]),
            last_verified_at=documents[model].get("last_verified_at"),
        )
        for model in SUPPORTED_WHISPER_MODELS
        if model in documents and documents[model].get("actual_size_bytes") is not None
    ]


def resolve_available_whisper_model_path(model: str, backend: str = "pytorch") -> Path:
    identity = registry_identity(backend, model)
    require_whisper_model_available(model, backend)
    verified = verify_whisper_model(model, backend=backend)
    if verified.status != "available":
        raise WhisperModelUnavailableError(whisper_model_unavailable_message(model, backend))
    root = whisper_model_directory(backend).resolve()
    path = whisper_model_path(backend, identity["model"]).resolve()
    expected_kind = path.is_file() if backend == "pytorch" else path.is_dir()
    if not path.is_relative_to(root) or not expected_kind:
        raise WhisperModelUnavailableError(whisper_model_unavailable_message(model, backend))
    return path


def whisper_download_directory(backend: str = "pytorch") -> Path:
    root = whisper_model_directory(backend).resolve()
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


def whisper_partial_path(model: str, backend: str = "pytorch") -> Path:
    identity = registry_identity(backend, model)
    suffix = ".part" if backend == "pytorch" else ".part-dir"
    return whisper_download_directory(backend) / f"{identity['model']}{suffix}"


def _registry_document(model: str, backend: str = "pytorch") -> dict:
    identity = registry_identity(backend, model)
    ensure_whisper_model_registry()
    document = get_database()[COLLECTION_NAME].find_one(identity)
    if document is None:
        raise ValueError(f"Unsupported Whisper model: {backend}:{model}")
    return document


def request_whisper_model_download(
    model: str, *, backend: str = "pytorch", retry: bool = False
) -> WhisperModelResponse:
    identity = registry_identity(backend, model)
    collection = get_database()[COLLECTION_NAME]
    current = _registry_document(model, backend)
    if current["status"] == "downloading":
        if retry:
            raise WhisperModelActionConflict(
                f"Whisper model {identity['model']} can only retry from failed, corrupted, or not_downloaded"
            )
        return _serialize(current)
    if current["status"] == "available":
        # A stale registry must not trigger a second download.
        verified = verify_whisper_model(model, backend=backend, missing_is_error=False)
        if verified.status == "available":
            return verified
        current = verified.model_dump()
    if current["status"] == "deleting":
        raise WhisperModelActionConflict(f"Whisper model {identity['model']} is being deleted")
    if current["status"] not in {"not_downloaded", "failed", "corrupted"}:
        raise WhisperModelActionConflict(
            f"Whisper model {identity['model']} cannot download from status {current['status']}"
        )
    now = utc_now()
    document = collection.find_one_and_update(
        {**identity, "status": current["status"]},
        {"$set": {
            "status": "downloading", "progress": 0, "download_started_at": None,
            "download_completed_at": None, "download_heartbeat_at": None,
            "download_worker_id": None, "cancel_requested": False, "last_error": None,
            "download_restart_requested": current["status"] == "corrupted",
            "operation_started_at": now, "updated_at": now,
        }},
        return_document=ReturnDocument.AFTER,
    )
    document = document or collection.find_one(identity)
    if document is None:
        raise RuntimeError(f"Whisper model registry entry disappeared: {backend}:{model}")
    return _serialize(document)


def cancel_whisper_model_download(model: str, backend: str = "pytorch") -> WhisperModelResponse:
    identity = registry_identity(backend, model)
    current = _registry_document(model, backend)
    if current["status"] != "downloading":
        raise WhisperModelActionConflict(f"Whisper model {identity['model']} is not queued or downloading")
    document = get_database()[COLLECTION_NAME].find_one_and_update(
        {**identity, "status": "downloading"},
        {"$set": {"cancel_requested": True, "updated_at": utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise WhisperModelActionConflict(f"Whisper model {identity['model']} is no longer downloading")
    return _serialize(document)


def _active_job_filter(backend: str, model: str) -> dict:
    backend_filter: dict
    if backend == "pytorch":
        backend_filter = {"$or": [
            {"transcription_backend": "pytorch"},
            {"transcription_backend": {"$exists": False}},
        ]}
    else:
        backend_filter = {"transcription_backend": backend}
    return {"model": {"$in": [model, "large"] if model == "large-v3" else model},
            "status": {"$in": ["queued", "processing"]}, **backend_filter}


def delete_whisper_model(model: str, backend: str = "pytorch") -> WhisperModelResponse:
    metadata = model_metadata(backend, model)
    identity = registry_identity(backend, model)
    root = whisper_model_directory(backend).resolve()
    model_path = whisper_model_path(backend, model)
    if not model_path.resolve(strict=False).is_relative_to(root):
        raise ValueError("Whisper model path is outside the configured model directory")
    if model_path.is_symlink():
        raise ValueError("Symbolic links are not valid model storage")
    database = get_database()
    collection = database[COLLECTION_NAME]
    now = utc_now()
    collection.update_one(identity, {"$pull": {"usage_leases": {"expires_at": {"$lte": now}}}})
    if database["transcription_jobs"].count_documents(_active_job_filter(backend, identity["model"]), limit=1):
        raise WhisperModelActionConflict(f"Whisper model {identity['model']} is used by an active transcription job")
    if backend == "pytorch" and database["live_sessions"].count_documents(
        {"model": {"$in": [identity["model"], "large"] if identity["model"] == "large-v3" else identity["model"]},
         "status": {"$in": ["active", "paused"]}}, limit=1
    ):
        raise WhisperModelActionConflict(f"Whisper model {identity['model']} is used by an active live session")
    current = collection.find_one(identity)
    if current is None:
        raise RuntimeError(f"Whisper model registry entry disappeared: {backend}:{model}")
    previous_status = current.get("status")
    if previous_status in {"downloading", "deleting"}:
        raise WhisperModelActionConflict(f"Whisper model {identity['model']} cannot be deleted while {previous_status}")
    deleting = collection.find_one_and_update(
        {**identity, "status": previous_status, "usage_leases": {"$size": 0}},
        {"$set": {"status": "deleting", "operation_started_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if deleting is None:
        raise WhisperModelActionConflict(f"Whisper model {identity['model']} cannot be deleted while active")
    try:
        if metadata.storage_kind == "checkpoint":
            model_path.unlink(missing_ok=True)
        elif model_path.exists():
            shutil.rmtree(model_path)
        partial = whisper_partial_path(model, backend)
        if partial.is_dir():
            shutil.rmtree(partial)
        else:
            partial.unlink(missing_ok=True)
    except OSError as exc:
        collection.update_one(
            {**identity, "status": "deleting"},
            {"$set": {"status": "failed", "last_error": f"Model deletion failed: {exc}",
                      "operation_started_at": None, "updated_at": utc_now()}},
        )
        raise
    now = utc_now()
    collection.update_one(identity, {"$set": {
        "status": "not_downloaded", "actual_size_bytes": None, "checksum": None,
        "checksum_valid": None, "validation_status": "not_verified", "downloaded_at": None,
        "last_verified_at": now, "last_error": None, "downloaded_bytes": 0,
        "progress": 0, "download_started_at": None, "download_completed_at": None,
        "download_heartbeat_at": None, "download_worker_id": None,
        "cancel_requested": False, "download_restart_requested": False,
        "cache_import_blocked": backend == "faster-whisper",
        "operation_started_at": None, "updated_at": now,
    }})
    document = collection.find_one(identity)
    if document is None:
        raise RuntimeError(f"Whisper model registry entry disappeared: {backend}:{model}")
    return _serialize(document)

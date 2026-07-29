"""Production audit, retention cleanup, and dependency readiness primitives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable, Protocol

from pymongo import ASCENDING

from ..config import Settings, get_settings
from ..database import get_database
from ..security import Principal, redact_value

AUDIT_COLLECTION = "security_audit_events"
_FORBIDDEN_AUDIT = re.compile(r"(?:transcript|audio(?:Content|Bytes|Pcm)?|text|translation|secret|token|password|path|checkpoint)", re.I)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_audit_metadata(metadata: dict[str, object] | None) -> dict[str, object]:
    return {
        key: redact_value(value)
        for key, value in (metadata or {}).items()
        if not _FORBIDDEN_AUDIT.search(key)
    }


class AuditSink(Protocol):
    def write(self, document: dict[str, object]) -> None: ...


class MongoAuditSink:
    def write(self, document: dict[str, object]) -> None:
        get_database()[AUDIT_COLLECTION].insert_one(document)


def ensure_production_hardening_indexes() -> None:
    collection = get_database()[AUDIT_COLLECTION]
    collection.create_index([("createdAt", ASCENDING)], name="audit_created_at")
    collection.create_index([("event", ASCENDING), ("createdAt", ASCENDING)], name="audit_event_created_at")


class MemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def write(self, document: dict[str, object]) -> None:
        self.events.append(document)


def audit_event(
    event: str, *, principal: Principal | None = None, session_id: str | None = None,
    outcome: str = "success", metadata: dict[str, object] | None = None,
    sink: AuditSink | None = None,
) -> bool:
    document: dict[str, object] = {
        "schemaVersion": 1, "event": event, "outcome": outcome,
        "actorId": principal.user_id if principal else None,
        "actorRole": principal.role if principal else None,
        "sessionId": session_id, "metadata": sanitize_audit_metadata(metadata),
        "createdAt": utc_now(),
    }
    try:
        (sink or MongoAuditSink()).write(document)
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class CleanupResult:
    dry_run: bool
    scanned: int
    eligible: int
    deleted: int
    limited: bool
    errors: tuple[str, ...] = ()


RETENTION_COLLECTIONS = {
    "session": ("pipeline_sessions", "updatedAt", "retention_session_metadata_days"),
    "legacy_session": ("live_sessions", "updated_at", "retention_session_metadata_days"),
    "segments": ("pipeline_audio_segments", "createdAt", "retention_session_metadata_days"),
    "speakers": ("pipeline_speaker_assignments", "updatedAt", "retention_session_metadata_days"),
    "jobs": ("pipeline_job_summaries", "updatedAt", "retention_session_metadata_days"),
    "transcript": ("pipeline_transcript_revisions", "createdAt", "retention_transcript_days"),
    "translation": ("pipeline_translation_revisions", "createdAt", "retention_translation_days"),
    "metrics": ("pipeline_metrics", "createdAt", "retention_metrics_days"),
    "audit": (AUDIT_COLLECTION, "createdAt", "retention_audit_days"),
}


def cleanup_retention(*, dry_run: bool = True, settings: Settings | None = None) -> CleanupResult:
    config = settings or get_settings()
    remaining = config.retention_cleanup_batch_size
    scanned = eligible = deleted = 0
    errors: list[str] = []
    now = utc_now()
    for _, (collection_name, timestamp, setting_name) in RETENTION_COLLECTIONS.items():
        if remaining <= 0:
            break
        cutoff = now - timedelta(days=int(getattr(config, setting_name)))
        try:
            collection = get_database()[collection_name]
            ids = [item["_id"] for item in collection.find({timestamp: {"$lt": cutoff}}, {"_id": 1}).limit(remaining)]
            scanned += len(ids); eligible += len(ids)
            if ids and not dry_run:
                deleted += collection.delete_many({"_id": {"$in": ids}}).deleted_count
            remaining -= len(ids)
        except Exception:
            errors.append(f"{collection_name}: cleanup unavailable")
    if remaining > 0:
        upload_root = (Path(config.storage_root).resolve() / "uploads").resolve()
        storage_root = Path(config.storage_root).resolve()
        cutoff_timestamp = (now - timedelta(days=config.retention_audio_days)).timestamp()
        try:
            if upload_root.is_relative_to(storage_root) and upload_root.is_dir():
                candidates = sorted(
                    (item for item in upload_root.iterdir() if item.is_file() and not item.is_symlink() and item.stat().st_mtime < cutoff_timestamp),
                    key=lambda item: item.stat().st_mtime,
                )[:remaining]
                scanned += len(candidates); eligible += len(candidates)
                if not dry_run:
                    for item in candidates:
                        resolved = item.resolve()
                        if resolved.parent != upload_root:
                            continue
                        resolved.unlink()
                        deleted += 1
                remaining -= len(candidates)
        except OSError:
            errors.append("audio storage: cleanup unavailable")
    return CleanupResult(dry_run, scanned, eligible, deleted, remaining == 0, tuple(errors))


def dependency_readiness(
    *, settings: Settings | None = None,
    mongo_ping: Callable[[], object] | None = None,
    worker_check: Callable[[], bool] | None = None,
    queue_check: Callable[[], bool] | None = None,
    persistence_check: Callable[[], bool] | None = None,
) -> dict[str, object]:
    config = settings or get_settings()
    checks: dict[str, dict[str, object]] = {}
    started = perf_counter()
    try:
        (mongo_ping or (lambda: get_database().command("ping")))()
        checks["mongodb"] = {"ready": True}
    except Exception:
        checks["mongodb"] = {"ready": False, "reason": "unavailable"}
    root = Path(config.storage_root).resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".readiness-probe"
        probe.touch(exist_ok=True); probe.unlink()
        checks["storage"] = {"ready": True}
    except OSError:
        checks["storage"] = {"ready": False, "reason": "unavailable"}
    checkpoint = {"Fast": "base.pt", "Balanced": "small.pt", "Accurate": "base.pt", "Private": "base.pt"}.get(config.security_profile)
    checks["model"] = {"ready": bool(checkpoint and (config.whisper_model_dir / checkpoint).is_file())}
    checks["workerSupervisor"] = {"ready": True if worker_check is None else bool(worker_check())}
    checks["queueCapacity"] = {"ready": True if queue_check is None else bool(queue_check())}
    checks["persistence"] = {"ready": True if persistence_check is None else bool(persistence_check())}
    ready = all(bool(item["ready"]) for item in checks.values())
    return {"status": "ready" if ready else "not_ready", "checks": checks,
            "latencyMs": round((perf_counter() - started) * 1000, 3)}

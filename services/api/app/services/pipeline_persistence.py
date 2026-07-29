"""Versioned pipeline persistence repository and non-blocking write service."""

from __future__ import annotations

import asyncio
import copy
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Protocol

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from ..database import get_database


SCHEMA_VERSION = 1
COLLECTIONS = {
    "session": "pipeline_sessions",
    "segment": "pipeline_audio_segments",
    "transcript": "pipeline_transcript_revisions",
    "translation": "pipeline_translation_revisions",
    "speaker": "pipeline_speaker_assignments",
    "job": "pipeline_job_summaries",
}
_SECRET = re.compile(r"(?:secret|password|passwd|token|api[_-]?key|credential|authorization|cookie)", re.I)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def redact_secrets(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SECRET.search(str(key)) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    return value


def versioned(document: dict) -> dict:
    result = copy.deepcopy(document)
    result["schemaVersion"] = SCHEMA_VERSION
    result.setdefault("createdAt", utc_now())
    result.setdefault("updatedAt", result["createdAt"])
    return redact_secrets(result)


class PipelineRepository(Protocol):
    def ensure_indexes(self) -> None: ...
    def write_session(self, value: dict) -> bool: ...
    def write_segment(self, value: dict) -> bool: ...
    def write_transcript(self, value: dict) -> bool: ...
    def write_translation(self, value: dict) -> bool: ...
    def write_speaker(self, value: dict) -> bool: ...
    def write_job(self, value: dict) -> bool: ...
    def rename_speaker(self, session_id: str, speaker_id: str, label: str) -> int: ...
    def restore(self, session_id: str) -> dict: ...


class InMemoryPipelineRepository:
    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self.segments: dict[tuple[str, str], dict] = {}
        self.transcripts: dict[tuple[str, str, int], dict] = {}
        self.translations: dict[tuple[str, str, int], dict] = {}
        self.speakers: dict[tuple[str, str], dict] = {}
        self.jobs: dict[str, dict] = {}

    def ensure_indexes(self) -> None:
        pass

    @staticmethod
    def _immutable(store: dict, key: object, value: dict) -> bool:
        current = store.get(key)
        if current is not None:
            if _comparable(current) == _comparable(value):
                return False
            raise ValueError("Immutable persisted entity conflict")
        store[key] = copy.deepcopy(value)
        return True

    def write_session(self, value: dict) -> bool:
        key = value["sessionId"]
        current = self.sessions.get(key)
        if current is None:
            self.sessions[key] = copy.deepcopy(value)
            return True
        created_at = current["createdAt"]
        current.update(copy.deepcopy(value))
        current["createdAt"] = created_at
        return True

    def write_segment(self, value: dict) -> bool:
        return self._immutable(self.segments, (value["sessionId"], value["segmentId"]), value)

    def write_transcript(self, value: dict) -> bool:
        key = (value["sessionId"], value["segmentId"], value["revision"])
        existing = [revision for session, segment, revision in self.transcripts if session == key[0] and segment == key[1]]
        if existing and value["revision"] < max(existing):
            raise ValueError("Transcript revision must be monotonic")
        return self._immutable(self.transcripts, key, value)

    def write_translation(self, value: dict) -> bool:
        key = (value["sessionId"], value["segmentId"], value["revision"])
        existing = [revision for session, segment, revision in self.translations if session == key[0] and segment == key[1]]
        if existing and value["revision"] < max(existing):
            raise ValueError("Translation revision must be monotonic")
        return self._immutable(self.translations, key, value)

    def write_speaker(self, value: dict) -> bool:
        key = (value["sessionId"], value["segmentId"])
        current = self.speakers.get(key)
        if current and current.get("speakerId") != value.get("speakerId"):
            raise ValueError("Speaker assignment identity is immutable")
        self.speakers[key] = copy.deepcopy(value)
        return True

    def write_job(self, value: dict) -> bool:
        current = self.jobs.get(value["jobId"])
        self.jobs[value["jobId"]] = copy.deepcopy(value)
        return current is None or _comparable(current) != _comparable(value)

    def rename_speaker(self, session_id: str, speaker_id: str, label: str) -> int:
        changed = 0
        for (owner, _), value in self.speakers.items():
            if owner == session_id and value["speakerId"] == speaker_id:
                value["speakerLabel"] = label
                value["updatedAt"] = utc_now()
                changed += 1
        return changed

    def restore(self, session_id: str) -> dict:
        return {
            "session": copy.deepcopy(self.sessions.get(session_id)),
            "segments": _owned(self.segments, session_id),
            "transcriptRevisions": _owned(self.transcripts, session_id),
            "translationRevisions": _owned(self.translations, session_id),
            "speakerAssignments": _owned(self.speakers, session_id),
            "processingJobs": [copy.deepcopy(item) for item in self.jobs.values() if item.get("sessionId") == session_id],
        }


class MongoPipelineRepository:
    def ensure_indexes(self) -> None:
        db = get_database()
        db[COLLECTIONS["session"]].create_index([("sessionId", ASCENDING)], unique=True)
        db[COLLECTIONS["segment"]].create_index([("sessionId", ASCENDING), ("segmentId", ASCENDING)], unique=True)
        db[COLLECTIONS["transcript"]].create_index([("sessionId", ASCENDING), ("segmentId", ASCENDING), ("revision", ASCENDING)], unique=True)
        db[COLLECTIONS["translation"]].create_index([("sessionId", ASCENDING), ("segmentId", ASCENDING), ("revision", ASCENDING)], unique=True)
        db[COLLECTIONS["speaker"]].create_index([("sessionId", ASCENDING), ("segmentId", ASCENDING)], unique=True)
        db[COLLECTIONS["job"]].create_index([("jobId", ASCENDING)], unique=True)
        for name in COLLECTIONS.values():
            db[name].create_index([("createdAt", ASCENDING)])

    def _immutable(self, kind: str, key: dict, value: dict) -> bool:
        collection = get_database()[COLLECTIONS[kind]]
        current = collection.find_one(key, {"_id": 0})
        if current is not None:
            if _comparable(current) == _comparable(value):
                return False
            raise ValueError("Immutable persisted entity conflict")
        try:
            collection.insert_one(copy.deepcopy(value))
            return True
        except DuplicateKeyError:
            return False

    def write_session(self, value: dict) -> bool:
        get_database()[COLLECTIONS["session"]].update_one(
            {"sessionId": value["sessionId"]},
            {"$set": {key: item for key, item in value.items() if key != "createdAt"}, "$setOnInsert": {"createdAt": value["createdAt"]}}, upsert=True,
        )
        return True

    def write_segment(self, value: dict) -> bool:
        return self._immutable("segment", {"sessionId": value["sessionId"], "segmentId": value["segmentId"]}, value)

    def write_transcript(self, value: dict) -> bool:
        latest = get_database()[COLLECTIONS["transcript"]].find_one({"sessionId": value["sessionId"], "segmentId": value["segmentId"]}, sort=[("revision", -1)])
        if latest and value["revision"] < latest["revision"]:
            raise ValueError("Transcript revision must be monotonic")
        return self._immutable("transcript", {"sessionId": value["sessionId"], "segmentId": value["segmentId"], "revision": value["revision"]}, value)

    def write_translation(self, value: dict) -> bool:
        latest = get_database()[COLLECTIONS["translation"]].find_one({"sessionId": value["sessionId"], "segmentId": value["segmentId"]}, sort=[("revision", -1)])
        if latest and value["revision"] < latest["revision"]:
            raise ValueError("Translation revision must be monotonic")
        return self._immutable("translation", {"sessionId": value["sessionId"], "segmentId": value["segmentId"], "revision": value["revision"]}, value)

    def write_speaker(self, value: dict) -> bool:
        collection = get_database()[COLLECTIONS["speaker"]]
        key = {"sessionId": value["sessionId"], "segmentId": value["segmentId"]}
        current = collection.find_one(key)
        if current and current.get("speakerId") != value.get("speakerId"):
            raise ValueError("Speaker assignment identity is immutable")
        collection.update_one(key, {"$set": value}, upsert=True)
        return True

    def write_job(self, value: dict) -> bool:
        get_database()[COLLECTIONS["job"]].update_one({"jobId": value["jobId"]}, {"$set": value}, upsert=True)
        return True

    def rename_speaker(self, session_id: str, speaker_id: str, label: str) -> int:
        result = get_database()[COLLECTIONS["speaker"]].update_many({"sessionId": session_id, "speakerId": speaker_id}, {"$set": {"speakerLabel": label, "updatedAt": utc_now()}})
        return result.modified_count

    def restore(self, session_id: str) -> dict:
        db = get_database()
        return {
            "session": db[COLLECTIONS["session"]].find_one({"sessionId": session_id}, {"_id": 0}),
            "segments": list(db[COLLECTIONS["segment"]].find({"sessionId": session_id}, {"_id": 0}).sort("sequenceStart")),
            "transcriptRevisions": list(db[COLLECTIONS["transcript"]].find({"sessionId": session_id}, {"_id": 0}).sort([("segmentId", 1), ("revision", 1)])),
            "translationRevisions": list(db[COLLECTIONS["translation"]].find({"sessionId": session_id}, {"_id": 0}).sort([("segmentId", 1), ("revision", 1)])),
            "speakerAssignments": list(db[COLLECTIONS["speaker"]].find({"sessionId": session_id}, {"_id": 0})),
            "processingJobs": list(db[COLLECTIONS["job"]].find({"sessionId": session_id}, {"_id": 0})),
        }


@dataclass(frozen=True)
class PersistenceWrite:
    kind: str
    value: dict
    attempt: int = 0


class PipelinePersistenceService:
    def __init__(self, repository: PipelineRepository, *, capacity: int = 256, max_retries: int = 2) -> None:
        self.repository = repository
        self.capacity = capacity
        self.max_retries = max_retries
        self._queue: asyncio.Queue[PersistenceWrite | None] = asyncio.Queue(capacity)
        self._worker: asyncio.Task | None = None
        self._closed = False
        self._degraded: set[str] = set()
        self._metrics = {"persistence_writes": 0, "successful_writes": 0, "failed_writes": 0, "retries": 0, "duplicate_writes_ignored": 0, "restore_count": 0, "restore_latency_total_ms": 0.0}

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    def submit(self, kind: str, value: dict) -> bool:
        if self._closed or self._queue.full():
            self._metrics["failed_writes"] += 1
            self._degraded.add(value.get("sessionId", "unknown"))
            return False
        self._metrics["persistence_writes"] += 1
        self._queue.put_nowait(PersistenceWrite(kind, versioned(value)))
        return True

    async def join(self) -> None:
        await self._queue.join()

    async def close(self) -> None:
        self._closed = True
        if self._worker is not None:
            await self._queue.join()
            await self._queue.put(None)
            await self._worker
            self._worker = None

    async def restore(self, session_id: str) -> dict:
        started = perf_counter()
        result = await asyncio.to_thread(self.repository.restore, session_id)
        self._metrics["restore_count"] += 1
        self._metrics["restore_latency_total_ms"] += (perf_counter() - started) * 1000
        return result

    async def rename_speaker(self, session_id: str, speaker_id: str, label: str) -> int:
        try:
            changed = await asyncio.to_thread(
                self.repository.rename_speaker, session_id, speaker_id, label
            )
            self._metrics["successful_writes"] += 1
            return changed
        except Exception:
            self._metrics["failed_writes"] += 1
            self._degraded.add(session_id)
            return 0

    def metrics(self) -> dict[str, int | float]:
        restores = self._metrics["restore_count"]
        return {**self._metrics, "restore_latency_ms": round(self._metrics["restore_latency_total_ms"] / restores, 3) if restores else 0.0, "degraded_sessions": len(self._degraded), "queue_depth": self._queue.qsize()}

    async def _run(self) -> None:
        methods = {kind: getattr(self.repository, f"write_{kind}") for kind in COLLECTIONS}
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                try:
                    changed = await asyncio.to_thread(methods[item.kind], item.value)
                    if changed:
                        self._metrics["successful_writes"] += 1
                    else:
                        self._metrics["duplicate_writes_ignored"] += 1
                    self._degraded.discard(item.value.get("sessionId", "unknown"))
                except ValueError:
                    self._metrics["failed_writes"] += 1
                    self._degraded.add(item.value.get("sessionId", "unknown"))
                except Exception:
                    if item.attempt < self.max_retries:
                        self._metrics["retries"] += 1
                        await self._queue.put(PersistenceWrite(item.kind, item.value, item.attempt + 1))
                    else:
                        self._metrics["failed_writes"] += 1
                        self._degraded.add(item.value.get("sessionId", "unknown"))
            finally:
                self._queue.task_done()


def _comparable(value: dict) -> dict:
    return {key: item for key, item in value.items() if key not in {"createdAt", "updatedAt", "_id"}}


def _owned(store: dict, session_id: str) -> list[dict]:
    return [copy.deepcopy(item) for key, item in store.items() if isinstance(key, tuple) and key[0] == session_id]

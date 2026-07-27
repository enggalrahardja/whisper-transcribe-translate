import logging
import os
import signal
import socket
import threading
from datetime import datetime, timedelta, timezone
from time import monotonic

from bson import ObjectId
from pymongo import ASCENDING, ReturnDocument

from .config import get_settings
from .database import close_database, get_database
from .services.jobs import COLLECTION_NAME as JOBS_COLLECTION, ensure_job_indexes
from .services.application_settings import RUNTIME_COLLECTION, get_application_settings, run_retention_cleanup
from .services.media_files import COLLECTION_NAME as MEDIA_COLLECTION, ensure_media_file_indexes
from .services.translation_adapter import TranslationAdapter
from .services.transcripts import COLLECTION_NAME as TRANSCRIPTS_COLLECTION, ensure_transcript_indexes
from .services.whisper_adapter import WhisperAdapter
from .services.storage import resolve_storage_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("transcription-worker")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TranscriptionWorker:
    def __init__(self, slot: int | None = None) -> None:
        self.slot = slot
        self.settings = get_settings()
        self.application_settings = get_application_settings(force=True)
        self.database = get_database()
        self.jobs = self.database[JOBS_COLLECTION]
        self.media_files = self.database[MEDIA_COLLECTION]
        self.transcripts = self.database[TRANSCRIPTS_COLLECTION]
        base_worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.worker_id = base_worker_id if slot is None else f"{base_worker_id}:{slot}"
        self.adapter = WhisperAdapter(device=self.application_settings.transcription.device)
        self.translation_adapter = TranslationAdapter()
        self.stopping = threading.Event()
        self.current_job_id: ObjectId | None = None
        self.heartbeat_thread: threading.Thread | None = None
        self.heartbeat_stop = threading.Event()
        self.job_cancel_requested = threading.Event()
        self.last_runtime_update = 0.0
        self.last_recovery_check = 0.0
        self.last_cleanup_check = monotonic()

    def ensure_indexes(self) -> None:
        ensure_job_indexes()
        ensure_media_file_indexes()
        ensure_transcript_indexes()

    def recover_stale_jobs(self) -> int:
        now = utc_now()
        stale_seconds = get_application_settings().worker_processing.stale_heartbeat_threshold_seconds
        cutoff = now - timedelta(seconds=stale_seconds)
        cancelled_jobs = list(
            self.jobs.find(
                {
                    "status": "processing",
                    "cancellation_requested": True,
                    "$or": [
                        {"heartbeat_at": {"$lt": cutoff}},
                        {"heartbeat_at": {"$exists": False}},
                        {"heartbeat_at": None},
                    ],
                },
                {"_id": 1},
            )
        )
        if cancelled_jobs:
            cancelled_ids = [job["_id"] for job in cancelled_jobs]
            self.jobs.update_many(
                {"_id": {"$in": cancelled_ids}, "status": "processing", "cancellation_requested": True},
                {
                    "$set": {"status": "cancelled", "completed_at": now, "updated_at": now},
                    "$unset": {"transcript_id": ""},
                },
            )
            self.transcripts.delete_many({"job_id": {"$in": cancelled_ids}})

        result = self.jobs.update_many(
            {
                "status": "processing",
                "task": {"$in": ["transcribe", "translate"]},
                "cancellation_requested": {"$ne": True},
                "$or": [
                    {"heartbeat_at": {"$lt": cutoff}},
                    {"heartbeat_at": {"$exists": False}},
                    {"heartbeat_at": None},
                ],
            },
            {
                "$set": {
                    "status": "queued",
                    "progress": 0,
                    "error": None,
                    "updated_at": utc_now(),
                    "recovered_at": utc_now(),
                },
                "$unset": {
                    "worker_id": "",
                    "heartbeat_at": "",
                    "started_at": "",
                    "completed_at": "",
                },
            },
        )
        return result.modified_count

    def claim_job(self) -> dict | None:
        now = utc_now()
        return self.jobs.find_one_and_update(
            {
                "status": "queued",
                "task": {"$in": ["transcribe", "translate"]},
                "cancellation_requested": {"$ne": True},
            },
            {
                "$set": {
                    "status": "processing",
                    "progress": 0,
                    "error": None,
                    "worker_id": self.worker_id,
                    "started_at": now,
                    "heartbeat_at": now,
                    "updated_at": now,
                },
                "$unset": {"cancellation_requested": "", "completed_at": ""},
            },
            sort=[("created_at", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )

    def update_progress(self, job_id: ObjectId, progress: int) -> bool:
        now = utc_now()
        result = self.jobs.update_one(
            {
                "_id": job_id,
                "status": "processing",
                "worker_id": self.worker_id,
                "cancellation_requested": {"$ne": True},
            },
            {"$set": {"progress": max(0, min(progress, 99)), "heartbeat_at": now, "updated_at": now}},
        )
        if result.matched_count == 0:
            self.job_cancel_requested.set()
            return False
        return True

    def should_cancel_current_job(self) -> bool:
        if self.stopping.is_set() or self.job_cancel_requested.is_set():
            return True
        if self.current_job_id is None:
            return False
        document = self.jobs.find_one(
            {
                "_id": self.current_job_id,
                "status": "processing",
                "worker_id": self.worker_id,
            },
            {"cancellation_requested": 1},
        )
        if document is None or document.get("cancellation_requested", False):
            self.job_cancel_requested.set()
            return True
        return False

    def start_heartbeat(self, job_id: ObjectId) -> None:
        self.heartbeat_stop.clear()

        def heartbeat() -> None:
            while True:
                worker_settings = get_application_settings().worker_processing
                heartbeat_interval = min(
                    self.settings.worker_heartbeat_interval_seconds,
                    max(1.0, worker_settings.stale_heartbeat_threshold_seconds / 3),
                )
                if self.heartbeat_stop.wait(heartbeat_interval):
                    return
                if self.stopping.is_set():
                    return
                if self.current_job_id != job_id:
                    return
                now = utc_now()
                result = self.jobs.update_one(
                    {
                        "_id": job_id,
                        "status": "processing",
                        "worker_id": self.worker_id,
                        "cancellation_requested": {"$ne": True},
                    },
                    {"$set": {"heartbeat_at": now, "updated_at": now}},
                )
                if result.matched_count == 0:
                    self.job_cancel_requested.set()
                    return
                self.report_runtime(force=True)

        self.heartbeat_thread = threading.Thread(target=heartbeat, name="worker-heartbeat", daemon=True)
        self.heartbeat_thread.start()

    def stop_heartbeat(self) -> None:
        if self.heartbeat_thread is not None:
            self.heartbeat_stop.set()
            self.heartbeat_thread.join(timeout=self.settings.worker_heartbeat_interval_seconds + 1)
            self.heartbeat_thread = None

    def complete_job(self, job: dict, transcript_id: ObjectId) -> bool:
        now = utc_now()
        result = self.jobs.update_one(
            {
                "_id": job["_id"],
                "status": "processing",
                "worker_id": self.worker_id,
                "cancellation_requested": {"$ne": True},
            },
            {
                "$set": {
                    "status": "completed",
                    "progress": 100,
                    "transcript_id": transcript_id,
                    "heartbeat_at": now,
                    "completed_at": now,
                    "updated_at": now,
                    "error": None,
                }
            },
        )
        return result.modified_count == 1

    def fail_job(self, job_id: ObjectId, error: str) -> bool:
        now = utc_now()
        result = self.jobs.update_one(
            {
                "_id": job_id,
                "status": "processing",
                "worker_id": self.worker_id,
                "cancellation_requested": {"$ne": True},
            },
            {
                "$set": {
                    "status": "failed",
                    "error": error,
                    "heartbeat_at": now,
                    "completed_at": now,
                    "updated_at": now,
                }
            },
        )
        return result.modified_count == 1

    def cancel_current_job(self) -> bool:
        if self.current_job_id is None:
            return False
        now = utc_now()
        result = self.jobs.update_one(
            {
                "_id": self.current_job_id,
                "status": "processing",
                "worker_id": self.worker_id,
                "cancellation_requested": True,
            },
            {
                "$set": {
                    "status": "cancelled",
                    "completed_at": now,
                    "updated_at": now,
                    "error": None,
                },
                "$unset": {"transcript_id": ""},
            },
        )
        if result.modified_count == 1:
            self.transcripts.delete_many({"job_id": self.current_job_id})
            return True
        return False

    def release_current_job(self) -> None:
        if self.current_job_id is None:
            return
        if self.cancel_current_job():
            return
        self.jobs.update_one(
            {
                "_id": self.current_job_id,
                "status": "processing",
                "worker_id": self.worker_id,
                "cancellation_requested": {"$ne": True},
            },
            {
                "$set": {"status": "queued", "progress": 0, "updated_at": utc_now()},
                "$unset": {"worker_id": "", "heartbeat_at": "", "started_at": ""},
            },
        )

    def save_transcript(self, job: dict, result: dict, translated_text: str | None = None) -> ObjectId:
        now = utc_now()
        original_text = str(result.get("text", "")).strip()
        source_language = str(result.get("language") or job.get("language") or "unknown")
        original_segments = result.get("segments", [])
        is_translation = job.get("task") == "translate"
        document = {
            "job_id": job["_id"],
            "media_file_id": job["media_file_id"],
            "text": translated_text if is_translation else original_text,
            "language": source_language,
            "segments": original_segments,
            "original_text": original_text,
            "translated_text": translated_text if is_translation else None,
            "source_language": source_language,
            "target_language": job.get("target_language") if is_translation else None,
            "original_segments": original_segments,
            "translated_segments": None,
            "created_at": now,
        }
        self.transcripts.update_one(
            {"job_id": job["_id"]},
            {"$setOnInsert": document},
            upsert=True,
        )
        transcript = self.transcripts.find_one({"job_id": job["_id"]}, {"_id": 1})
        if transcript is None:
            raise RuntimeError("Transcript could not be saved")
        return transcript["_id"]

    def process_job(self, job: dict) -> None:
        job_id = job["_id"]
        self.job_cancel_requested.clear()
        self.current_job_id = job_id
        self.start_heartbeat(job_id)
        logger.info("Processing job %s with worker %s", job_id, self.worker_id)

        try:
            existing_transcript = self.transcripts.find_one({"job_id": job_id}, {"_id": 1})
            if existing_transcript is not None:
                if self.complete_job(job, existing_transcript["_id"]):
                    logger.info("Completed recovered job %s from existing transcript", job_id)
                elif self.cancel_current_job():
                    logger.info("Cancelled recovered job %s", job_id)
                return

            media_file_id = job.get("media_file_id")
            if not isinstance(media_file_id, ObjectId):
                raise FileNotFoundError("Media file reference is missing for this job")

            media_file = self.media_files.find_one({"_id": media_file_id})
            if media_file is None:
                raise FileNotFoundError(f"Media file record {media_file_id} was not found")

            stored_path = media_file.get("stored_path")
            if not stored_path:
                raise FileNotFoundError(f"Media file {media_file_id} has no stored_path")

            try:
                audio_path = resolve_storage_file(stored_path)
            except (ValueError, FileNotFoundError) as exc:
                raise FileNotFoundError(f"Media file is missing or outside storage: {stored_path}") from exc

            if not self.update_progress(job_id, 5):
                self.cancel_current_job()
                return
            transcription_settings = get_application_settings().transcription
            model_name = str(job.get("model", "base"))
            self.adapter.load_model(model_name)
            if self.should_cancel_current_job() or not self.update_progress(job_id, 15):
                self.cancel_current_job()
                return

            is_translation = job.get("task") == "translate"

            def on_whisper_progress(percentage: int) -> None:
                progress_range = 0.4 if is_translation else 0.6
                mapped_progress = 30 + int(max(0, min(percentage, 100)) * progress_range)
                self.update_progress(job_id, min(mapped_progress, 69 if is_translation else 89))

            if not self.update_progress(job_id, 30):
                self.cancel_current_job()
                return
            result = self.adapter.transcribe(
                audio_path,
                model_name=model_name,
                language=str(job.get("language", "auto")),
                progress_callback=on_whisper_progress,
                cancel_callback=self.should_cancel_current_job,
                fp16=transcription_settings.fp16,
                beam_size=transcription_settings.beam_size,
                temperature=transcription_settings.temperature,
                initial_prompt=transcription_settings.initial_prompt,
                word_timestamps=transcription_settings.word_timestamps,
            )
            original_text = str(result.get("text", "")).strip()
            if is_translation and not original_text:
                raise ValueError("Transcription is empty; there is no text to save or translate")

            translated_text: str | None = None
            if is_translation:
                if self.should_cancel_current_job() or not self.update_progress(job_id, 75):
                    self.cancel_current_job()
                    return
                translated_text = self.translation_adapter.translate(
                    original_text,
                    target_language=str(job.get("target_language") or ""),
                    cancel_callback=self.should_cancel_current_job,
                )

            if self.should_cancel_current_job() or not self.update_progress(job_id, 90):
                self.cancel_current_job()
                return
            transcript_id = self.save_transcript(job, result, translated_text=translated_text)
            if self.complete_job(job, transcript_id):
                logger.info("Completed job %s with transcript %s", job_id, transcript_id)
            elif self.cancel_current_job():
                logger.info("Cancelled job %s after transcription", job_id)
        except InterruptedError:
            if self.cancel_current_job():
                logger.info("Cancelled interrupted job %s", job_id)
            else:
                self.release_current_job()
                logger.info("Released interrupted job %s", job_id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if self.cancel_current_job():
                logger.info("Cancelled job %s while handling error", job_id)
            elif self.fail_job(job_id, error):
                logger.exception("Failed job %s: %s", job_id, error)
            elif self.cancel_current_job():
                logger.info("Cancelled job %s after a concurrent error", job_id)
        finally:
            self.current_job_id = None
            self.stop_heartbeat()
            self.report_runtime(force=True)

    def report_runtime(self, force: bool = False, stopped: bool = False) -> None:
        current_monotonic = monotonic()
        if not force and current_monotonic - self.last_runtime_update < 2:
            return
        latest_settings = get_application_settings()
        now = utc_now()
        self.database[RUNTIME_COLLECTION].update_one(
            {"worker_id": self.worker_id},
            {
                "$set": {
                    "worker_id": self.worker_id,
                    "status": "stopped" if stopped else "online",
                    "last_heartbeat": now,
                    "current_job": str(self.current_job_id) if self.current_job_id else None,
                    "effective_device": self.adapter.effective_device,
                    "effective_device_setting": self.adapter.device_setting,
                    "configured_concurrency": self.application_settings.transcription.maximum_concurrent_transcription_jobs,
                    "settings_version": latest_settings.version,
                    "updated_at": now,
                },
                "$setOnInsert": {"started_at": now},
            },
            upsert=True,
        )
        self.last_runtime_update = current_monotonic

    def handle_shutdown(self, _signal_number: int, _frame: object) -> None:
        logger.info("Worker %s is stopping", self.worker_id)
        self.stopping.set()
        self.release_current_job()
        self.report_runtime(force=True, stopped=True)
        raise SystemExit(0)

    def run(self, register_signals: bool = True) -> None:
        if register_signals:
            signal.signal(signal.SIGINT, self.handle_shutdown)
            signal.signal(signal.SIGTERM, self.handle_shutdown)
        self.ensure_indexes()
        recovered = self.recover_stale_jobs()
        logger.info("Worker %s started; recovered %s stale job(s)", self.worker_id, recovered)
        self.report_runtime(force=True)

        while not self.stopping.is_set():
            latest_settings = get_application_settings()
            worker_settings = latest_settings.worker_processing
            self.report_runtime()
            if monotonic() - self.last_recovery_check >= 60:
                self.recover_stale_jobs()
                self.last_recovery_check = monotonic()
            if (self.slot in {None, 1} and latest_settings.storage_retention.cleanup_enabled
                    and monotonic() - self.last_cleanup_check >= 3600):
                cleanup = run_retention_cleanup()
                self.last_cleanup_check = monotonic()
                logger.info(
                    "Retention cleanup removed %s file(s) and reclaimed %s bytes",
                    cleanup.media_files_deleted + cleanup.export_files_deleted + cleanup.orphan_files_deleted,
                    cleanup.bytes_reclaimed,
                )
            if not worker_settings.worker_enabled:
                self.stopping.wait(worker_settings.polling_interval_seconds)
                continue
            job = self.claim_job()
            if job is None:
                self.stopping.wait(worker_settings.polling_interval_seconds)
                continue
            self.process_job(job)
            current = self.jobs.find_one({"_id": job["_id"]}, {"status": 1})
            if current and current.get("status") == "failed" and worker_settings.retry_delay_seconds:
                self.stopping.wait(worker_settings.retry_delay_seconds)


def main() -> None:
    concurrency = get_application_settings(force=True).transcription.maximum_concurrent_transcription_jobs
    if concurrency == 1:
        worker = TranscriptionWorker()
        try:
            worker.run()
        finally:
            worker.release_current_job()
            worker.report_runtime(force=True, stopped=True)
            close_database()
        return

    workers = [TranscriptionWorker(slot=index + 1) for index in range(concurrency)]
    threads = [threading.Thread(target=worker.run, args=(False,), name=f"worker-slot-{index + 1}") for index, worker in enumerate(workers)]

    def stop_workers(_signal_number: int, _frame: object) -> None:
        logger.info("Stopping %s transcription worker slots", len(workers))
        for worker in workers:
            worker.stopping.set()
            worker.release_current_job()

    signal.signal(signal.SIGINT, stop_workers)
    signal.signal(signal.SIGTERM, stop_workers)
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        for worker in workers:
            worker.stopping.set()
            worker.release_current_job()
            worker.report_runtime(force=True, stopped=True)
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=5)
        close_database()


if __name__ == "__main__":
    main()

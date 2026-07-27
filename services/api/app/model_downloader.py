import logging
import os
import re
import signal
import socket
import stat
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

import fcntl

from pymongo import ReturnDocument

from .config import get_settings
from .database import close_database, get_database
from .services.whisper_model_metadata import WHISPER_MODEL_METADATA, WhisperModelMetadata
from .services.whisper_models import (
    COLLECTION_NAME,
    HASH_CHUNK_SIZE,
    ensure_whisper_model_registry,
    whisper_model_directory,
    whisper_partial_path,
)

LOGGER = logging.getLogger("whisper-model-downloader")
USER_AGENT = "Whisper-Transcribe-Translate-Model-Downloader/1.0"
DOWNLOAD_CHUNK_SIZE = 256 * 1024
CONTENT_RANGE_PATTERN = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")


class DownloadCancelled(Exception):
    pass


class DownloaderStopping(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WhisperModelDownloader:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.database = get_database()
        self.collection = self.database[COLLECTION_NAME]
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.stopping = threading.Event()

    def stop(self, *_: object) -> None:
        self.stopping.set()

    def recover_interrupted_downloads(self) -> int:
        return self.recover_stale_downloads()

    def recover_stale_downloads(self) -> int:
        cutoff = utc_now() - timedelta(seconds=self.settings.whisper_download_stale_seconds)
        result = self.collection.update_many(
            {
                "status": "downloading",
                "download_worker_id": {"$ne": None},
                "$or": [
                    {"download_heartbeat_at": {"$lt": cutoff}},
                    {"download_heartbeat_at": None},
                    {"download_heartbeat_at": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    "download_worker_id": None,
                    "download_heartbeat_at": None,
                    "last_error": "Stale download recovered; waiting to resume",
                    "updated_at": utc_now(),
                }
            },
        )
        return result.modified_count

    def finalize_queued_cancellation(self) -> bool:
        document = self.collection.find_one(
            {
                "status": "downloading",
                "cancel_requested": True,
                "download_worker_id": None,
            },
            sort=[("updated_at", 1)],
        )
        if document is None:
            return False
        model = document["model"]
        self._remove_partial(model)
        now = utc_now()
        self.collection.update_one(
            {
                "model": model,
                "status": "downloading",
                "cancel_requested": True,
                "download_worker_id": None,
            },
            {
                "$set": {
                    "status": "failed",
                    "downloaded_bytes": 0,
                    "progress": 0,
                    "download_heartbeat_at": now,
                    "download_completed_at": now,
                    "cancel_requested": False,
                    "last_error": "Download cancelled",
                    "download_restart_requested": False,
                    "operation_started_at": None,
                    "updated_at": now,
                }
            },
        )
        return True

    def claim_download(self) -> dict | None:
        now = utc_now()
        return self.collection.find_one_and_update(
            {
                "status": "downloading",
                "download_worker_id": None,
                "cancel_requested": {"$ne": True},
            },
            {
                "$set": {
                    "download_worker_id": self.worker_id,
                    "download_started_at": now,
                    "download_heartbeat_at": now,
                    "last_error": None,
                    "updated_at": now,
                },
                "$inc": {"attempt": 1},
            },
            sort=[("updated_at", 1)],
            return_document=ReturnDocument.AFTER,
        )

    def _owned_update(self, model: str, values: dict) -> None:
        values["updated_at"] = utc_now()
        result = self.collection.update_one(
            {
                "model": model,
                "status": "downloading",
                "download_worker_id": self.worker_id,
            },
            {"$set": values},
        )
        if result.matched_count != 1:
            raise DownloaderStopping

    def _best_effort_owned_update(self, model: str, values: dict) -> None:
        try:
            self._owned_update(model, values)
        except Exception:
            LOGGER.exception("Could not persist final download state for %s", model)

    def _check_control(self, model: str) -> None:
        if self.stopping.is_set():
            raise DownloaderStopping
        document = self.collection.find_one(
            {"model": model}, {"cancel_requested": 1, "download_worker_id": 1}
        )
        if document is None or document.get("download_worker_id") != self.worker_id:
            raise DownloaderStopping
        if document.get("cancel_requested"):
            raise DownloadCancelled

    def _remove_partial(self, model: str) -> None:
        try:
            whisper_partial_path(model).unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _progress(downloaded: int, total: int | None) -> float:
        if not total:
            return 0
        return min(99, downloaded * 100 / total)

    @contextmanager
    def _model_file_lock(self, model: str):
        lock_path = whisper_partial_path(model).with_suffix(".lock")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            os.chmod(lock_path, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _response_total(self, response: object, offset: int) -> int | None:
        headers = response.headers
        if offset:
            match = CONTENT_RANGE_PATTERN.match(headers.get("Content-Range", ""))
            if match and match.group(3) != "*":
                return int(match.group(3))
        content_length = headers.get("Content-Length")
        return offset + int(content_length) if content_length else None

    def _open_download(self, metadata: WhisperModelMetadata, offset: int):
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(metadata.source_url, headers=headers)
        response = urllib.request.urlopen(
            request, timeout=self.settings.whisper_download_timeout_seconds
        )
        if not offset:
            return response, 0

        content_range = response.headers.get("Content-Range", "")
        match = CONTENT_RANGE_PATTERN.match(content_range)
        if response.status == 206 and match and int(match.group(1)) == offset:
            return response, offset

        response.close()
        LOGGER.info("%s did not provide a safe range response; restarting", metadata.model)
        request = urllib.request.Request(
            metadata.source_url,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
        )
        return (
            urllib.request.urlopen(
                request, timeout=self.settings.whisper_download_timeout_seconds
            ),
            0,
        )

    def _download_once(self, metadata: WhisperModelMetadata) -> tuple[Path, int, int | None]:
        model = metadata.model
        partial_path = whisper_partial_path(model)
        offset = partial_path.stat().st_size if partial_path.is_file() else 0
        try:
            response, accepted_offset = self._open_download(metadata, offset)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and offset:
                LOGGER.info("%s partial is not resumable; restarting from zero", model)
                response, accepted_offset = self._open_download(metadata, 0)
                self._owned_update(
                    model,
                    {
                        "downloaded_bytes": 0,
                        "progress": 0,
                        "last_error": "Partial was incompatible with remote size; restarted from zero",
                        "download_heartbeat_at": utc_now(),
                    },
                )
                offset = 0
            else:
                raise

        if accepted_offset == 0 and offset:
            self._owned_update(
                model,
                {
                    "downloaded_bytes": 0,
                    "progress": 0,
                    "last_error": "Server did not accept resume; restarted from zero",
                    "download_heartbeat_at": utc_now(),
                },
            )
            offset = 0
        total = self._response_total(response, accepted_offset)
        mode = "ab" if accepted_offset else "wb"
        downloaded = accepted_offset
        self._owned_update(
            model,
            {
                "expected_size_bytes": total,
                "downloaded_bytes": downloaded,
                "progress": self._progress(downloaded, total),
                "download_heartbeat_at": utc_now(),
            },
        )
        last_update = monotonic()
        if partial_path.is_symlink():
            response.close()
            raise OSError("Partial model path cannot be a symbolic link")
        flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if mode == "ab" else os.O_TRUNC)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(partial_path, flags, 0o600)
        try:
            os.chmod(partial_path, 0o600)
        except Exception:
            os.close(descriptor)
            response.close()
            raise
        with response, os.fdopen(descriptor, mode) as output:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                now_monotonic = monotonic()
                if now_monotonic - last_update >= 0.5:
                    self._check_control(model)
                    self._owned_update(
                        model,
                        {
                            "downloaded_bytes": downloaded,
                            "progress": self._progress(downloaded, total),
                            "download_heartbeat_at": utc_now(),
                        },
                    )
                    last_update = now_monotonic
        self._check_control(model)
        if total is not None and downloaded != total:
            raise OSError(f"Incomplete download: received {downloaded} of {total} bytes")
        self._owned_update(
            model,
            {
                "downloaded_bytes": downloaded,
                "progress": self._progress(downloaded, total),
                "download_heartbeat_at": utc_now(),
            },
        )
        return partial_path, downloaded, total

    def _download_with_retries(self, metadata: WhisperModelMetadata) -> tuple[Path, int]:
        retries = max(0, self.settings.whisper_download_max_retries)
        for retry_number in range(retries + 1):
            try:
                path, downloaded, _ = self._download_once(metadata)
                return path, downloaded
            except (DownloadCancelled, DownloaderStopping):
                raise
            except (OSError, urllib.error.URLError) as exc:
                if retry_number >= retries:
                    raise
                self._owned_update(
                    metadata.model,
                    {
                        "last_error": f"Transient download error; retrying: {exc}",
                        "download_heartbeat_at": utc_now(),
                    },
                )
                if self.stopping.wait(min(2 ** retry_number, 5)):
                    raise DownloaderStopping
                self._check_control(metadata.model)
        raise RuntimeError("Download retry loop exited unexpectedly")

    def _hash_file(self, model: str, path: Path) -> tuple[str, int]:
        import hashlib

        digest = hashlib.sha256()
        size = 0
        last_update = monotonic()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            os.close(descriptor)
            raise OSError("Model path is not a regular file")
        with os.fdopen(descriptor, "rb") as model_file:
            for chunk in iter(lambda: model_file.read(HASH_CHUNK_SIZE), b""):
                digest.update(chunk)
                size += len(chunk)
                if monotonic() - last_update >= self.settings.whisper_download_heartbeat_seconds:
                    self._check_control(model)
                    self._owned_update(model, {"download_heartbeat_at": utc_now()})
                    last_update = monotonic()
        self._check_control(model)
        return digest.hexdigest(), size

    def _mark_available(self, metadata: WhisperModelMetadata, checksum: str, size: int) -> None:
        now = utc_now()
        self._owned_update(
            metadata.model,
            {
                "status": "available",
                "actual_size_bytes": size,
                "expected_size_bytes": size,
                "checksum": checksum,
                "checksum_valid": True,
                "downloaded_bytes": size,
                "progress": 100,
                "downloaded_at": now,
                "download_completed_at": now,
                "download_heartbeat_at": now,
                "download_worker_id": None,
                "cancel_requested": False,
                "last_verified_at": now,
                "last_error": None,
                "download_restart_requested": False,
                "operation_started_at": None,
            },
        )

    def process_download(self, document: dict) -> None:
        model = document["model"]
        try:
            with self._model_file_lock(model):
                self._check_control(model)
                if document.get("download_restart_requested"):
                    self._remove_partial(model)
                    self._owned_update(
                        model,
                        {
                            "download_restart_requested": False,
                            "downloaded_bytes": 0,
                            "progress": 0,
                            "last_error": "Corrupted partial cleared; restarting from zero",
                            "download_heartbeat_at": utc_now(),
                        },
                    )
                self._process_download_owned(document)
        except DownloadCancelled:
            self._remove_partial(model)
            self._best_effort_owned_update(
                model,
                {
                    "status": "failed",
                    "downloaded_bytes": 0,
                    "progress": 0,
                    "download_completed_at": utc_now(),
                    "download_heartbeat_at": utc_now(),
                    "download_worker_id": None,
                    "cancel_requested": False,
                    "download_restart_requested": False,
                    "operation_started_at": None,
                    "last_error": "Download cancelled",
                },
            )
        except DownloaderStopping:
            self._best_effort_owned_update(
                model,
                {
                    "download_worker_id": None,
                    "download_heartbeat_at": utc_now(),
                    "last_error": "Download ownership changed; waiting to resume",
                },
            )
        except Exception as exc:
            LOGGER.exception("Download setup failed for %s", model)
            self._best_effort_owned_update(
                model,
                {
                    "status": "failed",
                    "progress": min(99, float(document.get("progress", 0))),
                    "download_completed_at": utc_now(),
                    "download_worker_id": None,
                    "cancel_requested": False,
                    "last_error": str(exc),
                    "operation_started_at": None,
                },
            )

    def _process_download_owned(self, document: dict) -> None:
        model = document["model"]
        metadata = WHISPER_MODEL_METADATA[model]
        canonical_path = whisper_model_directory() / metadata.file_name
        try:
            if canonical_path.is_file():
                checksum, size = self._hash_file(model, canonical_path)
                if checksum == metadata.expected_checksum:
                    self._remove_partial(model)
                    self._mark_available(metadata, checksum, size)
                    return

            partial_path, downloaded = self._download_with_retries(metadata)
            checksum, actual_size = self._hash_file(model, partial_path)
            if checksum != metadata.expected_checksum:
                now = utc_now()
                self._owned_update(
                    model,
                    {
                        "status": "corrupted",
                        "actual_size_bytes": actual_size,
                        "checksum": checksum,
                        "checksum_valid": False,
                        "downloaded_bytes": downloaded,
                        "progress": 99 if downloaded else 0,
                        "download_completed_at": now,
                        "download_heartbeat_at": now,
                        "download_worker_id": None,
                        "cancel_requested": False,
                        "last_verified_at": now,
                        "last_error": "Downloaded file SHA-256 checksum does not match",
                        "download_restart_requested": False,
                        "operation_started_at": None,
                    },
                )
                return

            if canonical_path.is_file():
                canonical_checksum, canonical_size = self._hash_file(model, canonical_path)
                if canonical_checksum == metadata.expected_checksum:
                    self._remove_partial(model)
                    self._mark_available(
                        metadata, canonical_checksum, canonical_size
                    )
                    return
            if canonical_path.is_symlink():
                raise OSError("Canonical model path cannot be a symbolic link")
            os.replace(partial_path, canonical_path)
            self._mark_available(metadata, checksum, actual_size)
        except DownloadCancelled:
            self._remove_partial(model)
            now = utc_now()
            self._best_effort_owned_update(
                model,
                {
                    "status": "failed",
                    "downloaded_bytes": 0,
                    "progress": 0,
                    "download_completed_at": now,
                    "download_heartbeat_at": now,
                    "download_worker_id": None,
                    "cancel_requested": False,
                    "last_error": "Download cancelled",
                    "download_restart_requested": False,
                    "operation_started_at": None,
                },
            )
        except DownloaderStopping:
            self._best_effort_owned_update(
                model,
                {
                    "download_worker_id": None,
                    "download_heartbeat_at": utc_now(),
                    "last_error": "Downloader stopped; waiting to resume",
                },
            )
        except Exception as exc:
            LOGGER.exception("Download failed for %s", model)
            now = utc_now()
            try:
                latest = self.collection.find_one({"model": model}, {"progress": 1})
                failed_progress = min(99, float(latest.get("progress", 0))) if latest else 0
            except Exception:
                failed_progress = 0
            self._best_effort_owned_update(
                model,
                {
                    "status": "failed",
                    "progress": failed_progress,
                    "download_completed_at": now,
                    "download_heartbeat_at": now,
                    "download_worker_id": None,
                    "cancel_requested": False,
                    "last_error": str(exc),
                    "operation_started_at": None,
                },
            )

    def run(self) -> None:
        ensure_whisper_model_registry()
        recovered = self.recover_interrupted_downloads()
        LOGGER.info("Downloader %s started; recovered %d download(s)", self.worker_id, recovered)
        last_stale_check = monotonic()
        while not self.stopping.is_set():
            if self.finalize_queued_cancellation():
                continue
            if monotonic() - last_stale_check >= self.settings.whisper_download_stale_seconds:
                self.recover_stale_downloads()
                last_stale_check = monotonic()
            document = self.claim_download()
            if document is not None:
                self.process_download(document)
                continue
            self.stopping.wait(self.settings.whisper_download_poll_seconds)
        LOGGER.info("Downloader %s stopped", self.worker_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    downloader = WhisperModelDownloader()
    signal.signal(signal.SIGINT, downloader.stop)
    signal.signal(signal.SIGTERM, downloader.stop)
    try:
        downloader.run()
    finally:
        close_database()


if __name__ == "__main__":
    main()

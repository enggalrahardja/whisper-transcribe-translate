import hashlib
import logging
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from pymongo import ReturnDocument

from .config import get_settings
from .database import close_database, get_database
from .services.whisper_model_metadata import MODEL_REGISTRY_METADATA, WhisperModelMetadata
from .services.whisper_models import (
    COLLECTION_NAME,
    HASH_CHUNK_SIZE,
    directory_size,
    ensure_whisper_model_registry,
    registry_identity,
    validate_ctranslate2_directory,
    whisper_model_path,
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
                "status": "downloading", "download_worker_id": {"$ne": None},
                "$or": [
                    {"download_heartbeat_at": {"$lt": cutoff}},
                    {"download_heartbeat_at": None},
                    {"download_heartbeat_at": {"$exists": False}},
                ],
            },
            {"$set": {"download_worker_id": None, "download_heartbeat_at": None,
                      "last_error": "Stale download recovered; waiting to resume", "updated_at": utc_now()}},
        )
        return result.modified_count

    def finalize_queued_cancellation(self) -> bool:
        document = self.collection.find_one(
            {"status": "downloading", "cancel_requested": True, "download_worker_id": None},
            sort=[("updated_at", 1)],
        )
        if document is None:
            return False
        backend, model = document.get("backend", "pytorch"), document["model"]
        self._remove_partial(backend, model)
        now = utc_now()
        self.collection.update_one(
            {**registry_identity(backend, model), "status": "downloading",
             "cancel_requested": True, "download_worker_id": None},
            {"$set": {"status": "failed", "downloaded_bytes": 0, "progress": 0,
                      "download_heartbeat_at": now, "download_completed_at": now,
                      "cancel_requested": False, "last_error": "Download cancelled",
                      "download_restart_requested": False, "operation_started_at": None,
                      "updated_at": now}},
        )
        return True

    def claim_download(self) -> dict | None:
        now = utc_now()
        return self.collection.find_one_and_update(
            {"status": "downloading", "download_worker_id": None,
             "cancel_requested": {"$ne": True}},
            {"$set": {"download_worker_id": self.worker_id, "download_started_at": now,
                      "download_heartbeat_at": now, "last_error": None, "updated_at": now},
             "$inc": {"attempt": 1}},
            sort=[("updated_at", 1)], return_document=ReturnDocument.AFTER,
        )

    def _owned_update(self, backend: str, model: str, values: dict) -> None:
        values["updated_at"] = utc_now()
        result = self.collection.update_one(
            {**registry_identity(backend, model), "status": "downloading",
             "download_worker_id": self.worker_id},
            {"$set": values},
        )
        if result.matched_count != 1:
            raise DownloaderStopping

    def _best_effort_owned_update(self, backend: str, model: str, values: dict) -> None:
        try:
            self._owned_update(backend, model, values)
        except Exception:
            LOGGER.exception("Could not persist final download state for %s:%s", backend, model)

    def _check_control(self, backend: str, model: str) -> None:
        if self.stopping.is_set():
            raise DownloaderStopping
        document = self.collection.find_one(
            registry_identity(backend, model), {"cancel_requested": 1, "download_worker_id": 1}
        )
        if document is None or document.get("download_worker_id") != self.worker_id:
            raise DownloaderStopping
        if document.get("cancel_requested"):
            raise DownloadCancelled

    def _remove_partial(self, backend: str, model: str) -> None:
        partial = whisper_partial_path(model, backend)
        try:
            if partial.is_dir() and not partial.is_symlink():
                shutil.rmtree(partial)
            else:
                partial.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _progress(downloaded: int, total: int | None) -> float:
        return min(99, downloaded * 100 / total) if total else 0

    @contextmanager
    def _model_file_lock(self, backend: str, model: str):
        partial = whisper_partial_path(model, backend)
        lock_path = partial.parent / f"{partial.name}.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            os.chmod(lock_path, 0o600)
            if os.name == "nt":
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _open_download(self, metadata: WhisperModelMetadata, offset: int):
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        response = urllib.request.urlopen(
            urllib.request.Request(metadata.source_url, headers=headers),
            timeout=self.settings.whisper_download_timeout_seconds,
        )
        if not offset:
            return response, 0
        match = CONTENT_RANGE_PATTERN.match(response.headers.get("Content-Range", ""))
        if response.status == 206 and match and int(match.group(1)) == offset:
            return response, offset
        response.close()
        return self._open_download(metadata, 0)

    def _download_pytorch_once(self, metadata: WhisperModelMetadata) -> tuple[Path, int]:
        backend, model = metadata.backend, metadata.model
        partial = whisper_partial_path(model, backend)
        offset = partial.stat().st_size if partial.is_file() else 0
        try:
            response, accepted_offset = self._open_download(metadata, offset)
        except urllib.error.HTTPError as exc:
            if exc.code != 416 or not offset:
                raise
            response, accepted_offset = self._open_download(metadata, 0)
        total_header = response.headers.get("Content-Length")
        total = accepted_offset + int(total_header) if total_header else None
        downloaded = accepted_offset
        self._owned_update(backend, model, {
            "expected_size_bytes": total, "downloaded_bytes": downloaded,
            "progress": self._progress(downloaded, total), "download_heartbeat_at": utc_now(),
        })
        if partial.is_symlink():
            response.close()
            raise OSError("Partial model path cannot be a symbolic link")
        flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if accepted_offset else os.O_TRUNC)
        descriptor = os.open(partial, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
        last_update = monotonic()
        with response, os.fdopen(descriptor, "ab" if accepted_offset else "wb") as output:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if monotonic() - last_update >= 0.5:
                    self._check_control(backend, model)
                    self._owned_update(backend, model, {
                        "downloaded_bytes": downloaded, "progress": self._progress(downloaded, total),
                        "download_heartbeat_at": utc_now(),
                    })
                    last_update = monotonic()
        self._check_control(backend, model)
        if total is not None and downloaded != total:
            raise OSError(f"Incomplete download: received {downloaded} of {total} bytes")
        return partial, downloaded

    def _download_pytorch(self, metadata: WhisperModelMetadata) -> tuple[Path, int]:
        retries = max(0, self.settings.whisper_download_max_retries)
        for retry_number in range(retries + 1):
            try:
                return self._download_pytorch_once(metadata)
            except (DownloadCancelled, DownloaderStopping):
                raise
            except (OSError, urllib.error.URLError) as exc:
                if retry_number >= retries:
                    raise
                self._owned_update(metadata.backend, metadata.model, {
                    "last_error": f"Transient download error; retrying: {exc}",
                    "download_heartbeat_at": utc_now(),
                })
                if self.stopping.wait(min(2 ** retry_number, 5)):
                    raise DownloaderStopping
        raise RuntimeError("Download retry loop exited unexpectedly")

    def _hash_file(self, backend: str, model: str, path: Path) -> tuple[str, int]:
        digest, size = hashlib.sha256(), 0
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            os.close(descriptor)
            raise OSError("Model path is not a regular file")
        last_update = monotonic()
        with os.fdopen(descriptor, "rb") as model_file:
            for chunk in iter(lambda: model_file.read(HASH_CHUNK_SIZE), b""):
                digest.update(chunk)
                size += len(chunk)
                if monotonic() - last_update >= self.settings.whisper_download_heartbeat_seconds:
                    self._check_control(backend, model)
                    self._owned_update(backend, model, {"download_heartbeat_at": utc_now()})
                    last_update = monotonic()
        self._check_control(backend, model)
        return digest.hexdigest(), size

    def _download_faster_whisper(self, metadata: WhisperModelMetadata) -> tuple[Path, int, str | None]:
        backend, model = metadata.backend, metadata.model
        partial = whisper_partial_path(model, backend)
        self._remove_partial(backend, model)
        partial.mkdir(parents=True, mode=0o700)
        process = subprocess.Popen([
            sys.executable, "-m", "app.services.faster_whisper_download",
            metadata.backend_model_id, str(partial),
        ])
        try:
            while process.poll() is None:
                if self.stopping.wait(0.5):
                    raise DownloaderStopping
                self._check_control(backend, model)
                size = directory_size(partial)
                self._owned_update(backend, model, {
                    "downloaded_bytes": size, "download_heartbeat_at": utc_now(),
                })
            if process.returncode:
                raise OSError(f"faster-whisper download exited with status {process.returncode}")
        except (DownloadCancelled, DownloaderStopping):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        valid, error = validate_ctranslate2_directory(partial)
        if not valid:
            raise OSError(error or "Downloaded directory is not a valid CTranslate2 model")
        size = directory_size(partial)
        model_bin = partial / "model.bin"
        checksum = self._hash_file(backend, model, model_bin)[0] if model_bin.is_file() else None
        return partial, size, checksum

    def _mark_available(
        self, metadata: WhisperModelMetadata, checksum: str | None, size: int
    ) -> None:
        now = utc_now()
        self._owned_update(metadata.backend, metadata.model, {
            "status": "available", "actual_size_bytes": size,
            "expected_size_bytes": size if metadata.backend == "pytorch" else None,
            "checksum": checksum,
            "checksum_valid": True if metadata.backend == "pytorch" else None,
            "validation_status": "valid", "downloaded_bytes": size, "progress": 100,
            "downloaded_at": now, "download_completed_at": now,
            "download_heartbeat_at": now, "download_worker_id": None,
            "cancel_requested": False, "last_verified_at": now, "last_error": None,
            "download_restart_requested": False, "cache_import_blocked": False,
            "operation_started_at": None,
        })

    def process_download(self, document: dict) -> None:
        backend, model = document.get("backend", "pytorch"), document["model"]
        try:
            with self._model_file_lock(backend, model):
                self._check_control(backend, model)
                if document.get("download_restart_requested"):
                    self._remove_partial(backend, model)
                    self._owned_update(backend, model, {
                        "download_restart_requested": False, "downloaded_bytes": 0,
                        "progress": 0, "last_error": None, "download_heartbeat_at": utc_now(),
                    })
                self._process_download_owned(document)
        except DownloadCancelled:
            self._remove_partial(backend, model)
            self._best_effort_owned_update(backend, model, {
                "status": "failed", "downloaded_bytes": 0, "progress": 0,
                "download_completed_at": utc_now(), "download_heartbeat_at": utc_now(),
                "download_worker_id": None, "cancel_requested": False,
                "download_restart_requested": False, "operation_started_at": None,
                "last_error": "Download cancelled",
            })
        except DownloaderStopping:
            self._best_effort_owned_update(backend, model, {
                "download_worker_id": None, "download_heartbeat_at": utc_now(),
                "last_error": "Download ownership changed; waiting to resume",
            })
        except Exception as exc:
            LOGGER.exception("Download failed for %s:%s", backend, model)
            self._best_effort_owned_update(backend, model, {
                "status": "failed", "progress": min(99, float(document.get("progress", 0))),
                "download_completed_at": utc_now(), "download_worker_id": None,
                "cancel_requested": False, "last_error": str(exc), "operation_started_at": None,
            })

    def _process_download_owned(self, document: dict) -> None:
        backend, model = document.get("backend", "pytorch"), document["model"]
        metadata = MODEL_REGISTRY_METADATA[(backend, model)]
        canonical = whisper_model_path(backend, model)
        if backend == "pytorch":
            if canonical.is_file():
                checksum, size = self._hash_file(backend, model, canonical)
                if checksum == metadata.expected_checksum:
                    self._remove_partial(backend, model)
                    self._mark_available(metadata, checksum, size)
                    return
            partial, downloaded = self._download_pytorch(metadata)
            checksum, size = self._hash_file(backend, model, partial)
            if checksum != metadata.expected_checksum:
                now = utc_now()
                self._owned_update(backend, model, {
                    "status": "corrupted", "actual_size_bytes": size, "checksum": checksum,
                    "checksum_valid": False, "validation_status": "invalid",
                    "downloaded_bytes": downloaded, "progress": 99 if downloaded else 0,
                    "download_completed_at": now, "download_heartbeat_at": now,
                    "download_worker_id": None, "cancel_requested": False,
                    "last_verified_at": now,
                    "last_error": "Downloaded file SHA-256 checksum does not match",
                    "download_restart_requested": False, "operation_started_at": None,
                })
                return
            if canonical.is_symlink():
                raise OSError("Canonical model path cannot be a symbolic link")
            os.replace(partial, canonical)
            self._mark_available(metadata, checksum, size)
            return

        if canonical.is_dir() and not canonical.is_symlink():
            valid, _ = validate_ctranslate2_directory(canonical)
            if valid:
                size = directory_size(canonical)
                model_bin = canonical / "model.bin"
                checksum = self._hash_file(backend, model, model_bin)[0] if model_bin.is_file() else None
                self._remove_partial(backend, model)
                self._mark_available(metadata, checksum, size)
                return
        partial, size, checksum = self._download_faster_whisper(metadata)
        if canonical.is_symlink():
            raise OSError("Canonical model path cannot be a symbolic link")
        if canonical.exists():
            shutil.rmtree(canonical)
        os.replace(partial, canonical)
        self._mark_available(metadata, checksum, size)

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

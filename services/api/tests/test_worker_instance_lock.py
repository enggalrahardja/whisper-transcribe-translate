import tempfile
import unittest
from pathlib import Path

from app.services.worker_instance_lock import WorkerInstanceAlreadyRunning, WorkerInstanceLock
from app.worker import TranscriptionWorker


class WorkerInstanceLockTests(unittest.TestCase):
    def test_duplicate_worker_process_is_rejected_until_owner_releases_lock(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "worker.lock"
            owner = WorkerInstanceLock(path)
            duplicate = WorkerInstanceLock(path)
            owner.acquire()
            try:
                with self.assertRaises(WorkerInstanceAlreadyRunning):
                    duplicate.acquire()
            finally:
                owner.release()

            duplicate.acquire()
            duplicate.release()

    def test_medium_oom_message_only_recommends_actually_smaller_models(self):
        message = TranscriptionWorker.cuda_oom_message("medium")

        self.assertIn("small, base, or tiny", message)
        self.assertNotIn("(medium", message)


if __name__ == "__main__":
    unittest.main()

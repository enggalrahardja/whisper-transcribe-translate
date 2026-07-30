import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bson import ObjectId

from app.services.dependency_compatibility import (
    PINNED_WORKER_DEPENDENCIES,
    WorkerDependencyMismatch,
    validate_worker_dependencies,
)
from app.worker import TranscriptionWorker


class WorkerDependencyCompatibilityTests(unittest.TestCase):
    def test_current_worker_environment_passes_preflight(self):
        installed = validate_worker_dependencies()
        self.assertEqual(installed["numba"], "0.58.1")
        self.assertEqual(installed["llvmlite"], "0.41.1")
        self.assertIsNone(installed["coverage"])
        if "triton" in PINNED_WORKER_DEPENDENCIES:
            self.assertEqual(installed["triton"], "3.7.1")

    def test_version_mismatch_fails_before_inference(self):
        actual = {
            package: expected for package, expected in PINNED_WORKER_DEPENDENCIES.items()
        }
        actual["llvmlite"] = "0.40.0"
        with patch(
            "app.services.dependency_compatibility.worker_dependency_versions",
            return_value=actual,
        ):
            with self.assertRaisesRegex(WorkerDependencyMismatch, "llvmlite expected 0.41.1"):
                validate_worker_dependencies()

    def test_unpinned_coverage_is_rejected(self):
        actual = {
            package: expected for package, expected in PINNED_WORKER_DEPENDENCIES.items()
        }
        actual["coverage"] = "7.10.0"
        with patch(
            "app.services.dependency_compatibility.worker_dependency_versions",
            return_value=actual,
        ):
            with self.assertRaisesRegex(WorkerDependencyMismatch, "coverage must be absent"):
                validate_worker_dependencies()

    def test_full_traceback_is_persisted_in_failure_history(self):
        worker = TranscriptionWorker.__new__(TranscriptionWorker)
        worker.worker_id = "test-worker"
        worker.jobs = MagicMock()
        worker.jobs.update_one.return_value = SimpleNamespace(modified_count=1)
        self.assertTrue(worker.fail_job(ObjectId(), "TypeError: broken", error_traceback="full traceback"))
        update = worker.jobs.update_one.call_args.args[1]
        self.assertEqual(update["$set"]["error_traceback"], "full traceback")
        self.assertEqual(update["$push"]["failure_history"]["traceback"], "full traceback")


if __name__ == "__main__":
    unittest.main()

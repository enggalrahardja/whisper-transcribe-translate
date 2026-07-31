import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import torch
from bson import ObjectId

from app.services.whisper_adapter import WhisperAdapter
from app.services.transcription_backends import BackendOutOfMemoryError
from app.worker import TranscriptionWorker


class FakeModel:
    def __init__(self, name: str, device: str) -> None:
        self.name = name
        self.device = torch.device(device)

    def transcribe(self, *_args, **_kwargs):
        return {"text": "ok", "segments": [], "language": "en"}


class WhisperAdapterLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        WhisperAdapter._cuda_owner = None
        self.temporary = TemporaryDirectory()
        self.paths = {}
        for name in ("base", "small"):
            path = Path(self.temporary.name) / f"{name}.pt"
            path.touch()
            self.paths[name] = path

    def tearDown(self) -> None:
        WhisperAdapter._cuda_owner = None
        self.temporary.cleanup()

    def patches(self, loader, *, cuda=True):
        return (
            patch("app.services.whisper_adapter.torch.cuda.is_available", return_value=cuda),
            patch("app.services.whisper_adapter.torch.cuda.mem_get_info", return_value=(4_000, 8_000)),
            patch("app.services.whisper_adapter.resolve_available_whisper_model_path", side_effect=self.paths.get),
            patch("app.services.whisper_adapter.whisper_model_usage", side_effect=lambda *_args: nullcontext()),
            patch("app.services.whisper_adapter.whisper.load_model", side_effect=loader),
        )

    def test_same_model_is_reused_without_reload(self):
        loads = []

        def loader(path, device, **_kwargs):
            loads.append(path)
            return FakeModel(Path(path).stem, device)

        patches = self.patches(loader)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            adapter = WhisperAdapter("cuda")
            first = adapter.load_model("base", fp16=True)
            second = adapter.load_model("base", fp16=True)

        self.assertIs(first, second)
        self.assertEqual(loads, [str(self.paths["base"])])
        self.assertEqual(adapter.active_model_name, "base")
        self.assertEqual(adapter.cached_model_count, 1)

    def test_different_model_evicts_previous_and_cache_stays_bounded(self):
        def loader(path, device, **_kwargs):
            return FakeModel(Path(path).stem, device)

        patches = self.patches(loader)
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patch("app.services.whisper_adapter.gc.collect") as collect,
            patch("app.services.whisper_adapter.torch.cuda.empty_cache") as empty_cache,
        ):
            adapter = WhisperAdapter("cuda")
            adapter.load_model("base")
            replacement = adapter.load_model("small")

        self.assertEqual(replacement.name, "small")
        self.assertEqual(adapter.active_model_name, "small")
        self.assertEqual(adapter.cached_model_count, 1)
        collect.assert_called()
        empty_cache.assert_called()

    def test_second_cuda_adapter_evicts_process_wide_owner(self):
        def loader(path, device, **_kwargs):
            return FakeModel(Path(path).stem, device)

        patches = self.patches(loader)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            first = WhisperAdapter("cuda")
            second = WhisperAdapter("cuda")
            first.load_model("base")
            second.load_model("small")

        self.assertIsNone(first.active_model_name)
        self.assertEqual(first.cached_model_count, 0)
        self.assertEqual(second.active_model_name, "small")
        self.assertEqual(second.cached_model_count, 1)

    def test_oom_releases_cache_and_adapter_remains_usable(self):
        outcomes = [torch.cuda.OutOfMemoryError("test OOM"), FakeModel("small", "cuda")]

        def loader(*_args, **_kwargs):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        patches = self.patches(loader)
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patch("app.services.whisper_adapter.gc.collect") as collect,
            patch("app.services.whisper_adapter.torch.cuda.empty_cache") as empty_cache,
        ):
            adapter = WhisperAdapter("cuda")
            with self.assertRaises(torch.cuda.OutOfMemoryError):
                adapter.load_model("base", fp16=True)
            self.assertIsNone(adapter.active_model_name)
            self.assertEqual(adapter.cached_model_count, 0)
            model = adapter.load_model("small", fp16=True)

        self.assertEqual(model.name, "small")
        self.assertEqual(adapter.active_model_name, "small")
        collect.assert_called()
        empty_cache.assert_called()
        self.assertEqual(
            adapter.last_load_metadata,
            {
                "requested_model": "small",
                "active_model": "small",
                "device": "cuda",
                "compute_type": "float16",
                "vram_free_bytes_before_load": 4_000,
                "vram_total_bytes_before_load": 8_000,
            },
        )

    def test_cpu_switch_does_not_call_cuda_cleanup(self):
        def loader(path, device, **_kwargs):
            return FakeModel(Path(path).stem, device)

        patches = self.patches(loader, cuda=False)
        with (
            patches[0], patches[2], patches[3], patches[4],
            patch("app.services.whisper_adapter.gc.collect") as collect,
            patch("app.services.whisper_adapter.torch.cuda.empty_cache") as empty_cache,
        ):
            adapter = WhisperAdapter("cpu")
            adapter.load_model("base", fp16=False)
            adapter.load_model("small", fp16=False)

        collect.assert_called()
        empty_cache.assert_not_called()
        self.assertEqual(adapter.last_load_metadata["compute_type"], "float32")
        self.assertIsNone(adapter.last_load_metadata["vram_free_bytes_before_load"])

    def test_concurrent_model_switch_is_serialized(self):
        active_loads = 0
        maximum_loads = 0
        counter_lock = threading.Lock()

        def loader(path, device, **_kwargs):
            nonlocal active_loads, maximum_loads
            with counter_lock:
                active_loads += 1
                maximum_loads = max(maximum_loads, active_loads)
            try:
                time.sleep(0.02)
                return FakeModel(Path(path).stem, device)
            finally:
                with counter_lock:
                    active_loads -= 1

        patches = self.patches(loader)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            adapter = WhisperAdapter("cuda")
            barrier = threading.Barrier(3)
            errors = []

            def switch(model_name):
                try:
                    barrier.wait()
                    adapter.load_model(model_name)
                except Exception as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            threads = [
                threading.Thread(target=switch, args=("base",)),
                threading.Thread(target=switch, args=("small",)),
            ]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(maximum_loads, 1)
        self.assertEqual(adapter.cached_model_count, 1)
        self.assertIn(adapter.active_model_name, {"base", "small"})


class WorkerCudaOomTests(unittest.TestCase):
    def test_gpu_concurrency_default_is_one(self):
        from app.models.settings import TranscriptionSettings

        self.assertEqual(TranscriptionSettings().maximum_concurrent_transcription_jobs, 1)

    def test_oom_marks_job_failed_without_escaping_worker(self):
        class OomAdapter:
            last_load_metadata = {
                "requested_model": "large",
                "active_model": None,
                "device": "cuda",
                "compute_type": "float16",
                "vram_free_bytes_before_load": 1024,
                "vram_total_bytes_before_load": 8192,
            }

            def __init__(self):
                self.released = False

            def load_model(self, *_args, **_kwargs):
                self.released = True
                raise BackendOutOfMemoryError("oom_load", "test OOM", stage="load")

            def release_cache(self):
                self.released = True

        worker = TranscriptionWorker.__new__(TranscriptionWorker)
        worker.adapter = OomAdapter()
        worker.job_cancel_requested = threading.Event()
        worker.current_job_progress = 0
        worker.current_job_id = None
        worker.worker_id = "test-worker"
        worker.transcripts = MagicMock()
        worker.transcripts.find_one.return_value = None
        worker.media_files = MagicMock()
        worker.media_files.find_one.return_value = {"stored_path": "audio.wav"}
        worker.start_heartbeat = MagicMock()
        worker.stop_heartbeat = MagicMock()
        worker.report_runtime = MagicMock()
        worker.update_progress = MagicMock(return_value=True)
        worker.should_cancel_current_job = MagicMock(return_value=False)
        worker.cancel_current_job = MagicMock(return_value=False)
        worker.fail_job = MagicMock(return_value=True)

        job = {
            "_id": ObjectId(),
            "media_file_id": ObjectId(),
            "model": "large",
            "language": "auto",
            "task": "transcribe",
        }
        settings = SimpleNamespace(
            transcription=SimpleNamespace(
                backend="pytorch",
                device="cuda",
                compute_type="float16",
                fp16=True,
                beam_size=5,
                temperature=0.0,
                initial_prompt="",
                word_timestamps=False,
            )
        )
        with (
            patch("app.worker.resolve_storage_file", return_value=Path("audio.wav")),
            patch("app.worker.get_application_settings", return_value=settings),
        ):
            worker.process_job(job)

        self.assertTrue(worker.adapter.released)
        worker.fail_job.assert_called_once()
        call = worker.fail_job.call_args
        self.assertIn("smaller Whisper model", call.args[1])
        self.assertEqual(call.kwargs["model_load_metadata"]["requested_model"], "large")
        self.assertIsNone(worker.current_job_id)


if __name__ == "__main__":
    unittest.main()

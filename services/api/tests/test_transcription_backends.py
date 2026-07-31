import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.transcription_backends import (
    BackendOutOfMemoryError,
    BackendConfig,
    FasterWhisperBackend,
    PytorchWhisperBackend,
    TranscriptionBackendError,
    TranscriptionBackendManager,
    TranscriptionOptions,
    canonical_model_name,
    resolve_backend_config,
    runtime_capabilities,
)
from app.services.cuda12_runtime import (
    Cuda12RuntimeFailure,
    merge_library_path,
    resolve_cuda12_library_paths,
)


def capabilities(*, cuda=True):
    return {
        "backends": [
            {"id": "pytorch", "label": "Whisper PyTorch", "available": True, "reason": None},
            {"id": "faster-whisper", "label": "faster-whisper", "available": True, "reason": None},
        ],
        "devices": [
            {"id": "cpu", "label": "CPU", "available": True},
            {"id": "cuda", "label": "CUDA", "available": cuda},
        ],
        "compute_types": {
            "pytorch": {"cpu": ["float32"], "cuda": ["float16", "float32"] if cuda else []},
            "faster-whisper": {"cpu": ["int8", "float32"], "cuda": ["float16", "int8_float16", "int8"] if cuda else []},
        },
        "models": ["tiny", "base", "small", "medium", "large-v3"],
        "recommended": {"backend": "faster-whisper", "model": "large-v3", "device": "cuda", "compute_type": "int8_float16"},
        "recommended_by_backend": {
            "pytorch": {"backend": "pytorch", "model": "medium", "device": "cuda", "compute_type": "float16"},
            "faster-whisper": {"backend": "faster-whisper", "model": "large-v3", "device": "cuda", "compute_type": "int8_float16"},
        },
    }


class BackendValidationTests(unittest.TestCase):
    def test_gpu_runtime_recommends_safe_backend_specific_presets(self):
        ctranslate2 = SimpleNamespace(
            get_cuda_device_count=lambda: 1,
            get_supported_compute_types=lambda device: (
                {"float16", "int8_float16", "int8"} if device == "cuda" else {"int8", "float32"}
            ),
        )
        with (
            patch("app.services.transcription_backends._faster_whisper_available", return_value=(True, None)),
            patch("app.services.transcription_backends.torch.cuda.is_available", return_value=True),
            patch.dict(sys.modules, {"ctranslate2": ctranslate2}),
        ):
            presets = runtime_capabilities()["recommended_by_backend"]
        self.assertEqual(presets["pytorch"], {
            "backend": "pytorch", "model": "medium", "device": "cuda", "compute_type": "float16",
        })
        self.assertEqual(presets["faster-whisper"], {
            "backend": "faster-whisper", "model": "large-v3", "device": "cuda", "compute_type": "int8_float16",
        })

    def test_large_alias_maps_to_large_v3(self):
        self.assertEqual(canonical_model_name("large"), "large-v3")

    def test_documented_valid_combinations(self):
        with patch("app.services.transcription_backends.runtime_capabilities", return_value=capabilities()):
            self.assertEqual(
                resolve_backend_config("faster-whisper", "large-v3", "cuda", "int8_float16").compute_type,
                "int8_float16",
            )
            self.assertEqual(resolve_backend_config("faster-whisper", "base", "cpu", "int8").device, "cpu")
            self.assertEqual(resolve_backend_config("pytorch", "base", "cuda", "float16").backend, "pytorch")

    def test_invalid_compute_type_names_full_configuration(self):
        with patch("app.services.transcription_backends.runtime_capabilities", return_value=capabilities()):
            with self.assertRaisesRegex(
                TranscriptionBackendError,
                "Invalid compute type int8_float16 for backend pytorch on cuda.*float16, float32",
            ):
                resolve_backend_config("pytorch", "base", "cuda", "int8_float16")

    def test_cuda_unavailable_is_clear(self):
        with patch("app.services.transcription_backends.runtime_capabilities", return_value=capabilities(cuda=False)):
            with self.assertRaisesRegex(TranscriptionBackendError, "CUDA is not available for backend faster-whisper"):
                resolve_backend_config("faster-whisper", "base", "cuda", "int8")


class FasterWhisperContractTests(unittest.TestCase):
    def test_large_v3_cuda_int8_float16_loader_contract(self):
        loaded_model = object()
        loader = MagicMock(return_value=loaded_model)
        fake_module = SimpleNamespace(WhisperModel=loader)
        with (
            patch.dict(sys.modules, {"faster_whisper": fake_module}),
            patch("app.services.transcription_backends.activate_faster_whisper_cuda") as activate_cuda,
            patch("app.services.transcription_backends.resolve_available_whisper_model_path", return_value=Path("/models/large-v3")),
        ):
            backend = FasterWhisperBackend()
            result = backend.load_model(
                BackendConfig("faster-whisper", "large-v3", "cuda", "int8_float16")
            )

        self.assertIs(result, loaded_model)
        activate_cuda.assert_called_once_with("int8_float16")
        loader.assert_called_once_with("/models/large-v3", device="cuda", compute_type="int8_float16")

    def test_cpu_loader_does_not_activate_cuda12_runtime(self):
        loader = MagicMock(return_value=object())
        with (
            patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=loader)}),
            patch("app.services.transcription_backends.activate_faster_whisper_cuda") as activate_cuda,
            patch("app.services.transcription_backends.resolve_available_whisper_model_path", return_value=Path("/models/base")),
        ):
            FasterWhisperBackend().load_model(
                BackendConfig("faster-whisper", "base", "cpu", "int8")
            )

        activate_cuda.assert_not_called()

    def test_missing_cuda12_library_has_structured_context(self):
        failure = Cuda12RuntimeFailure(
            "missing_cublas_cuda12",
            "libcublas.so.12 is missing",
            "libcublas.so.12",
            "Install nvidia-cublas-cu12 and restart the worker",
        )
        with patch(
            "app.services.transcription_backends.activate_faster_whisper_cuda",
            side_effect=failure,
        ):
            with self.assertRaises(TranscriptionBackendError) as raised:
                FasterWhisperBackend().load_model(
                    BackendConfig("faster-whisper", "large-v3", "cuda", "int8_float16")
                )

        error = raised.exception
        self.assertEqual(error.code, "dependency_incompatible")
        self.assertEqual(error.detail, "missing_cublas_cuda12")
        self.assertEqual(error.stage, "load")
        self.assertEqual(error.backend, "faster-whisper")
        self.assertEqual(error.device, "cuda")
        self.assertEqual(error.compute_type, "int8_float16")
        self.assertEqual(error.missing_library, "libcublas.so.12")
        self.assertIn("restart", error.remediation.lower())

    def test_generator_is_consumed_and_normalized(self):
        segment = SimpleNamespace(
            id=7,
            seek=0,
            start=1.25,
            end=2.5,
            text=" hello",
            tokens=[1, 2],
            temperature=0.0,
            avg_logprob=-0.25,
            compression_ratio=1.1,
            no_speech_prob=0.05,
            words=[SimpleNamespace(word=" hello", start=1.25, end=2.5, probability=0.9)],
        )
        consumed = []

        def generated():
            consumed.append(True)
            yield segment

        fake_model = MagicMock()
        fake_model.transcribe.return_value = (
            generated(),
            SimpleNamespace(language="en", language_probability=0.98, duration=3.0),
        )
        backend = FasterWhisperBackend()
        backend.model = fake_model
        backend.config = BackendConfig("faster-whisper", "base", "cpu", "int8")
        progress = MagicMock()

        result = backend.transcribe(Path("audio.wav"), TranscriptionOptions(language="auto", progress_callback=progress))

        self.assertEqual(consumed, [True])
        self.assertEqual(result["text"], "hello")
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["duration"], 3.0)
        self.assertEqual(result["segments"][0]["start"], 1.25)
        self.assertEqual(result["segments"][0]["end"], 2.5)
        self.assertEqual(result["segments"][0]["avg_logprob"], -0.25)
        self.assertEqual(result["segments"][0]["no_speech_prob"], 0.05)
        self.assertEqual(result["segments"][0]["words"][0]["probability"], 0.9)
        self.assertEqual([call.args[0] for call in progress.call_args_list], [83, 100])

    def test_lazy_cuda_library_error_is_structured_as_inference_dependency_failure(self):
        def generated():
            raise RuntimeError("Library libcublas.so.12 is not found or cannot be loaded")
            yield

        fake_model = MagicMock()
        fake_model.transcribe.return_value = (
            generated(),
            SimpleNamespace(language="en", language_probability=1.0, duration=1.0),
        )
        backend = FasterWhisperBackend()
        backend.model = fake_model
        backend.config = BackendConfig("faster-whisper", "large-v3", "cuda", "int8_float16")

        with self.assertRaises(TranscriptionBackendError) as raised:
            backend.transcribe(Path("audio.wav"), TranscriptionOptions(language="en"))

        self.assertEqual(raised.exception.code, "dependency_incompatible")
        self.assertEqual(raised.exception.stage, "inference")
        self.assertEqual(raised.exception.detail, "missing_cublas_cuda12")
        self.assertEqual(raised.exception.backend, "faster-whisper")
        self.assertEqual(raised.exception.device, "cuda")
        self.assertEqual(raised.exception.compute_type, "int8_float16")
        self.assertEqual(raised.exception.missing_library, "libcublas.so.12")


class PytorchIsolationTests(unittest.TestCase):
    def test_pytorch_loader_does_not_activate_cuda12_runtime(self):
        adapter = MagicMock()
        adapter.effective_device = "cuda"
        adapter.load_model.return_value = object()
        with (
            patch("app.services.transcription_backends.WhisperAdapter", return_value=adapter),
            patch("app.services.transcription_backends.activate_faster_whisper_cuda") as activate_cuda,
        ):
            PytorchWhisperBackend().load_model(
                BackendConfig("pytorch", "base", "cuda", "float16")
            )

        activate_cuda.assert_not_called()


class BackendManagerCacheTests(unittest.TestCase):
    def setUp(self):
        TranscriptionBackendManager._owner = None

    def tearDown(self):
        TranscriptionBackendManager._owner = None

    def test_same_identity_reuses_and_changed_identity_unloads(self):
        instances = []

        class FakeBackend:
            def __init__(self):
                self.loads = []
                self.unloads = 0
                self.model = object()
                instances.append(self)

            def load_model(self, config):
                self.loads.append(config)
                return self.model

            def unload_model(self):
                self.unloads += 1

            def get_runtime_metadata(self):
                return {"backend_runtime_marker": "available"}

        with (
            patch("app.services.transcription_backends.resolve_backend_config", side_effect=lambda b, m, d, c: BackendConfig(b, canonical_model_name(m), d, c)),
            patch("app.services.transcription_backends.PytorchWhisperBackend", FakeBackend),
            patch("app.services.transcription_backends.FasterWhisperBackend", FakeBackend),
            patch("app.services.transcription_backends.torch.cuda.is_available", return_value=False),
            patch("app.services.transcription_backends.gc.collect"),
        ):
            manager = TranscriptionBackendManager()
            first = manager.load_model("pytorch", "base", "cpu", "float32")
            second = manager.load_model("pytorch", "base", "cpu", "float32")
            manager.load_model("faster-whisper", "base", "cpu", "int8")

        self.assertIs(first, second)
        self.assertEqual(len(instances), 2)
        self.assertEqual(instances[0].unloads, 1)
        self.assertEqual(manager.cached_model_count, 1)
        self.assertEqual(manager.last_load_metadata["cache_identity"], "faster-whisper:base:cpu:int8")
        self.assertEqual(manager.last_load_metadata["backend_runtime_marker"], "available")

    def test_model_and_compute_changes_each_replace_the_active_cache(self):
        instances = []

        class FakeBackend:
            def __init__(self):
                self.model = object()
                self.unloads = 0
                instances.append(self)

            def load_model(self, _config):
                return self.model

            def unload_model(self):
                self.unloads += 1

            def get_runtime_metadata(self):
                return {}

        with (
            patch("app.services.transcription_backends.resolve_backend_config", side_effect=lambda b, m, d, c: BackendConfig(b, canonical_model_name(m), d, c)),
            patch("app.services.transcription_backends.PytorchWhisperBackend", FakeBackend),
            patch("app.services.transcription_backends.torch.cuda.is_available", return_value=False),
            patch("app.services.transcription_backends.gc.collect"),
        ):
            manager = TranscriptionBackendManager()
            manager.load_model("pytorch", "base", "cpu", "float32")
            manager.load_model("pytorch", "small", "cpu", "float32")
            manager.load_model("pytorch", "small", "cpu", "float16")

        self.assertEqual(len(instances), 3)
        self.assertEqual([item.unloads for item in instances], [1, 1, 0])
        self.assertEqual(manager.cached_model_count, 1)

    def test_inference_oom_clears_cache_without_backend_fallback(self):
        instances = []

        class OomBackend:
            def __init__(self):
                self.model = object()
                self.unloads = 0
                instances.append(self)

            def load_model(self, _config):
                return self.model

            def transcribe(self, _path, _options):
                raise BackendOutOfMemoryError("oom_inference", "out of memory", stage="inference")

            def unload_model(self):
                self.unloads += 1

            def get_runtime_metadata(self):
                return {}

        with (
            patch("app.services.transcription_backends.resolve_backend_config", return_value=BackendConfig("faster-whisper", "large-v3", "cuda", "int8_float16")),
            patch("app.services.transcription_backends.FasterWhisperBackend", OomBackend),
            patch("app.services.transcription_backends.PytorchWhisperBackend") as pytorch_backend,
            patch("app.services.transcription_backends.torch.cuda.is_available", return_value=False),
            patch("app.services.transcription_backends.gc.collect"),
        ):
            manager = TranscriptionBackendManager()
            with self.assertRaises(BackendOutOfMemoryError):
                manager.transcribe(
                    Path("audio.wav"), backend="faster-whisper", model_name="large-v3",
                    device="cuda", compute_type="int8_float16", language="auto",
                )

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].unloads, 1)
        self.assertEqual(manager.cached_model_count, 0)
        self.assertEqual(manager.last_load_metadata["model_status"], "failed")
        pytorch_backend.assert_not_called()


class Cuda12LibraryPathTests(unittest.TestCase):
    def test_namespace_packages_are_resolved_from_module_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cublas = root / "cublas"
            cudnn = root / "cudnn"
            cublas.mkdir()
            cudnn.mkdir()
            (cublas / "libcublas.so.12").touch()
            (cudnn / "libcudnn.so.9").touch()
            modules = [
                SimpleNamespace(__file__=None, __path__=[str(cublas)]),
                SimpleNamespace(__file__=None, __path__=[str(cudnn)]),
            ]
            with patch("app.services.cuda12_runtime.importlib.import_module", side_effect=modules):
                self.assertEqual(
                    resolve_cuda12_library_paths(),
                    (str(cublas.resolve()), str(cudnn.resolve())),
                )

    def test_existing_library_path_is_preserved(self):
        self.assertEqual(
            merge_library_path("/existing/one:/existing/two", ("/cuda/cublas", "/cuda/cudnn")),
            "/cuda/cublas:/cuda/cudnn:/existing/one:/existing/two",
        )

    def test_duplicate_paths_are_removed_without_reordering(self):
        self.assertEqual(
            merge_library_path("/cuda/cudnn:/existing:/cuda/cublas", ("/cuda/cublas", "/cuda/cudnn")),
            "/cuda/cublas:/cuda/cudnn:/existing",
        )


if __name__ == "__main__":
    unittest.main()

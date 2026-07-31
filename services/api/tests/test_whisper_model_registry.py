import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.whisper_model_metadata import (
    MODEL_PRESET_BADGES,
    MODEL_REGISTRY_METADATA,
    SUPPORTED_WHISPER_MODELS,
)
from app.services.whisper_models import (
    _verification_values,
    canonical_registry_model,
    registry_identity,
    whisper_model_path,
)


class WhisperModelRegistryTests(unittest.TestCase):
    def test_each_backend_has_the_complete_independent_catalogue(self):
        self.assertEqual(
            SUPPORTED_WHISPER_MODELS,
            ("tiny", "base", "small", "medium", "large-v3", "turbo"),
        )
        for backend in ("pytorch", "faster-whisper"):
            self.assertEqual(
                [model for candidate_backend, model in MODEL_REGISTRY_METADATA if candidate_backend == backend],
                list(SUPPORTED_WHISPER_MODELS),
            )

    def test_preset_badges_and_turbo_mapping_are_explicit(self):
        self.assertEqual(MODEL_PRESET_BADGES["small"], "Balanced")
        self.assertEqual(MODEL_PRESET_BADGES["turbo"], "Fastest")
        self.assertEqual(MODEL_PRESET_BADGES["large-v3"], "Best accuracy")
        for backend in ("pytorch", "faster-whisper"):
            turbo = MODEL_REGISTRY_METADATA[(backend, "turbo")]
            self.assertEqual(turbo.model, "turbo")
            self.assertEqual(turbo.backend_model_id, "turbo")

    def test_identity_always_contains_backend_and_keeps_legacy_large_alias(self):
        self.assertEqual(registry_identity("pytorch", "base"), {"backend": "pytorch", "model": "base"})
        self.assertEqual(
            registry_identity("faster-whisper", "large"),
            {"backend": "faster-whisper", "model": "large-v3"},
        )
        self.assertEqual(canonical_registry_model("large"), "large-v3")

    def test_same_model_uses_separate_file_and_directory_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = SimpleNamespace(
                whisper_model_dir=root / "pytorch",
                faster_whisper_model_dir=root / "faster-whisper",
            )
            with (
                patch("app.services.whisper_models.get_settings", return_value=settings),
                patch("app.services.whisper_models.materialize_cached_faster_whisper_model", return_value=False),
            ):
                pytorch_path = whisper_model_path("pytorch", "base")
                faster_path = whisper_model_path("faster-whisper", "base")
            self.assertEqual(pytorch_path, root / "pytorch" / "base.pt")
            self.assertEqual(faster_path, root / "faster-whisper" / "base")
            self.assertNotEqual(pytorch_path, faster_path)

    def test_pytorch_checkpoint_does_not_validate_faster_whisper_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = SimpleNamespace(
                whisper_model_dir=root / "pytorch",
                faster_whisper_model_dir=root / "faster-whisper",
            )
            pytorch = MODEL_REGISTRY_METADATA[("pytorch", "base")]
            faster = MODEL_REGISTRY_METADATA[("faster-whisper", "base")]
            (root / "pytorch").mkdir()
            checkpoint = root / "pytorch" / "base.pt"
            checkpoint.write_bytes(b"valid checkpoint fixture")
            pytorch = replace(
                pytorch,
                expected_checksum=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            )
            with (
                patch("app.services.whisper_models.get_settings", return_value=settings),
                patch("app.services.whisper_models.materialize_cached_faster_whisper_model", return_value=False),
            ):
                pytorch_values = _verification_values(pytorch, missing_is_error=False)
                faster_values = _verification_values(faster, missing_is_error=False)
            self.assertEqual(pytorch_values["status"], "available")
            self.assertEqual(faster_values["status"], "not_downloaded")

    def test_faster_whisper_directory_does_not_validate_pytorch_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = SimpleNamespace(
                whisper_model_dir=root / "pytorch",
                faster_whisper_model_dir=root / "faster-whisper",
            )
            model_directory = root / "faster-whisper" / "base"
            model_directory.mkdir(parents=True)
            (model_directory / "model.bin").write_bytes(b"ctranslate fixture")
            with (
                patch("app.services.whisper_models.get_settings", return_value=settings),
                patch("app.services.whisper_models.validate_ctranslate2_directory", return_value=(True, None)),
            ):
                faster_values = _verification_values(
                    MODEL_REGISTRY_METADATA[("faster-whisper", "base")], missing_is_error=False
                )
                pytorch_values = _verification_values(
                    MODEL_REGISTRY_METADATA[("pytorch", "base")], missing_is_error=False
                )
            self.assertEqual(faster_values["status"], "available")
            self.assertEqual(pytorch_values["status"], "not_downloaded")


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from pydantic import ValidationError

from app.models.job import CreateJobRequest
from app.services.job_transcription import inference_options
from app.services.transcription_backends import (
    BackendConfig,
    FasterWhisperBackend,
    PytorchWhisperBackend,
    TranscriptionOptions,
)
from app.services.transcription_languages import (
    InvalidTranscriptionLanguage,
    normalize_transcription_language,
    transcription_language_label,
)


def application_settings():
    return SimpleNamespace(transcription=SimpleNamespace(
        beam_size=5,
        temperature=0.0,
        word_timestamps=False,
        initial_prompt="",
    ))


class TranscriptionLanguageNormalizationTests(unittest.TestCase):
    def test_indonesian_label_and_legacy_name_map_to_id(self):
        self.assertEqual(normalize_transcription_language("Indonesian"), "id")
        self.assertEqual(normalize_transcription_language("indonesian"), "id")
        self.assertEqual(transcription_language_label("id"), "Indonesian")

    def test_iso_code_is_stable_and_auto_is_none(self):
        self.assertEqual(normalize_transcription_language("id"), "id")
        self.assertIsNone(normalize_transcription_language("auto"))
        self.assertIsNone(normalize_transcription_language(None))

    def test_unknown_language_is_rejected_during_request_validation(self):
        with self.assertRaises(ValidationError) as raised:
            CreateJobRequest(file_name="audio.wav", language="not-a-language")
        self.assertEqual(raised.exception.errors()[0]["type"], "language_unsupported")

    def test_legacy_job_full_language_name_is_compatible(self):
        options = inference_options({"language": "indonesian"}, application_settings())
        self.assertEqual(options.language, "id")

    def test_explicit_language_code_field_wins_over_legacy_display_value(self):
        options = inference_options(
            {"language": "Indonesian", "language_code": "id"}, application_settings()
        )
        self.assertEqual(options.language, "id")


class BackendLanguageContractTests(unittest.TestCase):
    def test_pytorch_and_faster_whisper_receive_the_same_iso_code(self):
        pytorch_adapter = MagicMock()
        pytorch = PytorchWhisperBackend()
        pytorch.adapter = pytorch_adapter
        pytorch.config = BackendConfig("pytorch", "base", "cpu", "float32")
        pytorch.transcribe(Path("audio.wav"), TranscriptionOptions(language="Indonesian"))
        self.assertEqual(pytorch_adapter.transcribe.call_args.kwargs["language"], "id")

        faster_model = MagicMock()
        faster_model.transcribe.return_value = (
            iter(()),
            SimpleNamespace(language="id", language_probability=1.0, duration=0.0),
        )
        faster = FasterWhisperBackend()
        faster.model = faster_model
        faster.config = BackendConfig("faster-whisper", "base", "cpu", "int8")
        faster.transcribe(Path("audio.wav"), TranscriptionOptions(language="Indonesian"))
        self.assertEqual(faster_model.transcribe.call_args.kwargs["language"], "id")

    def test_auto_detect_is_none_for_both_backends(self):
        pytorch_adapter = MagicMock()
        pytorch = PytorchWhisperBackend()
        pytorch.adapter = pytorch_adapter
        pytorch.config = BackendConfig("pytorch", "base", "cpu", "float32")
        pytorch.transcribe(Path("audio.wav"), TranscriptionOptions(language="auto"))
        self.assertIsNone(pytorch_adapter.transcribe.call_args.kwargs["language"])

        faster_model = MagicMock()
        faster_model.transcribe.return_value = (
            iter(()),
            SimpleNamespace(language="id", language_probability=1.0, duration=0.0),
        )
        faster = FasterWhisperBackend()
        faster.model = faster_model
        faster.config = BackendConfig("faster-whisper", "base", "cpu", "int8")
        faster.transcribe(Path("audio.wav"), TranscriptionOptions(language="auto"))
        self.assertIsNone(faster_model.transcribe.call_args.kwargs["language"])

    def test_unknown_value_is_rejected_before_backend_inference(self):
        faster_model = MagicMock()
        faster = FasterWhisperBackend()
        faster.model = faster_model
        faster.config = BackendConfig("faster-whisper", "base", "cpu", "int8")
        with self.assertRaises(InvalidTranscriptionLanguage):
            faster.transcribe(
                Path("audio.wav"), TranscriptionOptions(language="not-a-language")
            )
        faster_model.transcribe.assert_not_called()


if __name__ == "__main__":
    unittest.main()

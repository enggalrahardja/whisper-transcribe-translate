import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.models.job import AdvancedTranscriptionSettings
from app.services.job_transcription import apply_job_output_config, inference_options


def application_settings():
    return SimpleNamespace(transcription=SimpleNamespace(
        beam_size=5,
        temperature=0.2,
        word_timestamps=False,
        initial_prompt="existing prompt",
    ))


class JobTranscriptionSettingsTests(unittest.TestCase):
    def test_legacy_job_uses_existing_application_configuration(self):
        options = inference_options({"language": "id"}, application_settings())
        self.assertEqual(options.language, "id")
        self.assertEqual(options.beam_size, 5)
        self.assertEqual(options.temperature, 0.2)
        self.assertTrue(options.condition_on_previous_text)
        self.assertEqual(options.no_speech_threshold, 0.6)

    def test_job_overrides_are_isolated_to_that_job(self):
        job = {
            "language": "id",
            "transcription_config": {
                "processing_mode": "interview",
                "force_language": False,
                "use_vad": False,
                "use_previous_segment_context": False,
                "accurate_final": True,
                "accurate": {"beam_size": 9, "best_of": 7, "temperature": 0.4, "word_timestamps": True},
            },
        }
        options = inference_options(job, application_settings())
        self.assertEqual(options.language, "auto")
        self.assertIsNone(options.no_speech_threshold)
        self.assertFalse(options.condition_on_previous_text)
        self.assertEqual((options.beam_size, options.best_of, options.temperature), (9, 7, 0.4))
        self.assertTrue(options.word_timestamps)

        standard = inference_options({"language": "en", "transcription_config": {}}, application_settings())
        self.assertEqual((standard.beam_size, standard.temperature), (5, 0.2))

    def test_glossary_is_not_loaded_without_explicit_selection(self):
        with patch("app.services.job_transcription.load_job_glossary") as load:
            inference_options(
                {"language": "id", "transcription_config": {"apply_glossary": False}},
                application_settings(),
            )
            output = apply_job_output_config(
                {"text": "Galva", "language": "id", "segments": [{"text": " Galva", "avg_logprob": -2}]},
                {"transcription_config": {}},
            )
        load.assert_not_called()
        self.assertEqual(output["text"], "Galva")

    def test_clean_and_low_confidence_changes_require_explicit_values(self):
        result = {
            "text": " um sebuah kalimat",
            "language": "id",
            "segments": [{"start": 0, "end": 1, "text": " um sebuah kalimat", "avg_logprob": -2}],
        }
        clean = apply_job_output_config(result, {"transcription_config": {
            "processing_mode": "clean",
            "transcript_style": "clean",
            "low_confidence_handling": "replace",
        }})
        self.assertEqual(clean["text"], "[tidak jelas]")

    def test_glossary_requires_an_id(self):
        with self.assertRaises(ValueError):
            AdvancedTranscriptionSettings(apply_glossary=True)


if __name__ == "__main__":
    unittest.main()

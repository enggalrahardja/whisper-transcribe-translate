import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).resolve().parents[1] / "run.py"
SPEC = importlib.util.spec_from_file_location("benchmark_run", MODULE_PATH)
benchmark_run = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(benchmark_run)


class ErrorRateTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(benchmark_run.error_rate("Halo dunia", "halo dunia!", unit="word"), 0.0)
        self.assertEqual(benchmark_run.error_rate("Halo dunia", "halo dunia!", unit="character"), 0.0)

    def test_word_substitution(self):
        self.assertEqual(benchmark_run.error_rate("satu dua tiga", "satu empat tiga", unit="word"), 1 / 3)

    def test_character_deletion(self):
        self.assertEqual(benchmark_run.error_rate("abc", "ac", unit="character"), 1 / 3)

    def test_translation_chrf(self):
        self.assertEqual(benchmark_run.translation_chrf("Halo dunia", "halo dunia"), 100.0)
        self.assertLess(benchmark_run.translation_chrf("halo dunia", "good morning"), 50.0)


class ManifestTests(unittest.TestCase):
    def test_repository_manifest_is_valid_with_disabled_placeholders(self):
        manifest = Path(__file__).resolve().parents[1] / "dataset" / "manifest.json"
        self.assertEqual(benchmark_run.validate_manifest(manifest), [])

    def test_enabled_case_requires_files_digest_and_consent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            case = {
                "id": "unsafe_case", "enabled": True, "audio_path": "missing.wav",
                "reference_transcript_path": "missing.txt", "reference_translation_path": None,
                "profiles": sorted(benchmark_run.REQUIRED_PROFILES),
                "provenance": {"contains_sensitive_data": False, "consent_or_license": "required-before-enable"},
                "sha256": None,
            }
            path.write_text(json.dumps({"schema_version": "1.0", "cases": [case]}), encoding="utf-8")
            errors = benchmark_run.validate_manifest(path)
            self.assertTrue(any("does not exist" in error for error in errors))
            self.assertTrue(any("sha256" in error for error in errors))
            self.assertTrue(any("consent_or_license" in error for error in errors))


class RunnerIntegrationTests(unittest.TestCase):
    def test_run_writes_all_formats_for_provider_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root / "sample.wav"
            with wave.open(str(audio), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\x00\x00" * 1600)
            reference = root / "reference.txt"
            reference.write_text("halo dunia", encoding="utf-8")
            provider = root / "provider.py"
            provider.write_text(
                "import json\n"
                "print(json.dumps({'event': 'audio_end'}), flush=True)\n"
                "print(json.dumps({'event': 'partial', 'text': 'halo'}), flush=True)\n"
                "print(json.dumps({'event': 'stable', 'text': 'halo dunia'}), flush=True)\n"
                "print(json.dumps({'event': 'final', 'text': 'halo dunia'}), flush=True)\n",
                encoding="utf-8",
            )
            digest = hashlib.sha256(audio.read_bytes()).hexdigest()
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps({
                "schema_version": "1.0", "dataset_id": "fixture", "dataset_version": "1",
                "cases": [{
                    "id": "fixture", "enabled": True, "audio_path": "sample.wav",
                    "reference_transcript_path": "reference.txt", "reference_translation_path": None,
                    "language": "id", "target_language": None,
                    "profiles": sorted(benchmark_run.REQUIRED_PROFILES),
                    "provenance": {"contains_sensitive_data": False, "consent_or_license": "synthetic-test-fixture"},
                    "sha256": digest,
                }],
            }), encoding="utf-8")
            output_dir = root / "results"
            args = SimpleNamespace(
                manifest=manifest_path, provider="fixture", model="fixture-model",
                model_version="1", deployment="local",
                provider_command=f'"{sys.executable}" "{provider}"',
                output_dir=output_dir, case=None, timeout_seconds=10.0, sample_interval=0.05, beam_size=5,
            )
            self.assertEqual(benchmark_run.command_run(args), 0)
            payload = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["results"][0]["accuracy"]["wer"], 0.0)
            self.assertEqual(payload["results"][0]["accuracy"]["cer"], 0.0)
            self.assertEqual(payload["results"][0]["latency"]["final_latency_origin"], "audio_end_event")
            self.assertTrue((output_dir / "results.csv").is_file())
            self.assertTrue((output_dir / "report.md").is_file())


if __name__ == "__main__":
    unittest.main()

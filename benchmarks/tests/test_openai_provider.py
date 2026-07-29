import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "providers" / "openai_transcription.py"
SPEC = importlib.util.spec_from_file_location("openai_benchmark_provider", MODULE_PATH)
provider = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(provider)


class CloudBenchmarkGateTests(unittest.TestCase):
    def test_missing_key_skips_without_reading_audio(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(provider.main(), 78)

    def test_billing_and_dataset_approval_are_both_required(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}, clear=True):
            self.assertEqual(provider.main(), 78)
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-only",
            "OPENAI_BILLING_APPROVED": "true",
        }, clear=True):
            self.assertEqual(provider.main(), 78)


if __name__ == "__main__":
    unittest.main()

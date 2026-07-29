import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "stage15.py"
SPEC = importlib.util.spec_from_file_location("stage15", MODULE)
stage15 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(stage15)


class Stage15Tests(unittest.TestCase):
    def test_result_parsing(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "results.json"
            path.write_text(json.dumps({"results": [{"status": "completed", "accuracy": {"wer": 0.2, "cer": 0.1}, "latency": {"final_latency_ms": 12, "real_time_factor": 0.5, "model_load_time_ms": 3}, "resource_usage": {}}]}), encoding="utf-8")
            result = stage15._summarize_run(path)
            self.assertEqual(result["wer"], 0.2)
            self.assertEqual(result["failureRate"], 0.0)

    def test_unknown_results_are_not_promoted(self):
        self.assertIsNone(stage15._summarize_run(Path("missing-results.json")))


if __name__ == "__main__":
    unittest.main()

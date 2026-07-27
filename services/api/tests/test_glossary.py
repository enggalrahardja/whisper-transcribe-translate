import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.config import Settings
from app.services.final_transcription import (
    FinalTranscriptionConfig,
    FinalTranscriptionRequest,
    PersistentLocalFinalTranscriber,
)
from app.services.glossary import GlossaryManager, combine_prompt
from app.services.live_processor import process_live_chunk_detailed


def term(
    preferred,
    aliases=(),
    *,
    do_not_change=False,
    priority=100,
    language="*",
    active=True,
    category="product",
):
    return {
        "preferredSpelling": preferred,
        "aliases": list(aliases),
        "doNotChange": do_not_change,
        "category": category,
        "priority": priority,
        "language": language,
        "active": active,
    }


class GlossaryMatchingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "glossary.json"

    def tearDown(self):
        self.temporary.cleanup()

    def manager(self, terms, *, enabled=True):
        self.path.write_text(json.dumps({"terms": terms}), encoding="utf-8")
        return GlossaryManager(self.path, enabled=enabled)

    def test_preferred_spelling_and_case_handling(self):
        snapshot = self.manager([term("SMARTHub")]).snapshot()
        output = snapshot.correct("smarthub and SmArThUb", language="en")
        self.assertEqual(output.corrected_text, "SMARTHub and SMARTHub")
        self.assertEqual(len(output.corrections), 2)

    def test_alias_correction_preserves_raw_output(self):
        snapshot = self.manager([term("TagTrace", ["Tag Trace"])]).snapshot()
        output = snapshot.correct("Open Tag Trace now", language="en")
        self.assertEqual(output.raw_text, "Open Tag Trace now")
        self.assertEqual(output.corrected_text, "Open TagTrace now")
        self.assertEqual(output.corrections[0].source, "Tag Trace")

    def test_whole_word_matching_does_not_replace_substrings(self):
        snapshot = self.manager([term("CIS", ["cis"], do_not_change=False)]).snapshot()
        output = snapshot.correct("cis and cistern and scis", language="en")
        self.assertEqual(output.corrected_text, "CIS and cistern and scis")

    def test_punctuation_is_a_valid_boundary(self):
        snapshot = self.manager([term("SMARTHub", ["Smart Hub"])]).snapshot()
        output = snapshot.correct("(Smart Hub), [smart hub].", language="en")
        self.assertEqual(output.corrected_text, "(SMARTHub), [SMARTHub].")

    def test_higher_priority_overlapping_term_wins(self):
        manager = self.manager([
            term("RF-ID", ["RFID"], priority=10),
            term("RFID Core System", ["RFID core system"], priority=200),
        ])
        output = manager.snapshot().correct("RFID core system", language="en")
        self.assertEqual(output.corrected_text, "RFID Core System")
        self.assertEqual(output.corrections[0].priority, 200)
        self.assertEqual(manager.metrics()["correction_conflicts"], 1)

    def test_do_not_change_protects_original_case(self):
        snapshot = self.manager([
            term("CIS", ["cis"], do_not_change=True, priority=200),
            term("C.I.S.", ["cis"], priority=10),
        ]).snapshot()
        output = snapshot.correct("Keep cis exactly", language="en")
        self.assertEqual(output.corrected_text, "Keep cis exactly")
        self.assertEqual(output.corrections, ())

    def test_correction_is_idempotent(self):
        snapshot = self.manager([term("FX9600", ["FX 9600"])]).snapshot()
        first = snapshot.correct("Reader FX 9600", language="en")
        second = snapshot.correct(first.corrected_text, language="en")
        self.assertEqual(second.corrected_text, first.corrected_text)
        self.assertEqual(second.corrections, ())

    def test_inactive_and_other_language_terms_are_ignored(self):
        snapshot = self.manager([
            term("Aktif", ["active"], language="id"),
            term("Hidden", ["hidden alias"], active=False),
        ]).snapshot()
        output = snapshot.correct("active hidden alias", language="en")
        self.assertEqual(output.corrected_text, "active hidden alias")

    def test_reload_only_changes_future_snapshots(self):
        manager = self.manager([term("Alpha", ["alias"])] )
        old_snapshot = manager.snapshot()
        self.path.write_text(
            json.dumps({"terms": [term("Beta", ["alias"])]}),
            encoding="utf-8",
        )
        new_snapshot = manager.reload()
        self.assertEqual(old_snapshot.correct("alias", language="en").corrected_text, "Alpha")
        self.assertEqual(new_snapshot.correct("alias", language="en").corrected_text, "Beta")
        self.assertNotEqual(old_snapshot.version, new_snapshot.version)
        self.assertEqual(manager.metrics()["glossary_reload_count"], 1)

    def test_sessions_do_not_share_correction_input_or_output(self):
        snapshot = self.manager([term("Galva Technologies", ["Galva Tech"])]).snapshot()
        session_a = snapshot.correct("Galva Tech", language="en")
        session_b = snapshot.correct("unrelated", language="en")
        self.assertEqual(session_a.corrected_text, "Galva Technologies")
        self.assertEqual(session_b.corrected_text, "unrelated")

    def test_feature_off_does_not_read_file_or_change_result(self):
        missing = Path(self.temporary.name) / "missing.json"
        manager = GlossaryManager(missing, enabled=False)
        output = manager.snapshot().correct("Smart Hub", language="en")
        self.assertEqual(output.raw_text, output.corrected_text)
        self.assertEqual(output.corrections, ())
        self.assertEqual(manager.metrics()["glossary_terms_loaded"], 0)
        self.assertFalse(Settings().live_glossary_enabled)

    def test_metrics_include_loaded_corrections_unmatched_and_latency(self):
        manager = self.manager([term("TagTrace", ["Tag Trace", "Tag-Trace"])] )
        manager.snapshot().correct("Tag Trace", language="en")
        metrics = manager.metrics()
        self.assertEqual(metrics["glossary_terms_loaded"], 1)
        self.assertEqual(metrics["corrections_applied"], 1)
        self.assertEqual(metrics["segments_corrected"], 1)
        self.assertEqual(metrics["unmatched_aliases"], 1)
        self.assertGreaterEqual(metrics["correction_latency_ms"], 0)


class GlossaryPromptIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "glossary.json"
        self.path.write_text(
            json.dumps({"terms": [term("SMARTHub", ["Smart Hub"])]}),
            encoding="utf-8",
        )
        self.manager = GlossaryManager(self.path, enabled=True)
        self.snapshot = self.manager.snapshot()

    def tearDown(self):
        self.temporary.cleanup()

    def test_prompt_combines_user_and_terminology_context(self):
        prompt = combine_prompt("Meeting context", self.snapshot.prompt_context)
        self.assertIn("Meeting context", prompt)
        self.assertIn("SMARTHub", prompt)

    def test_live_path_uses_context_and_keeps_raw_and_corrected_output(self):
        now = datetime.now(timezone.utc)
        document = {
            "session_id": "session-a",
            "status": "active",
            "language": "en",
            "model": "base",
            "started_at": now,
            "ended_at": None,
            "duration": 0,
            "partial_text": "",
            "final_text": "",
            "segments": [],
            "error": None,
            "created_at": now,
            "updated_at": now,
            "audio_cursor": 0,
            "processed_chunk_hashes": [],
        }
        settings = SimpleNamespace(
            transcription=SimpleNamespace(
                fp16=False,
                beam_size=5,
                temperature=0.0,
                initial_prompt="Meeting context",
                word_timestamps=False,
            ),
            live_transcription=SimpleNamespace(overlap_duration_seconds=0.5),
        )
        with (
            patch("app.services.live_processor.claim_live_chunk", return_value=(document, True)),
            patch("app.services.live_processor.append_live_result", return_value="session") as append,
            patch("app.services.live_processor.get_application_settings", return_value=settings),
            patch("app.services.live_processor._adapter.transcribe", return_value={
                "text": "Smart Hub",
                "segments": [{"start": 0, "end": 1, "text": "Smart Hub"}],
            }) as transcribe,
        ):
            _, _, detail = process_live_chunk_detailed(
                "session-a",
                b"RIFFinvalid",
                glossary=self.snapshot,
            )
        self.assertIn("SMARTHub", transcribe.call_args.kwargs["initial_prompt"])
        self.assertEqual(detail.raw_text, "Smart Hub")
        self.assertEqual(detail.corrected_text, "SMARTHub")
        self.assertEqual(len(detail.corrections), 1)
        self.assertEqual(append.call_args.args[2], "SMARTHub")
        self.assertEqual(append.call_args.args[3][0]["text"], "SMARTHub")
        self.assertEqual(append.call_args.args[3][0]["start"], 0)
        self.assertEqual(append.call_args.args[3][0]["end"], 1)

    def test_accurate_final_uses_same_snapshot_without_changing_timestamps(self):
        class FakeAdapter:
            effective_device = "cpu"

            def load_model(self, _model):
                return object()

            def transcribe(self, _audio_path, **kwargs):
                self.prompt = kwargs["initial_prompt"]
                return {
                    "text": "Smart Hub",
                    "language": "en",
                    "segments": [{"start": 0.1, "end": 0.5, "text": "Smart Hub"}],
                }

        adapter = FakeAdapter()
        transcriber = PersistentLocalFinalTranscriber(
            FinalTranscriptionConfig(device="cpu", compute_type="float32"),
            adapter=adapter,
            checkpoint_resolver=lambda _model: Path("C:/models/base.pt"),
        )
        request = FinalTranscriptionRequest(
            session_id="session-a",
            segment_id="segment-1",
            sequence_start=1,
            sequence_end=3,
            start_ms=200,
            end_ms=800,
            language="en",
            audio_wav=b"RIFF complete audio",
            glossary=self.snapshot,
        )
        output = transcriber.transcribe(request, 1)
        self.assertIn("SMARTHub", adapter.prompt)
        self.assertEqual(output.raw_text, "Smart Hub")
        self.assertEqual(output.text, "SMARTHub")
        self.assertEqual(output.metadata.timestamps[0]["startMs"], 300)
        self.assertEqual(output.metadata.timestamps[0]["endMs"], 700)
        self.assertEqual(output.metadata.timestamps[0]["rawText"], "Smart Hub")
        self.assertEqual(output.metadata.timestamps[0]["text"], "SMARTHub")

    def test_glossary_reload_does_not_reload_final_model(self):
        class FakeAdapter:
            effective_device = "cpu"

            def __init__(self):
                self.loads = 0

            def load_model(self, _model):
                self.loads += 1

            def transcribe(self, _audio_path, **_kwargs):
                return {
                    "text": "Smart Hub",
                    "language": "en",
                    "segments": [{"start": 0, "end": 0.5, "text": "Smart Hub"}],
                }

        adapter = FakeAdapter()
        transcriber = PersistentLocalFinalTranscriber(
            FinalTranscriptionConfig(device="cpu", compute_type="float32"),
            adapter=adapter,
            checkpoint_resolver=lambda _model: Path("C:/models/base.pt"),
        )
        base = {
            "session_id": "session-a",
            "segment_id": "segment-1",
            "sequence_start": 1,
            "sequence_end": 3,
            "start_ms": 0,
            "end_ms": 500,
            "language": "en",
            "audio_wav": b"RIFF complete audio",
        }
        transcriber.transcribe(FinalTranscriptionRequest(**base, glossary=self.snapshot), 1)
        reloaded = self.manager.reload()
        transcriber.transcribe(
            FinalTranscriptionRequest(**{**base, "segment_id": "segment-2"}, glossary=reloaded),
            1,
        )
        self.assertEqual(adapter.loads, 1)


if __name__ == "__main__":
    unittest.main()

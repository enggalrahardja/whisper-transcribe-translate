import os
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.config import Settings, validate_startup_configuration
from app.security import (
    Principal, SlidingWindowLimiter, allow_bursty_throughput, authenticate_token, authorize_owner,
    enforce_concurrent_limit, is_allowed_web_origin, redact_value, safe_error,
    validate_audio_frame_size, websocket_idle_expired, websocket_principal,
)
from app.services.pcm_ingestion import PcmChunkMetadata, PcmProtocolError
from app.services.production_hardening import (
    MemoryAuditSink, audit_event, cleanup_retention, dependency_readiness,
)
from app.models.live import CreateLiveSessionRequest
from app.services.live_sessions import create_live_session, record_disconnect


def settings(**values):
    if values.get("app_env") == "production":
        values.setdefault("release_profile", "production-local")
    return Settings(_env_file=None, **values)


class FakeCursor(list):
    def limit(self, value):
        return FakeCursor(self[:value])


class FakeCollection:
    def find(self, *_args, **_kwargs):
        return FakeCursor()

    def delete_many(self, _query):
        return type("Delete", (), {"deleted_count": 0})()


class FakeDatabase(dict):
    def __getitem__(self, key):
        return self.setdefault(key, FakeCollection())


class CaptureCollection:
    def __init__(self):
        self.call = None

    def update_one(self, query, update):
        self.call = (query, update)

    def insert_one(self, document):
        self.inserted = document


class CaptureDatabase(dict):
    def __init__(self, collection):
        super().__init__(); self.collection = collection

    def __getitem__(self, _key):
        return self.collection


class FakeWebSocket:
    def __init__(self, authorization=None, access_token=None, protocol=None):
        self.headers = {"authorization": authorization} if authorization else {}
        if protocol:
            self.headers["sec-websocket-protocol"] = protocol
        self.query_params = {"access_token": access_token} if access_token else {}


class AuthenticationTests(unittest.TestCase):
    def test_authentication_roles_and_failure(self):
        config = settings(security_auth_enabled=True, security_tokens_json='{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":{"userId":"alice","role":"admin"}}')
        with patch("app.security.get_settings", return_value=config):
            principal = authenticate_token("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
            self.assertEqual((principal.user_id, principal.role), ("alice", "admin"))
            with self.assertRaises(HTTPException) as context:
                authenticate_token("wrong")
            self.assertEqual(context.exception.status_code, 401)

    def test_development_auth_is_legacy_compatible(self):
        with patch("app.security.get_settings", return_value=settings()):
            self.assertTrue(authenticate_token(None).is_admin)

    def test_owner_or_admin_authorization(self):
        authorize_owner(Principal("alice"), "alice")
        authorize_owner(Principal("root", "admin"), "alice")
        with self.assertRaises(HTTPException):
            authorize_owner(Principal("bob"), "alice")

    def test_websocket_auth_and_origin(self):
        config = settings(security_auth_enabled=True, security_tokens_json='{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":"alice"}', security_trusted_origins="https://app.example")
        with patch("app.security.get_settings", return_value=config):
            self.assertEqual(websocket_principal(FakeWebSocket("Bearer aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")).user_id, "alice")
            self.assertEqual(websocket_principal(FakeWebSocket(protocol="bearer, aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")).user_id, "alice")
            self.assertTrue(is_allowed_web_origin("https://app.example"))
            self.assertFalse(is_allowed_web_origin("https://evil.example"))


class LimitAndInputTests(unittest.TestCase):
    def test_sliding_rate_limit(self):
        now = [0.0]
        limiter = SlidingWindowLimiter(lambda: now[0])
        self.assertTrue(limiter.allow("audio", "alice", 10, cost=6))
        self.assertFalse(limiter.allow("audio", "alice", 10, cost=5))
        now[0] = 61.0
        self.assertTrue(limiter.allow("audio", "alice", 10, cost=10))
        bounded = SlidingWindowLimiter(lambda: 0.0, max_identities=2)
        for user in ("one", "two", "three"):
            bounded.allow("session", user, 1)
        self.assertEqual(len(bounded._events), 2)

    def test_audio_rate_limit_allows_valid_wav_chunk_bursts(self):
        now = [0.0]
        limiter = SlidingWindowLimiter(lambda: now[0])
        wav_chunk_size = 288_044
        for _ in range(24):
            self.assertTrue(allow_bursty_throughput(
                "audio", "alice", 128_000,
                cost=wav_chunk_size,
                maximum_burst=524_288,
                limiter=limiter,
            ))
            now[0] += 2.5
        self.assertFalse(allow_bursty_throughput(
            "audio", "alice", 128_000,
            cost=524_289,
            maximum_burst=524_288,
            limiter=limiter,
        ))

    def test_pcm_metadata_is_strict(self):
        valid = {"type":"pcm_chunk", "sessionId":"a" * 32, "sequence":0, "captureTimestampMs":1.0, "sampleRate":16000, "channelCount":1, "chunkDurationMs":200.0, "byteLength":6400}
        self.assertEqual(PcmChunkMetadata.from_payload(valid).byte_length, 6400)
        for changed in (
            {**valid, "sequence": 1.5},
            {**valid, "sampleRate": 48000},
            {**valid, "extra": "value"},
            {**valid, "byteLength": 6401},
        ):
            with self.assertRaises(PcmProtocolError):
                PcmChunkMetadata.from_payload(changed)

    def test_concurrency_chunk_idle_and_heartbeat_bounds(self):
        with self.assertRaises(HTTPException):
            enforce_concurrent_limit(8, 8)
        validate_audio_frame_size(6400, 8000)
        with self.assertRaises(ValueError):
            validate_audio_frame_size(8001, 8000)
        self.assertFalse(websocket_idle_expired(10.0, 39.9, 30.0))
        self.assertTrue(websocket_idle_expired(10.0, 40.0, 30.0))

    def test_graceful_disconnect_only_marks_reconnectable_session(self):
        collection = CaptureCollection()
        with patch("app.services.live_sessions.get_database", return_value=CaptureDatabase(collection)):
            record_disconnect("session-safe")
        query, update = collection.call
        self.assertEqual(query["session_id"], "session-safe")
        self.assertEqual(query["status"]["$in"], ["active", "paused"])
        self.assertIn("last_disconnected_at", update["$set"])

    def test_session_creation_persists_owner(self):
        collection = CaptureCollection()
        with patch("app.services.live_sessions.get_database", return_value=CaptureDatabase(collection)), patch(
            "app.services.live_sessions.whisper_model_usage", return_value=nullcontext()
        ):
            create_live_session(CreateLiveSessionRequest(), owner_id="alice")
        self.assertEqual(collection.inserted["owner_id"], "alice")
        self.assertEqual(collection.inserted["transcription_backend"], "pytorch")

    def test_session_creation_persists_selected_transcription_runtime(self):
        collection = CaptureCollection()
        payload = CreateLiveSessionRequest(
            transcription_backend="faster-whisper",
            transcription_device="cpu",
            transcription_compute_type="int8",
        )
        with patch("app.services.live_sessions.get_database", return_value=CaptureDatabase(collection)), patch(
            "app.services.live_sessions.resolve_backend_config"
        ), patch(
            "app.services.live_sessions.whisper_model_usage", return_value=nullcontext()
        ) as usage:
            session = create_live_session(payload, owner_id="alice")
        self.assertEqual(session.transcription_backend, "faster-whisper")
        self.assertEqual(session.transcription_device, "cpu")
        self.assertEqual(session.transcription_compute_type, "int8")
        usage.assert_called_once_with("base", "live-session-create", backend="faster-whisper")


class RedactionRetentionAndReadinessTests(unittest.TestCase):
    def test_error_and_audit_redaction(self):
        self.assertNotIn("Users", safe_error(RuntimeError("C:\\Users\\name\\model.pt token=abc")))
        self.assertEqual(redact_value({"token": "abc"})["token"], "[REDACTED]")
        sink = MemoryAuditSink()
        audit_event("session_start", principal=Principal("alice"), session_id="safe", metadata={"audioContent": "private", "count": 2, "checkpointPath": "C:\\model.pt"}, sink=sink)
        event = sink.events[0]
        self.assertEqual(event["metadata"], {"count": 2})
        self.assertNotIn("private", str(event))

    def test_cleanup_dry_run_and_enforcement_are_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            upload = Path(temporary, "uploads"); upload.mkdir()
            expired = upload / "expired.wav"; expired.write_bytes(b"RIFF")
            old = (datetime.now(timezone.utc) - timedelta(days=31)).timestamp()
            os.utime(expired, (old, old))
            config = settings(storage_root=temporary, retention_audio_days=30, retention_cleanup_batch_size=1)
            with patch("app.services.production_hardening.get_database", return_value=FakeDatabase()):
                preview = cleanup_retention(dry_run=True, settings=config)
                self.assertTrue(expired.exists()); self.assertEqual(preview.eligible, 1)
                applied = cleanup_retention(dry_run=False, settings=config)
                self.assertFalse(expired.exists()); self.assertEqual(applied.deleted, 1)

    def test_readiness_fails_required_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); model_dir = root / "models"; model_dir.mkdir(); (model_dir / "base.pt").touch()
            result = dependency_readiness(settings=settings(storage_root=str(root / "storage"), whisper_model_dir=model_dir), mongo_ping=lambda: None, worker_check=lambda: False)
            self.assertEqual(result["status"], "not_ready")
            self.assertFalse(result["checks"]["workerSupervisor"]["ready"])


class ProductionConfigurationTests(unittest.TestCase):
    def test_production_rejects_unsafe_configuration(self):
        with self.assertRaisesRegex(ValueError, "Authentication"):
            validate_startup_configuration(settings(app_env="production"))
        with self.assertRaisesRegex(ValueError, "retention"):
            validate_startup_configuration(settings(retention_audit_days=0))
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            validate_startup_configuration(settings(security_profile="Cloud"))
        with self.assertRaisesRegex(ValueError, "Debug"):
            validate_startup_configuration(settings(app_env="production", app_debug=True))

    def test_production_rejects_placeholder_and_missing_model(self):
        base = dict(
            app_env="production", security_auth_enabled=True,
            web_origin="https://app.example", security_trusted_origins="https://app.example",
        )
        with self.assertRaisesRegex(ValueError, "non-default"):
            validate_startup_configuration(settings(**base, security_tokens_json='{"replace-with-random-token-at-least-32-characters":"admin"}'))
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "checkpoint"):
                validate_startup_configuration(settings(
                    **base, security_tokens_json='{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":"admin"}',
                    whisper_model_dir=Path(temporary),
                ))

    def test_valid_local_production_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = Path(temporary); (model_dir / "base.pt").touch()
            config = settings(
                app_env="production", security_auth_enabled=True,
                security_tokens_json='{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":{"userId":"admin","role":"admin"}}',
                web_origin="https://app.example", security_trusted_origins="https://app.example", security_require_https=True,
                whisper_model_dir=model_dir, security_profile="Private",
            )
            validate_startup_configuration(config)
            self.assertFalse(config.live_translation_enabled)
            self.assertNotIn("openai", config.security_tokens_json.lower())


if __name__ == "__main__":
    unittest.main()

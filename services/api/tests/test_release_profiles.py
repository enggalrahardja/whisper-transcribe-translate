import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings, validate_startup_configuration
from app.services.release_profiles import (
    ReleaseProfileError,
    load_release_profiles,
    validate_release_profile,
)


ROOT = Path(__file__).resolve().parents[3]
CATALOGUE = ROOT / "config/release-profiles.json"


class ReleaseProfileTests(unittest.TestCase):
    def test_development_local_is_default_and_cloud_free(self):
        settings = Settings(_env_file=None)
        validate_release_profile(settings, CATALOGUE)
        self.assertEqual(settings.release_profile, "development-local")
        self.assertEqual(settings.live_transcription_provider, "local")
        self.assertEqual(settings.live_final_provider, "local")
        self.assertFalse(settings.openai_api_key)

    def test_production_local_needs_no_cloud_credentials(self):
        settings = Settings(
            _env_file=None, app_env="production", release_profile="production-local",
            live_transcription_provider="local", live_final_provider="local",
        )
        validate_release_profile(settings, CATALOGUE)
        self.assertFalse(settings.openai_api_key)

    def test_production_hybrid_requires_key_consent_and_explicit_providers(self):
        base = dict(
            _env_file=None, app_env="production", release_profile="production-hybrid",
            live_transcription_provider="openai", live_final_provider="openai",
        )
        with self.assertRaisesRegex(ReleaseProfileError, "OPENAI_API_KEY"):
            validate_release_profile(Settings(**base), CATALOGUE)
        with self.assertRaisesRegex(ReleaseProfileError, "consent"):
            validate_release_profile(Settings(**base, openai_api_key="test-secret"), CATALOGUE)
        settings = Settings(
            **base, openai_api_key="test-secret", openai_external_audio_consent=True,
            live_pcm_streaming_enabled=True, live_vad_enabled=True,
            live_transcript_state_enabled=True,
        )
        validate_release_profile(settings, CATALOGUE)

    def test_profile_mismatch_and_unknown_profile_are_rejected(self):
        with self.assertRaisesRegex(ReleaseProfileError, "APP_ENV"):
            validate_release_profile(
                Settings(_env_file=None, release_profile="production-local"), CATALOGUE
            )
        with self.assertRaisesRegex(ReleaseProfileError, "Unsupported"):
            validate_release_profile(
                Settings(_env_file=None, release_profile="unknown"), CATALOGUE
            )

    def test_catalogue_schema_and_exact_names(self):
        payload = load_release_profiles(CATALOGUE)
        self.assertEqual(payload["defaultProfile"], "development-local")
        self.assertEqual(set(payload["profiles"]), {
            "development-local", "production-local", "production-hybrid",
        })
        with tempfile.TemporaryDirectory() as temporary:
            invalid = Path(temporary) / "profiles.json"
            invalid.write_text(json.dumps({"schemaVersion": 2, "profiles": {}}), encoding="utf-8")
            with self.assertRaises(ReleaseProfileError):
                load_release_profiles(invalid)

    def test_full_startup_validation_checks_profile_first(self):
        with self.assertRaisesRegex(ValueError, "development-local requires APP_ENV=development"):
            validate_startup_configuration(Settings(_env_file=None, app_env="production"))

    def test_production_profiles_pass_full_startup_validation_when_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            model_dir = Path(temporary)
            (model_dir / "base.pt").touch()
            common = dict(
                _env_file=None, app_env="production", app_debug=False,
                security_auth_enabled=True,
                security_tokens_json='{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":{"userId":"admin","role":"admin"}}',
                security_connection_ticket_secret="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                web_origin="https://app.example",
                security_trusted_origins="https://app.example",
                security_require_https=True, whisper_model_dir=model_dir,
            )
            validate_startup_configuration(Settings(
                **common, release_profile="production-local",
                live_transcription_provider="local", live_final_provider="local",
                security_profile="Private",
            ))
            validate_startup_configuration(Settings(
                **common, release_profile="production-hybrid",
                live_transcription_provider="openai", live_final_provider="openai",
                openai_api_key="server-side-test-secret",
                openai_external_audio_consent=True,
                live_pcm_streaming_enabled=True, live_vad_enabled=True,
                live_transcript_state_enabled=True, security_profile="Fast",
            ))


if __name__ == "__main__":
    unittest.main()

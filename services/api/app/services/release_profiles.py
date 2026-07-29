"""Stage 18 release-profile catalogue and startup invariants."""

from __future__ import annotations

import json
from pathlib import Path


class ReleaseProfileError(ValueError):
    pass


def load_release_profiles(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != 1 or not isinstance(payload.get("profiles"), dict):
        raise ReleaseProfileError("Release profile catalogue is invalid")
    profiles = payload["profiles"]
    required = {"development-local", "production-local", "production-hybrid"}
    if set(profiles) != required or payload.get("defaultProfile") != "development-local":
        raise ReleaseProfileError("Release profile names/default do not match Stage 18")
    for name, profile in profiles.items():
        if profile.get("environment") not in {"development", "production"}:
            raise ReleaseProfileError(f"{name} has an invalid environment")
        if profile.get("liveProvider") not in {"local", "openai"} or profile.get("finalProvider") not in {"local", "openai"}:
            raise ReleaseProfileError(f"{name} has an invalid provider")
    return payload


def validate_release_profile(settings: object, catalogue_path: Path) -> None:
    payload = load_release_profiles(catalogue_path)
    name = str(getattr(settings, "release_profile"))
    profiles = payload["profiles"]
    if name not in profiles:
        raise ReleaseProfileError(f"Unsupported release profile: {name}")
    profile = profiles[name]
    environment = str(getattr(settings, "app_env")).lower()
    live_provider = str(getattr(settings, "live_transcription_provider"))
    final_provider = str(getattr(settings, "live_final_provider"))
    if environment != profile["environment"]:
        raise ReleaseProfileError(f"{name} requires APP_ENV={profile['environment']}")
    if live_provider != profile["liveProvider"] or final_provider != profile["finalProvider"]:
        raise ReleaseProfileError(
            f"{name} requires live/final providers {profile['liveProvider']}/{profile['finalProvider']}"
        )
    if profile["requiresCloudCredential"] and not str(getattr(settings, "openai_api_key")).strip():
        raise ReleaseProfileError(f"{name} requires OPENAI_API_KEY")
    if profile["requiresExternalAudioConsent"] and not bool(getattr(settings, "openai_external_audio_consent")):
        raise ReleaseProfileError(f"{name} requires external-audio consent")

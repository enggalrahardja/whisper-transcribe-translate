"""Configuration-driven, local-only model profile resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PROFILE_PATH = PROJECT_ROOT / "config/model-profiles.json"
WHISPER_FILES = {
    "base": "base.pt", "small": "small.pt", "medium": "medium.pt",
    "large-v3": "large-v3.pt", "large-v3-turbo": "large-v3-turbo.pt",
}
LOCAL_PROVIDERS = {"local-marian", "local-speechbrain"}


@dataclass(frozen=True)
class ProfileResolution:
    requested: str
    selected: str
    configuration: dict[str, Any]
    fallback_chain: tuple[str, ...]
    warnings: tuple[str, ...]


def load_profile_catalogue(path: Path = DEFAULT_PROFILE_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_profile_catalogue(payload)
    return payload


def validate_profile_catalogue(payload: dict[str, Any]) -> None:
    if payload.get("schemaVersion") != "1.0":
        raise ValueError("profile schemaVersion must be 1.0")
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict) or set(profiles) != {"Fast", "Balanced", "Accurate", "Private"}:
        raise ValueError("profiles must define Fast, Balanced, Accurate, and Private")
    if payload.get("defaultProfile") not in profiles:
        raise ValueError("defaultProfile must reference a profile")
    for name, profile in profiles.items():
        if profile.get("fallback") not in profiles:
            raise ValueError(f"{name} fallback is invalid")
        for section in ("live", "accurateFinal"):
            model = profile[section].get("model")
            if model not in WHISPER_FILES or profile[section].get("checkpoint") != WHISPER_FILES[model]:
                raise ValueError(f"{name}.{section} has an unsupported model/checkpoint")
            if profile[section].get("device") not in {"auto", "cpu", "cuda"}:
                raise ValueError(f"{name}.{section} device is invalid")
            if int(profile[section].get("beamSize", 0)) < 1:
                raise ValueError(f"{name}.{section} beamSize must be positive")
        for component in ("translation", "diarization"):
            if component in profile and profile[component].get("provider") not in LOCAL_PROVIDERS:
                raise ValueError(f"{name}.{component} must use a local provider")
        if name == "Private" and profile.get("networkProvidersAllowed") is not False:
            raise ValueError("Private must prohibit network providers")


def resolve_profile(
    requested: str | None,
    *,
    model_dir: Path,
    cuda_available: bool,
    catalogue: dict[str, Any] | None = None,
) -> ProfileResolution:
    source = catalogue or load_profile_catalogue()
    profiles = source["profiles"]
    current = requested or source["defaultProfile"]
    if current not in profiles:
        raise ValueError(f"Unknown model profile: {current}")
    chain: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        chain.append(current)
        profile = profiles[current]
        required = [profile["live"]]
        if profile["accurateFinal"].get("enabled"):
            required.append(profile["accurateFinal"])
        missing = [item["model"] for item in required if not (model_dir / item["checkpoint"]).is_file()]
        cuda_required = any(item.get("device") == "cuda" for item in required) and not cuda_available
        if not missing and not cuda_required:
            return ProfileResolution(requested or source["defaultProfile"], current, profile, tuple(chain), tuple(warnings))
        if missing:
            warnings.append(f"{current}: unavailable checkpoint(s): {', '.join(sorted(set(missing)))}")
        if cuda_required:
            warnings.append(f"{current}: CUDA requested but unavailable")
        current = profile["fallback"]
    raise RuntimeError("No usable local profile fallback")

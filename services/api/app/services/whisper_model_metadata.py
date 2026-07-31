import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import whisper  # noqa: E402

SUPPORTED_MODEL_BACKENDS = ("pytorch", "faster-whisper")
SUPPORTED_WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3", "turbo")
TRUSTED_MODEL_HOSTS = {"openaipublic.azureedge.net"}
PYTORCH_TURBO_URL = (
    "https://openaipublic.azureedge.net/main/whisper/models/"
    "aff26ae408abcba5fbf8813c21e62b0941638c5f6eebfb145be0c9839262a19a/"
    "large-v3-turbo.pt"
)
PYTORCH_SOURCE_IDS = {
    "tiny": "tiny",
    "base": "base",
    "small": "small",
    "medium": "medium",
    "large-v3": "large",
    "turbo": "turbo",
}
PYTORCH_FILE_NAMES = {
    "tiny": "tiny.pt",
    "base": "base.pt",
    "small": "small.pt",
    "medium": "medium.pt",
    "large-v3": "large-v3.pt",
    "turbo": "turbo.pt",
}
FASTER_WHISPER_REPOSITORIES = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
MODEL_PRESET_BADGES = {
    "small": "Balanced",
    "large-v3": "Best accuracy",
    "turbo": "Fastest",
}


@dataclass(frozen=True)
class WhisperModelMetadata:
    backend: str
    model: str
    backend_model_id: str
    source: str
    storage_name: str
    expected_checksum: str | None
    expected_size_bytes: int | None
    storage_kind: str

    @property
    def file_name(self) -> str:
        return self.storage_name

    @property
    def source_url(self) -> str:
        return self.source


def _pytorch_metadata(model: str) -> WhisperModelMetadata:
    source_id = PYTORCH_SOURCE_IDS[model]
    url = PYTORCH_TURBO_URL if model == "turbo" else whisper._MODELS[source_id]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in TRUSTED_MODEL_HOSTS:
        raise RuntimeError(f"Untrusted Whisper model source for {model}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError(f"Invalid Whisper model source for {model}")
    parts = Path(parsed.path).parts
    if len(parts) < 2 or len(parts[-2]) != 64:
        raise RuntimeError(f"Invalid Whisper checksum metadata for {model}")
    try:
        int(parts[-2], 16)
    except ValueError as exc:
        raise RuntimeError(f"Invalid Whisper checksum metadata for {model}") from exc
    return WhisperModelMetadata(
        backend="pytorch",
        model=model,
        backend_model_id=model,
        source=url,
        storage_name=PYTORCH_FILE_NAMES[model],
        expected_checksum=parts[-2],
        expected_size_bytes=None,
        storage_kind="checkpoint",
    )


def _faster_whisper_metadata(model: str) -> WhisperModelMetadata:
    return WhisperModelMetadata(
        backend="faster-whisper",
        model=model,
        backend_model_id=model,
        source=FASTER_WHISPER_REPOSITORIES[model],
        storage_name=model,
        expected_checksum=None,
        expected_size_bytes=None,
        storage_kind="ctranslate2_directory",
    )


MODEL_REGISTRY_METADATA = {
    (backend, model): (
        _pytorch_metadata(model) if backend == "pytorch" else _faster_whisper_metadata(model)
    )
    for backend in SUPPORTED_MODEL_BACKENDS
    for model in SUPPORTED_WHISPER_MODELS
}

# Backward-compatible PyTorch metadata export for existing callers.
WHISPER_MODEL_METADATA = {
    model: MODEL_REGISTRY_METADATA[("pytorch", model)]
    for model in SUPPORTED_WHISPER_MODELS
}

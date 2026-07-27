import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import whisper  # noqa: E402

SUPPORTED_WHISPER_MODELS = ("tiny", "base", "small", "medium", "large")
TRUSTED_MODEL_HOSTS = {"openaipublic.azureedge.net"}
CANONICAL_FILE_NAMES = {
    "tiny": "tiny.pt",
    "base": "base.pt",
    "small": "small.pt",
    "medium": "medium.pt",
    "large": "large-v3.pt",
}


@dataclass(frozen=True)
class WhisperModelMetadata:
    model: str
    source_url: str
    file_name: str
    expected_checksum: str
    expected_size_bytes: int | None


def _metadata_for(model: str) -> WhisperModelMetadata:
    url = whisper._MODELS[model]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in TRUSTED_MODEL_HOSTS:
        raise RuntimeError(f"Untrusted Whisper model source for {model}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError(f"Invalid Whisper model source for {model}")
    parts = Path(parsed.path).parts
    if len(parts) < 2 or len(parts[-2]) != 64:
        raise RuntimeError(f"Invalid Whisper checksum metadata for {model}")
    if parts[-1] != CANONICAL_FILE_NAMES[model]:
        raise RuntimeError(f"Invalid Whisper file name metadata for {model}")
    try:
        int(parts[-2], 16)
    except ValueError as exc:
        raise RuntimeError(f"Invalid Whisper checksum metadata for {model}") from exc
    return WhisperModelMetadata(
        model=model,
        source_url=url,
        file_name=parts[-1],
        expected_checksum=parts[-2],
        # The existing Whisper metadata has no authoritative content length.
        expected_size_bytes=None,
    )


WHISPER_MODEL_METADATA = {
    model: _metadata_for(model) for model in SUPPORTED_WHISPER_MODELS
}

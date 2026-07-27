from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Whisper Transcribe & Translate API"
    app_env: str = "development"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    web_origin: str = "http://localhost:3000"
    mongodb_uri: str = "mongodb://127.0.0.1:27017"
    mongodb_database: str = "whisper_transcribe_translate"
    storage_root: str = "storage"
    whisper_model_dir: Path = PROJECT_ROOT / "storage/models/whisper"
    whisper_download_timeout_seconds: float = 30.0
    whisper_download_max_retries: int = 2
    whisper_download_heartbeat_seconds: float = 5.0
    whisper_download_stale_seconds: int = 60
    whisper_download_poll_seconds: float = 1.0
    worker_poll_interval_seconds: float = 1.0
    worker_heartbeat_interval_seconds: float = 5.0
    worker_stale_after_seconds: int = 60

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("whisper_model_dir", mode="before")
    @classmethod
    def resolve_whisper_model_dir(cls, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()

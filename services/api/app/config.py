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
    live_pcm_streaming_enabled: bool = False
    live_pcm_max_buffer_seconds: float = 10.0
    live_pcm_transcription_window_seconds: float = 3.0
    live_pcm_max_sessions: int = 128
    live_pcm_max_sequence_gap: int = 128
    live_vad_enabled: bool = False
    live_vad_speech_threshold: float = 0.6
    live_vad_silence_duration_ms: int = 600
    live_vad_pre_speech_duration_ms: int = 300
    live_vad_minimum_speech_duration_ms: int = 250
    live_vad_maximum_segment_duration_ms: int = 20_000
    live_vad_segment_overlap_ms: int = 500
    live_vad_webrtc_mode: int = 2
    live_transcript_state_enabled: bool = False

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

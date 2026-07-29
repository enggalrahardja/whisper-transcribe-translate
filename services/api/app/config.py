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
    app_debug: bool = False
    security_auth_enabled: bool = False
    security_tokens_json: str = "{}"
    security_trusted_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    security_require_https: bool = False
    security_profile: str = "Fast"
    rate_session_create_per_minute: int = 10
    rate_websocket_connect_per_minute: int = 20
    rate_audio_bytes_per_second: int = 128_000
    rate_glossary_reload_per_minute: int = 2
    rate_monitoring_per_minute: int = 30
    limit_concurrent_sessions: int = 8
    limit_session_duration_seconds: int = 14_400
    limit_audio_chunk_bytes: int = 524_288
    limit_queue_depth: int = 256
    limit_upload_bytes: int = 1_073_741_824
    limit_reconnect_attempts: int = 20
    websocket_idle_timeout_seconds: float = 30.0
    websocket_heartbeat_seconds: float = 10.0
    retention_session_metadata_days: int = 90
    retention_audio_days: int = 30
    retention_transcript_days: int = 90
    retention_translation_days: int = 90
    retention_metrics_days: int = 30
    retention_audit_days: int = 365
    retention_cleanup_batch_size: int = 500
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
    live_accurate_final_enabled: bool = False
    live_final_model: str = "base"
    live_final_device: str = "auto"
    live_final_compute_type: str = "auto"
    live_final_beam_size: int = 5
    live_final_timeout_seconds: float = 30.0
    live_final_max_retries: int = 1
    live_final_worker_concurrency: int = 1
    live_final_queue_capacity: int = 128
    live_glossary_enabled: bool = False
    live_glossary_path: Path = PROJECT_ROOT / "config/glossary.development.json"
    live_glossary_prompt_max_terms: int = 64
    live_translation_enabled: bool = False
    live_translation_model: str = "Helsinki-NLP/opus-mt-id-en"
    live_translation_model_revision: str = "main"
    live_translation_source_language: str = "id"
    live_translation_target_language: str = "en"
    live_translation_device: str = "auto"
    live_translation_compute_type: str = "auto"
    live_translation_beam_size: int = 4
    live_translation_timeout_seconds: float = 20.0
    live_translation_max_retries: int = 1
    live_translation_worker_concurrency: int = 1
    live_translation_queue_capacity: int = 64
    live_translation_context_segments: int = 3
    live_translation_quality_enabled: bool = False
    live_translation_quality_timeout_seconds: float = 2.0
    live_translation_quality_max_retries: int = 1
    live_translation_quality_worker_concurrency: int = 1
    live_translation_quality_queue_capacity: int = 64
    live_diarization_enabled: bool = False
    live_diarization_model: str = "speechbrain/spkrec-ecapa-voxceleb"
    live_diarization_model_revision: str = "main"
    live_diarization_device: str = "auto"
    live_diarization_compute_type: str = "auto"
    live_diarization_similarity_threshold: float = 0.72
    live_diarization_low_confidence_threshold: float = 0.65
    live_diarization_timeout_seconds: float = 30.0
    live_diarization_max_retries: int = 1
    live_diarization_worker_concurrency: int = 1
    live_diarization_queue_capacity: int = 64
    live_transcript_postprocess_enabled: bool = False
    live_transcript_postprocess_filler_mode: str = "preserve"
    live_transcript_postprocess_filler_words: str = "uh,um,erm,hmm,eh,anu,eee,mmm"
    live_transcript_postprocess_paragraph_sentences: int = 3
    live_transcript_postprocess_timeout_seconds: float = 2.0
    live_transcript_postprocess_max_retries: int = 1
    live_transcript_postprocess_worker_concurrency: int = 1
    live_transcript_postprocess_queue_capacity: int = 64
    live_processing_worker_concurrency: int = 1
    live_processing_worker_queue_capacity: int = 32
    live_processing_worker_timeout_ms: int = 30_000
    live_pipeline_persistence_enabled: bool = False
    live_pipeline_persistence_queue_capacity: int = 256
    live_pipeline_persistence_max_retries: int = 2

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("whisper_model_dir", mode="before")
    @classmethod
    def resolve_whisper_model_dir(cls, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    @field_validator("live_glossary_path", mode="before")
    @classmethod
    def resolve_live_glossary_path(cls, value: str | Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()


def validate_startup_configuration(settings: Settings) -> None:
    """Reject unsafe production settings while keeping development friction low."""
    positive = {
        "session create rate": settings.rate_session_create_per_minute,
        "WebSocket rate": settings.rate_websocket_connect_per_minute,
        "audio throughput": settings.rate_audio_bytes_per_second,
        "concurrent sessions": settings.limit_concurrent_sessions,
        "session duration": settings.limit_session_duration_seconds,
        "chunk bytes": settings.limit_audio_chunk_bytes,
        "queue depth": settings.limit_queue_depth,
        "upload bytes": settings.limit_upload_bytes,
        "reconnect attempts": settings.limit_reconnect_attempts,
        "cleanup batch": settings.retention_cleanup_batch_size,
    }
    invalid = [name for name, value in positive.items() if value <= 0]
    retentions = {
        "session metadata": settings.retention_session_metadata_days,
        "audio": settings.retention_audio_days,
        "transcript": settings.retention_transcript_days,
        "translation": settings.retention_translation_days,
        "metrics": settings.retention_metrics_days,
        "audit": settings.retention_audit_days,
    }
    invalid.extend(f"{name} retention" for name, value in retentions.items() if value < 1)
    if invalid:
        raise ValueError("Invalid production limits: " + ", ".join(invalid))
    if settings.limit_audio_chunk_bytes < 8_000 or settings.limit_audio_chunk_bytes > 10 * 1024 * 1024:
        raise ValueError("Audio chunk limit must be between 8000 bytes and 10 MiB")
    if settings.websocket_heartbeat_seconds <= 0 or settings.websocket_idle_timeout_seconds <= settings.websocket_heartbeat_seconds:
        raise ValueError("WebSocket idle timeout must exceed the positive heartbeat interval")
    queue_capacities = (
        settings.live_processing_worker_queue_capacity, settings.live_final_queue_capacity,
        settings.live_translation_queue_capacity, settings.live_translation_quality_queue_capacity,
        settings.live_diarization_queue_capacity, settings.live_transcript_postprocess_queue_capacity,
        settings.live_pipeline_persistence_queue_capacity,
    )
    if any(value > settings.limit_queue_depth for value in queue_capacities):
        raise ValueError("Worker queue capacity exceeds the configured production maximum")
    if settings.security_profile not in {"Fast", "Balanced", "Accurate", "Private"}:
        raise ValueError("Unsupported security profile")
    if settings.app_env.lower() != "production":
        return
    import json
    from urllib.parse import urlsplit
    if settings.app_debug:
        raise ValueError("Debug mode must be disabled in production")
    if not settings.security_auth_enabled:
        raise ValueError("Authentication must be enabled in production")
    try:
        tokens = json.loads(settings.security_tokens_json)
    except json.JSONDecodeError as exc:
        raise ValueError("SECURITY_TOKENS_JSON must be valid JSON") from exc
    if not isinstance(tokens, dict) or not tokens or any(
        len(str(token)) < 32 or any(marker in str(token).lower() for marker in ("replace", "change-me", "example", "default"))
        for token in tokens
    ):
        raise ValueError("Production requires non-default bearer tokens of at least 32 characters")
    if len(tokens) > 10_000 or any(
        isinstance(value, dict) and (
            not value.get("userId") or value.get("role", "user") not in {"user", "admin"}
        )
        for value in tokens.values()
    ):
        raise ValueError("Production bearer principal mapping is invalid")
    origins = [value.strip() for value in settings.security_trusted_origins.split(",") if value.strip()]
    if not origins or "*" in settings.web_origin or any("*" in value for value in origins):
        raise ValueError("Wildcard or empty trusted origins are forbidden in production")
    if settings.security_require_https and (
        urlsplit(settings.web_origin).scheme != "https" or any(urlsplit(value).scheme != "https" for value in origins)
    ):
        raise ValueError("HTTPS trusted origins are required in production")
    checkpoint = settings.whisper_model_dir / ({"Fast": "base.pt", "Balanced": "small.pt", "Accurate": "base.pt", "Private": "base.pt"}[settings.security_profile])
    if not checkpoint.is_file():
        raise ValueError(f"Required local checkpoint is unavailable for profile {settings.security_profile}")


@lru_cache
def get_settings() -> Settings:
    return Settings()

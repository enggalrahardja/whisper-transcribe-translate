from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

WhisperModel = Literal["tiny", "base", "small", "medium", "large"]
WhisperModelStatus = Literal[
    "not_downloaded", "downloading", "available", "failed", "corrupted", "deleting"
]


class WhisperModelResponse(BaseModel):
    model: WhisperModel
    status: WhisperModelStatus
    file_name: str
    file_path: str
    expected_size_bytes: int | None
    actual_size_bytes: int | None
    checksum_valid: bool | None
    downloaded_at: datetime | None
    last_verified_at: datetime | None
    last_error: str | None
    downloaded_bytes: int = 0
    progress: float = 0
    download_started_at: datetime | None = None
    download_completed_at: datetime | None = None
    download_heartbeat_at: datetime | None = None
    download_worker_id: str | None = None
    cancel_requested: bool = False
    attempt: int = 0


class AvailableWhisperModelResponse(BaseModel):
    model: WhisperModel
    file_name: str
    file_path: str
    actual_size_bytes: int
    last_verified_at: datetime | None


class GeneralSettings(BaseModel):
    default_language: str = Field(default="auto", min_length=2, max_length=64)
    default_whisper_model: WhisperModel = "base"
    default_task: Literal["transcribe", "translate"] = "transcribe"
    timezone: str = "UTC"
    theme_preference: Literal["system", "light", "dark"] = "system"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class TranscriptionSettings(BaseModel):
    device: Literal["auto", "cpu", "cuda"] = "auto"
    fp16: bool = True
    beam_size: int = Field(default=5, ge=1, le=20)
    temperature: float = Field(default=0.0, ge=0, le=1)
    initial_prompt: str = Field(default="", max_length=4000)
    word_timestamps: bool = False
    maximum_concurrent_transcription_jobs: int = Field(default=1, ge=1, le=8)


class TranslationSettings(BaseModel):
    default_target_language: str = Field(default="english", min_length=2, max_length=64)
    translation_provider: Literal["google"] = "google"
    provider_timeout_seconds: float = Field(default=30, ge=1, le=300)
    max_chunk_length: int = Field(default=4500, ge=100, le=5000)
    retry_count: int = Field(default=2, ge=0, le=10)


class LiveTranscriptionSettings(BaseModel):
    chunk_duration_seconds: float = Field(default=3, ge=2, le=5)
    overlap_duration_seconds: float = Field(default=0.5, ge=0, le=2)
    reconnect_attempts: int = Field(default=5, ge=0, le=20)
    reconnect_delay_seconds: float = Field(default=1.5, ge=0.25, le=30)
    auto_stop_idle_seconds: int = Field(default=300, ge=10, le=86400)
    default_live_model: WhisperModel = "base"

    @model_validator(mode="after")
    def validate_overlap(self) -> "LiveTranscriptionSettings":
        if self.overlap_duration_seconds >= self.chunk_duration_seconds:
            raise ValueError("overlap duration must be shorter than chunk duration")
        return self


class StorageRetentionSettings(BaseModel):
    storage_location: str = Field(default="", max_length=4096)
    previous_storage_locations: list[str] = Field(default_factory=list, max_length=20)
    upload_max_size_mb: int = Field(default=512, ge=1, le=10240)
    allowed_extensions: list[str] = Field(
        default_factory=lambda: [".wav", ".mp3", ".ogg", ".flac", ".m4a", ".mp4", ".mov", ".wmv", ".avi", ".mkv"],
        min_length=1,
        max_length=30,
    )
    media_retention_days: int = Field(default=30, ge=1, le=3650)
    export_retention_days: int = Field(default=14, ge=1, le=3650)
    cleanup_enabled: bool = True

    @field_validator("allowed_extensions")
    @classmethod
    def normalize_extensions(cls, values: list[str]) -> list[str]:
        supported = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".mp4", ".mov", ".wmv", ".avi", ".mkv"}
        normalized: list[str] = []
        for raw in values:
            value = raw.strip().lower()
            if not value.startswith(".") or len(value) < 2 or not value[1:].isalnum():
                raise ValueError(f"Invalid file extension: {raw}")
            if value not in supported:
                raise ValueError(f"Unsupported media extension: {raw}")
            if value not in normalized:
                normalized.append(value)
        return normalized


class WorkerProcessingSettings(BaseModel):
    polling_interval_seconds: float = Field(default=1, ge=0.1, le=60)
    stale_heartbeat_threshold_seconds: int = Field(default=60, ge=10, le=3600)
    retry_delay_seconds: float = Field(default=3, ge=0, le=300)
    worker_enabled: bool = True


class ApplicationSettingsValues(BaseModel):
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    transcription: TranscriptionSettings = Field(default_factory=TranscriptionSettings)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    live_transcription: LiveTranscriptionSettings = Field(default_factory=LiveTranscriptionSettings)
    storage_retention: StorageRetentionSettings = Field(default_factory=StorageRetentionSettings)
    worker_processing: WorkerProcessingSettings = Field(default_factory=WorkerProcessingSettings)


class UpdateApplicationSettingsRequest(ApplicationSettingsValues):
    version: int = Field(ge=1)


class ApplicationSettingsResponse(ApplicationSettingsValues):
    version: int
    updated_at: datetime
    restart_required_fields: list[str]


class StorageUsageSummary(BaseModel):
    total_bytes: int
    uploads_bytes: int
    exports_bytes: int
    other_bytes: int
    file_count: int


class WorkerRuntimeResponse(BaseModel):
    worker_status: Literal["online", "offline", "disabled"]
    worker_id: str | None
    last_heartbeat: datetime | None
    current_job: str | None
    active_workers: int
    queued_jobs: int
    processing_jobs: int
    completed_jobs: int
    failed_jobs: int
    effective_device: str | None
    configured_concurrency: int
    pending_restart: bool
    pending_restart_fields: list[str]
    settings_version: int
    storage_usage: StorageUsageSummary


class CleanupResponse(BaseModel):
    media_files_deleted: int
    export_files_deleted: int
    orphan_files_deleted: int
    bytes_reclaimed: int
    protected_active_files: int
    protected_project_files: int
    errors: list[str]


class LocalFileResponse(BaseModel):
    id: str
    original_name: str
    media_type: str
    content_type: str | None
    file_size: int
    created_at: datetime
    job_count: int
    active_job_count: int
    subtitle_project_count: int
    deletable: bool
    protection_reason: str | None


class DeleteLocalFileResponse(BaseModel):
    id: str
    original_name: str
    bytes_deleted: int

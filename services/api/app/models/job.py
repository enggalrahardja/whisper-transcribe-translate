from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class VadSettings(BaseModel):
    minimum_silence_ms: int = Field(default=600, ge=100, le=10_000)
    maximum_segment_duration_seconds: float = Field(default=30, ge=5, le=300)
    speech_padding_ms: int = Field(default=300, ge=0, le=5_000)


class AccurateFinalSettings(BaseModel):
    beam_size: int = Field(default=5, ge=1, le=20)
    best_of: int = Field(default=5, ge=1, le=20)
    temperature: float = Field(default=0, ge=0, le=1)
    word_timestamps: bool = False


class AdvancedTranscriptionSettings(BaseModel):
    processing_mode: str = Field(default="standard", pattern="^(standard|interview|lecture|clean)$")
    force_language: bool = False
    use_vad: bool = True
    vad: VadSettings = Field(default_factory=VadSettings)
    use_previous_segment_context: bool = True
    apply_glossary: bool = False
    glossary_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9._-]+$")
    accurate_final: bool = False
    accurate: AccurateFinalSettings = Field(default_factory=AccurateFinalSettings)
    speaker_diarization: bool = False
    transcript_style: str = Field(default="verbatim", pattern="^(verbatim|verbatim_normalized|clean)$")
    low_confidence_handling: str = Field(default="keep", pattern="^(keep|mark|replace)$")

    @model_validator(mode="after")
    def require_selected_glossary(self) -> "AdvancedTranscriptionSettings":
        if self.apply_glossary and not self.glossary_id:
            raise ValueError("glossary_id is required when apply_glossary is enabled")
        if not self.apply_glossary:
            self.glossary_id = None
        return self


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CreateJobRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="audio", pattern="^(audio|video)$")
    language: str = Field(default="auto", min_length=2, max_length=50)
    model: str = Field(default="base", pattern="^(tiny|base|small|medium|large)$")
    task: str = Field(default="transcribe", pattern="^(transcribe|translate)$")
    target_language: str | None = Field(default=None, min_length=2, max_length=50)
    transcription_config: AdvancedTranscriptionSettings | None = None

    @model_validator(mode="after")
    def require_translate_target(self) -> "CreateJobRequest":
        if self.task == "translate" and not self.target_language:
            raise ValueError("target_language is required for translate jobs")
        return self


class JobResponse(BaseModel):
    id: str
    file_name: str
    media_type: str
    language: str
    model: str
    task: str
    target_language: str | None = None
    transcription_config: AdvancedTranscriptionSettings | None = None
    status: JobStatus
    progress: int
    progress_stage: str | None = None
    progress_message: str | None = None
    file_size: int | None = None
    content_type: str | None = None
    error: str | None = None
    model_load_metadata: dict[str, object] | None = None
    cancellation_requested: bool = False
    worker_id: str | None = None
    transcript_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TranscriptResponse(BaseModel):
    id: str
    job_id: str
    media_file_id: str
    text: str
    language: str
    segments: list[dict]
    original_text: str | None = None
    translated_text: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    original_segments: list[dict] | None = None
    translated_segments: list[dict] | None = None
    created_at: datetime

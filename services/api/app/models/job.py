from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


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
    status: JobStatus
    progress: int
    file_size: int | None = None
    content_type: str | None = None
    error: str | None = None
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

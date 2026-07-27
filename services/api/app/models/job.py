from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
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


class JobResponse(BaseModel):
    id: str
    file_name: str
    media_type: str
    language: str
    model: str
    task: str
    status: JobStatus
    progress: int
    error: str | None = None
    created_at: datetime
    updated_at: datetime

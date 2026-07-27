from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SubtitleSourceType = Literal["transcription", "translation_original", "translation_translated"]
SubtitleBurnStatus = Literal["queued", "processing", "completed", "failed"]


class SubtitleSegment(BaseModel):
    sequence: int = Field(ge=1)
    start: float = Field(ge=0)
    end: float
    text: str = Field(max_length=10000)
    duration: float = 0

    @model_validator(mode="after")
    def validate_timing(self) -> "SubtitleSegment":
        if self.end <= self.start:
            raise ValueError("Segment end must be greater than start")
        self.duration = round(self.end - self.start, 3)
        return self


class CreateSubtitleProjectRequest(BaseModel):
    job_id: str
    source_type: SubtitleSourceType = "transcription"


class UpdateSubtitleProjectRequest(BaseModel):
    version: int = Field(ge=1)
    segments: list[SubtitleSegment]


class SubtitleProjectResponse(BaseModel):
    project_id: str
    job_id: str
    media_file_id: str
    source_type: SubtitleSourceType
    language: str
    segments: list[SubtitleSegment]
    version: int
    file_name: str
    media_type: str
    content_type: str | None = None
    created_at: datetime
    updated_at: datetime


class SubtitleBurnResponse(BaseModel):
    burn_id: str
    project_id: str
    status: SubtitleBurnStatus
    output_file_name: str | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime

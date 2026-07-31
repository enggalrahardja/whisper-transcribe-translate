from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from ..services.transcription_languages import (
    InvalidTranscriptionLanguage,
    normalize_transcription_language,
)


LiveSessionStatus = Literal["active", "paused", "completed", "failed"]


class CreateLiveSessionRequest(BaseModel):
    language: str = Field(default="auto", min_length=2, max_length=50)
    model: str = Field(default="base", pattern="^(tiny|base|small|medium|large|large-v3|turbo)$")
    transcription_backend: str = Field(default="pytorch", pattern="^(pytorch|faster-whisper)$")
    transcription_device: str = Field(default="auto", pattern="^(auto|cpu|cuda)$")
    transcription_compute_type: str = Field(
        default="auto", pattern="^(auto|float16|float32|int8_float16|int8)$"
    )

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: object) -> str:
        try:
            return normalize_transcription_language(value) or "auto"
        except InvalidTranscriptionLanguage as exc:
            raise PydanticCustomError(
                exc.code,
                "Unsupported transcription language: {value}",
                {"value": value},
            ) from exc


class LiveSessionResponse(BaseModel):
    session_id: str
    status: LiveSessionStatus
    language: str
    model: str
    transcription_backend: str = "pytorch"
    transcription_device: str = "auto"
    transcription_compute_type: str = "auto"
    started_at: datetime
    ended_at: datetime | None = None
    duration: float
    partial_text: str
    final_text: str
    segments: list[dict]
    error: str | None = None
    created_at: datetime
    updated_at: datetime

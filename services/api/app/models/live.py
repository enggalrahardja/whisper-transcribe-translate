from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


LiveSessionStatus = Literal["active", "paused", "completed", "failed"]


class CreateLiveSessionRequest(BaseModel):
    language: str = Field(default="auto", min_length=2, max_length=50)
    model: str = Field(default="base", pattern="^(tiny|base|small|medium|large)$")


class LiveSessionResponse(BaseModel):
    session_id: str
    status: LiveSessionStatus
    language: str
    model: str
    started_at: datetime
    ended_at: datetime | None = None
    duration: float
    partial_text: str
    final_text: str
    segments: list[dict]
    error: str | None = None
    created_at: datetime
    updated_at: datetime

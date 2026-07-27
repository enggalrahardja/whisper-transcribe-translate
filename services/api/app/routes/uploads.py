from fastapi import APIRouter, File, Form, UploadFile, status

from ..models.job import JobResponse
from ..services.jobs import create_uploaded_job
from ..services.storage import save_upload

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    language: str = Form(default="auto"),
    model: str = Form(default="base", pattern="^(tiny|base|small|medium|large)$"),
    task: str = Form(default="transcribe", pattern="^(transcribe|translate)$"),
) -> JobResponse:
    media = await save_upload(file)
    return create_uploaded_job(media, language=language, model=model, task=task)

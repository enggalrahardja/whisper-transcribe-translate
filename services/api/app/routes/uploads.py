from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from ..models.job import JobResponse
from ..services.jobs import create_uploaded_job
from ..services.media_files import create_media_file
from ..services.storage import save_upload
from ..services.translation_adapter import normalize_target_language

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    language: str = Form(default="auto"),
    model: str = Form(default="base", pattern="^(tiny|base|small|medium|large)$"),
    task: str = Form(default="transcribe", pattern="^(transcribe|translate)$"),
    target_language: str | None = Form(default=None),
) -> JobResponse:
    if task == "translate":
        try:
            target_language = normalize_target_language(target_language)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    media = await save_upload(file)
    media_file = create_media_file(media)
    return create_uploaded_job(
        media,
        media_file_id=media_file["_id"],
        language=language,
        model=model,
        task=task,
        target_language=target_language,
    )

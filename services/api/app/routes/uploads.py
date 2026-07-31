from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import ValidationError

from ..models.job import AdvancedTranscriptionSettings, JobResponse
from ..services.jobs import create_uploaded_job, resolve_job_backend_config, transcription_model_reservation
from ..services.media_files import create_media_file
from ..services.storage import save_upload
from ..services.translation_adapter import normalize_target_language

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    language: str = Form(default="auto"),
    model: str = Form(default="base", pattern="^(tiny|base|small|medium|large|large-v3)$"),
    transcription_backend: str | None = Form(default=None, pattern="^(pytorch|faster-whisper)$"),
    transcription_device: str | None = Form(default=None, pattern="^(auto|cpu|cuda)$"),
    transcription_compute_type: str | None = Form(
        default=None, pattern="^(auto|float16|float32|int8_float16|int8)$"
    ),
    task: str = Form(default="transcribe", pattern="^(transcribe|translate)$"),
    target_language: str | None = Form(default=None),
    transcription_config: str | None = Form(default=None),
) -> JobResponse:
    backend_config = resolve_job_backend_config(
        model, transcription_backend, transcription_device, transcription_compute_type
    )
    with transcription_model_reservation(backend_config, "upload-request"):
        if task == "translate":
            try:
                target_language = normalize_target_language(target_language)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        parsed_config: AdvancedTranscriptionSettings | None = None
        if transcription_config is not None:
            try:
                parsed_config = AdvancedTranscriptionSettings.model_validate_json(transcription_config)
            except ValidationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid advanced transcription settings",
                ) from exc
        media = await save_upload(file)
        media_file = create_media_file(media)
        return create_uploaded_job(
            media,
            media_file_id=media_file["_id"],
            language=language,
            model=model,
            task=task,
            target_language=target_language,
            availability_reserved=True,
            transcription_config=parsed_config.model_dump() if parsed_config else None,
            transcription_backend=transcription_backend,
            transcription_device=transcription_device,
            transcription_compute_type=transcription_compute_type,
        )

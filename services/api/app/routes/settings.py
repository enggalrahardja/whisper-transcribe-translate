import asyncio

from fastapi import APIRouter, HTTPException, status

from ..models.settings import (
    ApplicationSettingsResponse,
    AvailableWhisperModelResponse,
    CleanupResponse,
    UpdateApplicationSettingsRequest,
    WhisperModel,
    WhisperModelResponse,
    WorkerRuntimeResponse,
)
from ..services.application_settings import (
    get_application_settings,
    get_runtime_status,
    run_retention_cleanup,
    update_application_settings,
)
from ..services.whisper_models import (
    WhisperModelActionConflict,
    cancel_whisper_model_download,
    delete_whisper_model,
    list_available_whisper_models,
    list_whisper_models,
    request_whisper_model_download,
    scan_whisper_models,
    verify_whisper_model,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=ApplicationSettingsResponse)
def get_settings() -> ApplicationSettingsResponse:
    return get_application_settings()


@router.patch("", response_model=ApplicationSettingsResponse)
def patch_settings(payload: UpdateApplicationSettingsRequest) -> ApplicationSettingsResponse:
    return update_application_settings(payload)


@router.get("/runtime", response_model=WorkerRuntimeResponse)
def get_runtime() -> WorkerRuntimeResponse:
    return get_runtime_status()


@router.post("/cleanup", response_model=CleanupResponse)
def cleanup() -> CleanupResponse:
    return run_retention_cleanup()


@router.get("/models", response_model=list[WhisperModelResponse])
def get_models() -> list[WhisperModelResponse]:
    return list_whisper_models()


@router.get("/models/available", response_model=list[AvailableWhisperModelResponse])
def get_available_models() -> list[AvailableWhisperModelResponse]:
    return list_available_whisper_models()


@router.post("/models/scan", response_model=list[WhisperModelResponse])
async def scan_models() -> list[WhisperModelResponse]:
    return await asyncio.to_thread(scan_whisper_models)


@router.post("/models/{model}/verify", response_model=WhisperModelResponse)
async def verify_model(model: WhisperModel) -> WhisperModelResponse:
    try:
        return await asyncio.to_thread(verify_whisper_model, model)
    except WhisperModelActionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/models/{model}", response_model=WhisperModelResponse)
async def delete_model(model: WhisperModel) -> WhisperModelResponse:
    try:
        return await asyncio.to_thread(delete_whisper_model, model)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except WhisperModelActionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Whisper model file could not be deleted: {exc}",
        ) from exc


@router.post(
    "/models/{model}/download",
    response_model=WhisperModelResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def download_model(model: WhisperModel) -> WhisperModelResponse:
    try:
        return await asyncio.to_thread(request_whisper_model_download, model)
    except WhisperModelActionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/models/{model}/cancel",
    response_model=WhisperModelResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_model_download(model: WhisperModel) -> WhisperModelResponse:
    try:
        return await asyncio.to_thread(cancel_whisper_model_download, model)
    except WhisperModelActionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/models/{model}/retry",
    response_model=WhisperModelResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_model_download(model: WhisperModel) -> WhisperModelResponse:
    try:
        return await asyncio.to_thread(
            request_whisper_model_download, model, retry=True
        )
    except WhisperModelActionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

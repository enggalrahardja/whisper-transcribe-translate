import asyncio

from fastapi import APIRouter, Depends, HTTPException, status

from ..models.settings import (
    ApplicationSettingsResponse,
    AvailableWhisperModelResponse,
    CleanupResponse,
    DeleteLocalFileResponse,
    LocalFileResponse,
    TranscriptionCapabilitiesResponse,
    UpdateApplicationSettingsRequest,
    WhisperModel,
    ModelRegistryBackend,
    WhisperModelActionRequest,
    WhisperModelScanRequest,
    WhisperModelResponse,
    WorkerRuntimeResponse,
)
from ..services.application_settings import (
    get_application_settings,
    get_runtime_status,
    run_retention_cleanup,
    update_application_settings,
)
from ..services.media_files import delete_local_file, list_local_files
from ..services.transcription_backends import runtime_capabilities
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
from ..security import Principal, require_admin, require_principal
from ..services.production_hardening import audit_event


def _admin_settings_access(principal: Principal = Depends(require_principal)) -> Principal:
    require_admin(principal)
    return principal

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(_admin_settings_access)])


@router.get("", response_model=ApplicationSettingsResponse)
def get_settings() -> ApplicationSettingsResponse:
    return get_application_settings()


@router.patch("", response_model=ApplicationSettingsResponse)
def patch_settings(
    payload: UpdateApplicationSettingsRequest,
    principal: Principal = Depends(require_principal),
) -> ApplicationSettingsResponse:
    require_admin(principal)
    before = get_application_settings()
    result = update_application_settings(payload)
    if (
        before.general.default_whisper_model != result.general.default_whisper_model
        or before.live_transcription.default_live_model != result.live_transcription.default_live_model
    ):
        audit_event(
            "profile_change", principal=principal,
            metadata={"defaultModel": result.general.default_whisper_model, "liveModel": result.live_transcription.default_live_model},
        )
    return result


@router.get("/runtime", response_model=WorkerRuntimeResponse)
def get_runtime() -> WorkerRuntimeResponse:
    return get_runtime_status()


@router.post("/cleanup", response_model=CleanupResponse)
def cleanup() -> CleanupResponse:
    return run_retention_cleanup()


@router.get("/transcription-capabilities", response_model=TranscriptionCapabilitiesResponse)
def get_transcription_capabilities() -> TranscriptionCapabilitiesResponse:
    return TranscriptionCapabilitiesResponse.model_validate(runtime_capabilities())


@router.get("/local-files", response_model=list[LocalFileResponse])
def get_local_files() -> list[LocalFileResponse]:
    return list_local_files()


@router.delete("/local-files/{media_file_id}", response_model=DeleteLocalFileResponse)
def remove_local_file(
    media_file_id: str,
    principal: Principal = Depends(require_principal),
) -> DeleteLocalFileResponse:
    require_admin(principal)
    result = delete_local_file(media_file_id)
    audit_event(
        "local_file_delete",
        principal=principal,
        metadata={"mediaFileId": result.id, "fileName": result.original_name, "bytesDeleted": result.bytes_deleted},
    )
    return result


@router.get("/models", response_model=list[WhisperModelResponse])
def get_models(backend: ModelRegistryBackend = "pytorch") -> list[WhisperModelResponse]:
    return list_whisper_models(backend)


@router.get("/models/available", response_model=list[AvailableWhisperModelResponse])
def get_available_models(
    backend: ModelRegistryBackend = "pytorch",
) -> list[AvailableWhisperModelResponse]:
    return list_available_whisper_models(backend)


@router.post("/models/scan", response_model=list[WhisperModelResponse])
async def scan_models(
    payload: WhisperModelScanRequest | None = None,
) -> list[WhisperModelResponse]:
    return await asyncio.to_thread(
        scan_whisper_models, payload.backend if payload else "pytorch"
    )


@router.post("/models/verify", response_model=WhisperModelResponse)
async def verify_backend_model(payload: WhisperModelActionRequest) -> WhisperModelResponse:
    try:
        return await asyncio.to_thread(
            verify_whisper_model, payload.model, backend=payload.backend
        )
    except WhisperModelActionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/models", response_model=WhisperModelResponse)
async def delete_backend_model(payload: WhisperModelActionRequest) -> WhisperModelResponse:
    return await _delete_model(payload.model, payload.backend)


@router.post(
    "/models/download", response_model=WhisperModelResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def download_backend_model(payload: WhisperModelActionRequest) -> WhisperModelResponse:
    return await _request_download(payload, retry=False)


@router.post(
    "/models/cancel", response_model=WhisperModelResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_backend_model(payload: WhisperModelActionRequest) -> WhisperModelResponse:
    try:
        return await asyncio.to_thread(
            cancel_whisper_model_download, payload.model, payload.backend
        )
    except WhisperModelActionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/models/retry", response_model=WhisperModelResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_backend_model(payload: WhisperModelActionRequest) -> WhisperModelResponse:
    return await _request_download(payload, retry=True)


@router.post("/models/{model}/verify", response_model=WhisperModelResponse)
async def verify_model(model: WhisperModel) -> WhisperModelResponse:
    try:
        return await asyncio.to_thread(verify_whisper_model, model)
    except WhisperModelActionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/models/{model}", response_model=WhisperModelResponse)
async def delete_model(model: WhisperModel) -> WhisperModelResponse:
    return await _delete_model(model, "pytorch")


async def _delete_model(model: WhisperModel, backend: ModelRegistryBackend) -> WhisperModelResponse:
    try:
        return await asyncio.to_thread(delete_whisper_model, model, backend)
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
            detail="Whisper model file could not be deleted",
        ) from exc


@router.post(
    "/models/{model}/download",
    response_model=WhisperModelResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def download_model(model: WhisperModel) -> WhisperModelResponse:
    return await _request_download(
        WhisperModelActionRequest(backend="pytorch", model=model), retry=False
    )


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
    return await _request_download(
        WhisperModelActionRequest(backend="pytorch", model=model), retry=True
    )


async def _request_download(
    payload: WhisperModelActionRequest, *, retry: bool
) -> WhisperModelResponse:
    try:
        return await asyncio.to_thread(
            request_whisper_model_download,
            payload.model,
            backend=payload.backend,
            retry=retry,
        )
    except WhisperModelActionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

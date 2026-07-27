from fastapi import APIRouter

from ..models.settings import (
    ApplicationSettingsResponse,
    CleanupResponse,
    UpdateApplicationSettingsRequest,
    WorkerRuntimeResponse,
)
from ..services.application_settings import (
    get_application_settings,
    get_runtime_status,
    run_retention_cleanup,
    update_application_settings,
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

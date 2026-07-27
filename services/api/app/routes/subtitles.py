from fastapi import APIRouter, BackgroundTasks, Query, Response, status
from fastapi.responses import FileResponse

from ..models.subtitle import (
    CreateSubtitleProjectRequest,
    SubtitleBurnResponse,
    SubtitleProjectResponse,
    UpdateSubtitleProjectRequest,
)
from ..services.subtitle_burns import (
    create_subtitle_burn,
    get_burn_output,
    get_subtitle_burn,
    list_subtitle_burns,
    process_subtitle_burn,
)
from ..services.subtitle_projects import (
    create_subtitle_project,
    delete_subtitle_project,
    get_project_media,
    get_subtitle_document,
    get_subtitle_project,
    list_subtitle_projects,
    render_subtitle,
    safe_export_name,
    update_subtitle_project,
)

router = APIRouter(prefix="/api/subtitles", tags=["subtitles"])


@router.get("/burns", response_model=list[SubtitleBurnResponse])
def get_burns(limit: int = Query(default=100, ge=1, le=100)) -> list[SubtitleBurnResponse]:
    return list_subtitle_burns(limit)


@router.get("/burns/{burn_id}", response_model=SubtitleBurnResponse)
def get_burn(burn_id: str) -> SubtitleBurnResponse:
    return get_subtitle_burn(burn_id)


@router.get("/burns/{burn_id}/download")
def download_burn(burn_id: str) -> FileResponse:
    burn, output_path = get_burn_output(burn_id)
    return FileResponse(output_path, media_type="video/mp4", filename=burn["output_file_name"])


@router.post("/projects", response_model=SubtitleProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(payload: CreateSubtitleProjectRequest) -> SubtitleProjectResponse:
    return create_subtitle_project(payload)


@router.get("/projects", response_model=list[SubtitleProjectResponse])
def get_projects(limit: int = Query(default=100, ge=1, le=100)) -> list[SubtitleProjectResponse]:
    return list_subtitle_projects(limit)


@router.get("/projects/{project_id}", response_model=SubtitleProjectResponse)
def get_project(project_id: str) -> SubtitleProjectResponse:
    return get_subtitle_project(project_id)


@router.patch("/projects/{project_id}", response_model=SubtitleProjectResponse)
def update_project(project_id: str, payload: UpdateSubtitleProjectRequest) -> SubtitleProjectResponse:
    return update_subtitle_project(project_id, payload)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str) -> Response:
    delete_subtitle_project(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/export")
def export_project(project_id: str, format: str = Query(pattern="^(srt|vtt|txt)$")) -> Response:
    document = get_subtitle_document(project_id)
    content_types = {
        "srt": "application/x-subrip; charset=utf-8",
        "vtt": "text/vtt; charset=utf-8",
        "txt": "text/plain; charset=utf-8",
    }
    file_name = safe_export_name(document["file_name"], project_id, format)
    return Response(
        render_subtitle(document, format),
        media_type=content_types[format],
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.get("/projects/{project_id}/media")
def preview_project_media(project_id: str) -> FileResponse:
    media, path = get_project_media(project_id)
    return FileResponse(
        path,
        media_type=str(media.get("content_type") or "application/octet-stream"),
        filename=str(media.get("original_name") or path.name),
        content_disposition_type="inline",
    )


@router.post("/projects/{project_id}/burn", response_model=SubtitleBurnResponse, status_code=status.HTTP_202_ACCEPTED)
def burn_project(project_id: str, background_tasks: BackgroundTasks) -> SubtitleBurnResponse:
    burn = create_subtitle_burn(project_id)
    background_tasks.add_task(process_subtitle_burn, burn.burn_id)
    return burn

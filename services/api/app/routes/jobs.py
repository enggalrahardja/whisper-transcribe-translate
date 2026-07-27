from fastapi import APIRouter, Query, Response, status

from ..models.job import CreateJobRequest, JobResponse, TranscriptResponse
from ..services.jobs import cancel_job, create_job, delete_job, get_job, get_job_summary, list_jobs, retry_job
from ..services.transcripts import get_job_result

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_transcription_job(payload: CreateJobRequest) -> JobResponse:
    return create_job(payload)


@router.get("", response_model=list[JobResponse])
def get_transcription_jobs(
    limit: int = Query(default=20, ge=1, le=100),
) -> list[JobResponse]:
    return list_jobs(limit)


@router.get("/summary")
def get_transcription_job_summary() -> dict[str, int]:
    return get_job_summary()


@router.get("/{job_id}", response_model=JobResponse)
def get_transcription_job(job_id: str) -> JobResponse:
    return get_job(job_id)


@router.get("/{job_id}/result", response_model=TranscriptResponse)
def get_transcription_job_result(job_id: str) -> TranscriptResponse:
    return get_job_result(job_id)


@router.post("/{job_id}/retry", response_model=JobResponse)
def retry_transcription_job(job_id: str) -> JobResponse:
    return retry_job(job_id)


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_transcription_job(job_id: str) -> JobResponse:
    return cancel_job(job_id)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transcription_job(job_id: str) -> Response:
    delete_job(job_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

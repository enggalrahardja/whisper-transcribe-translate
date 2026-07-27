from fastapi import APIRouter, Query, status

from ..models.job import CreateJobRequest, JobResponse
from ..services.jobs import create_job, get_job, get_job_summary, list_jobs

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

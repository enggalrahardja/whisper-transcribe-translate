from fastapi import APIRouter

from ..services.job_glossaries import list_job_glossaries

router = APIRouter(prefix="/api/glossaries", tags=["glossaries"])


@router.get("")
def get_available_glossaries() -> list[dict[str, str]]:
    return list_job_glossaries()

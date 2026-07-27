from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import PyMongoError

from .config import get_settings
from .database import close_database, get_database
from .routes.jobs import router as jobs_router
from .routes.live import router as live_router
from .routes.settings import router as settings_router
from .routes.subtitles import router as subtitles_router
from .routes.uploads import router as uploads_router
from .security import allowed_web_origins
from .services.jobs import ensure_job_indexes
from .services.application_settings import ensure_application_settings
from .services.live_sessions import ensure_live_session_indexes
from .services.media_files import ensure_media_file_indexes
from .services.subtitle_burns import ensure_subtitle_burn_indexes, recover_interrupted_subtitle_burns
from .services.subtitle_projects import ensure_subtitle_project_indexes
from .services.transcripts import ensure_transcript_indexes


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_application_settings()
    ensure_job_indexes()
    ensure_live_session_indexes()
    ensure_media_file_indexes()
    ensure_subtitle_project_indexes()
    ensure_subtitle_burn_indexes()
    recover_interrupted_subtitle_burns()
    ensure_transcript_indexes()
    yield
    close_database()


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_web_origins()),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.include_router(jobs_router)
app.include_router(live_router)
app.include_router(settings_router)
app.include_router(subtitles_router)
app.include_router(uploads_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "api", "environment": settings.app_env}


@app.get("/health/mongodb")
def mongodb_health() -> dict[str, str]:
    try:
        get_database().command("ping")
        return {"status": "ok", "database": settings.mongodb_database}
    except PyMongoError as exc:
        return {"status": "error", "message": str(exc)}

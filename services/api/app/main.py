from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
from .services.application_settings import RUNTIME_COLLECTION, ensure_application_settings, get_application_settings
from .services.live_sessions import ensure_live_session_indexes
from .services.media_files import ensure_media_file_indexes
from .services.subtitle_burns import ensure_subtitle_burn_indexes, recover_interrupted_subtitle_burns
from .services.subtitle_projects import ensure_subtitle_project_indexes
from .services.transcripts import ensure_transcript_indexes
from .services.whisper_models import WhisperModelUnavailableError, ensure_whisper_model_registry


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        # Make an explicit server selection before running any startup work. This
        # prevents Uvicorn from appearing healthy while MongoDB is unavailable.
        get_database().command("ping")
        ensure_application_settings()
        ensure_job_indexes()
        ensure_live_session_indexes()
        ensure_media_file_indexes()
        ensure_subtitle_project_indexes()
        ensure_subtitle_burn_indexes()
        recover_interrupted_subtitle_burns()
        ensure_transcript_indexes()
        ensure_whisper_model_registry()
        yield
    finally:
        close_database()


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.exception_handler(WhisperModelUnavailableError)
async def unavailable_whisper_model_handler(
    _: Request, exc: WhisperModelUnavailableError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc)},
    )


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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "message": str(exc)},
        ) from exc


@app.get("/health/worker")
def worker_health() -> dict[str, str]:
    try:
        stale_after = max(
            10,
            get_application_settings().worker_processing.stale_heartbeat_threshold_seconds,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after)
        runtime = get_database()[RUNTIME_COLLECTION].find_one(
            {"status": "online", "last_heartbeat": {"$gte": cutoff}},
            sort=[("last_heartbeat", -1)],
        )
    except PyMongoError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "message": str(exc)},
        ) from exc

    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "message": "No fresh worker heartbeat found"},
        )

    heartbeat = runtime["last_heartbeat"]
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    return {
        "status": "ok",
        "worker_id": str(runtime["worker_id"]),
        "last_heartbeat": heartbeat.isoformat(),
    }

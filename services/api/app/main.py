from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from .config import get_settings
from .database import close_database, get_database
from .routes.jobs import router as jobs_router
from .routes.glossaries import router as glossaries_router
from .routes.live import (
    router as live_router,
    shutdown_final_transcription_queue,
    shutdown_live_translation_queue,
    shutdown_translation_quality_queue,
    shutdown_speaker_diarization_queue,
    shutdown_transcript_postprocess_queue,
    shutdown_processing_workers,
    startup_processing_workers,
    production_pipeline_readiness,
)
from .routes.settings import router as settings_router
from .routes.subtitles import router as subtitles_router
from .routes.uploads import router as uploads_router
from .security import Principal, allowed_web_origins, require_admin, require_principal, safe_error
from .services.production_hardening import audit_event, cleanup_retention, dependency_readiness, ensure_production_hardening_indexes
from .config import validate_startup_configuration
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
        validate_startup_configuration(get_settings())
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
        ensure_production_hardening_indexes()
        await startup_processing_workers()
        yield
    finally:
        await shutdown_processing_workers()
        await shutdown_final_transcription_queue()
        await shutdown_live_translation_queue()
        await shutdown_translation_quality_queue()
        await shutdown_speaker_diarization_queue()
        await shutdown_transcript_postprocess_queue()
        close_database()


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)


if settings.app_env.lower() == "production":
    @app.exception_handler(Exception)
    async def sanitized_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": safe_error(exc)})


@app.middleware("http")
async def production_security_headers(request: Request, call_next):
    if settings.security_require_https and request.url.scheme != "https":
        return JSONResponse(status_code=400, content={"detail": "HTTPS is required"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), payment=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    if settings.security_require_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


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
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(jobs_router)
app.include_router(glossaries_router)
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
        return {"status": "ok", "dependency": "mongodb"}
    except PyMongoError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "message": "Database unavailable"},
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
            detail={"status": "error", "message": "Worker status unavailable"},
        ) from exc

    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "message": "No fresh worker heartbeat found"},
        )

    return {"status": "ok", "dependency": "worker", "heartbeat_fresh": True}


@app.get("/health/readiness")
def readiness() -> JSONResponse:
    worker, queue, persistence = production_pipeline_readiness()
    result = dependency_readiness(
        worker_check=lambda: worker,
        queue_check=lambda: queue,
        persistence_check=lambda: persistence,
    )
    return JSONResponse(status_code=200 if result["status"] == "ready" else 503, content=result)


@app.post("/api/operations/retention/cleanup")
def retention_cleanup(
    dry_run: bool = Query(default=True),
    principal: Principal = Depends(require_principal),
) -> dict[str, object]:
    require_admin(principal)
    result = cleanup_retention(dry_run=dry_run)
    audit_event("retention_cleanup", principal=principal, metadata={"dryRun": dry_run, "eligible": result.eligible, "deleted": result.deleted})
    return {
        "dryRun": result.dry_run, "scanned": result.scanned,
        "eligible": result.eligible, "deleted": result.deleted,
        "limited": result.limited, "errors": list(result.errors),
    }

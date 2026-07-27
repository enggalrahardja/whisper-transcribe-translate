from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import PyMongoError

from .config import get_settings
from .database import close_database, get_database
from .routes.jobs import router as jobs_router
from .services.jobs import ensure_job_indexes


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_job_indexes()
    yield
    close_database()


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(jobs_router)


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

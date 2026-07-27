from contextlib import asynccontextmanager

from fastapi import FastAPI
from pymongo.errors import PyMongoError

from .config import get_settings
from .database import close_database, get_database


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    close_database()


settings = get_settings()
app = FastAPI(title=settings.app_name, lifespan=lifespan)


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

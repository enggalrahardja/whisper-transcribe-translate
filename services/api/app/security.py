from urllib.parse import urlsplit

from .config import get_settings


def allowed_web_origins() -> set[str]:
    settings = get_settings()
    origins = {settings.web_origin.rstrip("/")}
    if settings.app_env.lower() == "development":
        origins.update({"http://localhost:3000", "http://127.0.0.1:3000"})
    return origins


def is_allowed_web_origin(origin: str | None) -> bool:
    if not origin:
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return False
    normalized = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return normalized in allowed_web_origins()

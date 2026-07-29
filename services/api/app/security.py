"""Authentication, authorization, origin, throttling, and safe-error helpers."""

from __future__ import annotations

import hmac
import json
import re
from collections import deque
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Callable
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request, WebSocket, status

from .config import get_settings

_SENSITIVE = re.compile(r"(authorization|bearer\s+\S+|token|secret|password|api[_-]?key|credential|[A-Za-z]:[\\/]|/(?:home|Users|var|opt)/)", re.I)


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def allowed_web_origins() -> set[str]:
    settings = get_settings()
    origins = {item.strip().rstrip("/") for item in settings.security_trusted_origins.split(",") if item.strip()}
    origins.add(settings.web_origin.rstrip("/"))
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
    if parsed.scheme not in {"http", "https"} or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return False
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/") in allowed_web_origins()


def _token_principals() -> dict[str, Principal]:
    try:
        raw = json.loads(get_settings().security_tokens_json)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    output: dict[str, Principal] = {}
    for token, value in raw.items():
        if isinstance(value, str):
            output[str(token)] = Principal(value)
        elif isinstance(value, dict) and value.get("userId"):
            output[str(token)] = Principal(str(value["userId"]), str(value.get("role", "user")))
    return output


def authenticate_token(token: str | None) -> Principal:
    settings = get_settings()
    if not settings.security_auth_enabled:
        return Principal("development-user", "admin")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    for expected, principal in _token_principals().items():
        if hmac.compare_digest(token, expected):
            return principal
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")


def _bearer(value: str | None) -> str | None:
    if not value or not value.startswith("Bearer "):
        return None
    return value[7:].strip() or None


def require_principal(authorization: str | None = Header(default=None)) -> Principal:
    try:
        return authenticate_token(_bearer(authorization))
    except HTTPException:
        try:
            from .services.production_hardening import audit_event
            audit_event("auth_failure", outcome="rejected")
        except Exception:
            pass
        raise


def require_admin(principal: Principal) -> None:
    if not principal.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")


def websocket_principal(websocket: WebSocket) -> Principal:
    token = _bearer(websocket.headers.get("authorization"))
    if token is None:
        protocols = [item.strip() for item in websocket.headers.get("sec-websocket-protocol", "").split(",") if item.strip()]
        if len(protocols) >= 2 and protocols[0].lower() == "bearer":
            token = protocols[1]
    if token is None:
        token = websocket.query_params.get("access_token")
    return authenticate_token(token)


def authorize_owner(principal: Principal, owner_id: str | None) -> None:
    if principal.is_admin or owner_id == principal.user_id:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session access denied")


def safe_error(_: BaseException | str) -> str:
    """Return a stable public error without local path/configuration disclosure."""
    return "Request could not be processed safely"


def redact_value(value: object) -> object:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _SENSITIVE.search(str(key)) else redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    if isinstance(value, str) and _SENSITIVE.search(value):
        return "[REDACTED]"
    return value


class SlidingWindowLimiter:
    def __init__(self, clock: Callable[[], float] = monotonic, *, max_identities: int = 10_000) -> None:
        self._clock = clock
        self._max_identities = max_identities
        self._events: dict[tuple[str, str], deque[tuple[float, int]]] = {}
        self._lock = RLock()

    def allow(self, category: str, key: str, limit: int, *, window_seconds: float = 60.0, cost: int = 1) -> bool:
        now = self._clock()
        identity = (category, key)
        with self._lock:
            if identity not in self._events and len(self._events) >= self._max_identities:
                self._events.pop(next(iter(self._events)))
            events = self._events.setdefault(identity, deque())
            while events and events[0][0] <= now - window_seconds:
                events.popleft()
            if sum(item[1] for item in events) + cost > limit:
                return False
            events.append((now, cost))
            return True

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


rate_limiter = SlidingWindowLimiter()


def rate_limit_or_raise(category: str, key: str, limit: int, *, window_seconds: float = 60.0, cost: int = 1) -> None:
    if not rate_limiter.allow(category, key, limit, window_seconds=window_seconds, cost=cost):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")


def enforce_concurrent_limit(active: int, limit: int) -> None:
    if active >= limit:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Concurrent session limit reached")


def validate_audio_frame_size(size: int, limit: int) -> None:
    if size <= 0:
        raise ValueError("Audio frame is empty")
    if size > limit:
        raise ValueError("Audio frame exceeds the configured limit")


def websocket_idle_expired(last_activity: float, now: float, idle_timeout_seconds: float) -> bool:
    return now - last_activity >= idle_timeout_seconds

"""App-level remediation knobs (per replica; admin calls fan out or are idempotent).

Scale/restart are NOT here — the agent performs those via ``docker compose``.
"""
from __future__ import annotations

import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_EXEMPT_PREFIXES = ("/healthz", "/metrics", "/admin")


def _exempt(path: str) -> bool:
    return any(path.startswith(p) for p in _EXEMPT_PREFIXES)


class TokenBucket:
    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._updated = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Token-bucket load shedding — returns 429 when enabled and the bucket is dry."""

    def __init__(self, app, store, rate: float, burst: int) -> None:
        super().__init__(app)
        self._store = store
        self._bucket = TokenBucket(rate, burst)

    async def dispatch(self, request: Request, call_next):
        if not _exempt(request.url.path) and self._store.read().remediation.rate_limit_enabled:
            if not self._bucket.allow():
                return JSONResponse(status_code=429, content={"detail": "load shed"})
        return await call_next(request)


class ResponseCache:
    """Tiny TTL cache for the `GET /orders` list payload."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        hit = self._store.get(key)
        if hit is None:
            return None
        stored_at, value = hit
        if (time.monotonic() - stored_at) > self._ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object) -> None:
        self._store[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._store.clear()


async def apply_remediation(action: str, enabled: bool, *, store, database, cache: ResponseCache):
    """Dispatch a remediation action; returns the new RemediationState."""
    if action == "enable_cache":
        if not enabled:
            cache.clear()
        return store.update_remediation(cache_enabled=enabled)

    if action == "disable_feature_flag":
        # enabled=True (default) -> perform the rollback (flag OFF);
        # enabled=False -> undo the rollback (flag back ON).
        return store.update_remediation(feature_flag_enabled=not enabled)

    if action == "enable_rate_limit":
        return store.update_remediation(rate_limit_enabled=enabled)

    if action == "reset_pool":
        await database.reset()
        current = store.read().remediation.pool_generation
        cache.clear()
        return store.update_remediation(pool_generation=current + 1)

    raise ValueError(f"unknown remediation action: {action}")

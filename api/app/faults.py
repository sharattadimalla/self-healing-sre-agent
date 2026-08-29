"""Runtime fault injection.

- ``FaultMiddleware`` injects request-path latency (``target == "api"``) and a
  fraction of HTTP 500s (only while the feature flag is ON — ``disable_feature_flag``
  is the rollback).
- ``maybe_slow_db`` is awaited by routes before their queries so ``target == "db"``
  latency lands inside the SQL span region of the trace.
"""
from __future__ import annotations

import asyncio
import random

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Paths that must never be perturbed, or the demo/observability breaks.
_EXEMPT_PREFIXES = ("/healthz", "/metrics", "/admin")


def _exempt(path: str) -> bool:
    return any(path.startswith(p) for p in _EXEMPT_PREFIXES)


class FaultMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, store, rng: random.Random | None = None) -> None:
        super().__init__(app)
        self._store = store
        self._rng = rng or random.Random()

    async def dispatch(self, request: Request, call_next):
        if _exempt(request.url.path):
            return await call_next(request)

        state = self._store.read()
        fault = state.fault

        if fault.latency_ms > 0 and fault.latency_target == "api":
            await asyncio.sleep(fault.latency_ms / 1000.0)

        if (
            fault.error_rate > 0.0
            and state.remediation.feature_flag_enabled
            and self._rng.random() < fault.error_rate
        ):
            return JSONResponse(
                status_code=500,
                content={"detail": "injected fault (feature flag)"},
            )

        return await call_next(request)


async def maybe_slow_db(store) -> None:
    """Await the configured DB-path delay, if any."""
    fault = store.read().fault
    if fault.latency_ms > 0 and fault.latency_target == "db":
        await asyncio.sleep(fault.latency_ms / 1000.0)

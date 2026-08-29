"""Application factory + wiring."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import text

from app.config import Settings, get_settings
from app.db import Base, Database
from app.faults import FaultMiddleware
from app.remediation import RateLimitMiddleware, ResponseCache
from app.routes_admin import router as admin_router
from app.routes_orders import router as orders_router
from app.state_store import StateStore
from app.telemetry import setup_telemetry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app.main")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    database = Database(settings.database_url, settings.db_pool_size)
    store = StateStore(settings.state_path)
    cache = ResponseCache(settings.cache_ttl_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        setup_telemetry(
            app,
            database.engine,
            endpoint=settings.otel_exporter_otlp_endpoint,
            service_name=settings.otel_service_name,
        )
        store.read()  # materialize the shared state file on boot
        logger.info("api ready (db=%s)", settings.database_url.split("@")[-1])
        yield
        await database.dispose()

    app = FastAPI(title="SRE Demo API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.store = store
    app.state.cache = cache

    # Inner-to-outer: FaultMiddleware added first, RateLimitMiddleware outermost.
    app.add_middleware(FaultMiddleware, store=store)
    app.add_middleware(
        RateLimitMiddleware,
        store=store,
        rate=settings.rate_limit_rps,
        burst=settings.rate_limit_burst,
    )

    Instrumentator().instrument(app).expose(app, include_in_schema=False)

    app.include_router(orders_router)
    app.include_router(admin_router)

    @app.get("/healthz", tags=["meta"])
    async def healthz():
        try:
            async with database.sessionmaker() as session:
                await session.execute(text("SELECT 1"))
        except Exception:  # pragma: no cover - only on a real DB outage
            logger.exception("healthz db check failed")
            return JSONResponse(status_code=503, content={"status": "db_unavailable"})
        return {"status": "ok"}

    return app


app = create_app()

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

ADMIN_TOKEN = "test-token"


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        # File-backed so `reset_pool` (dispose + rebuild engine) keeps the schema,
        # the way a real Postgres would. `:memory:` would vanish on dispose.
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        state_path=str(tmp_path / "state.json"),
        admin_token=ADMIN_TOKEN,
        otel_exporter_otlp_endpoint="",  # tracing export off in tests
        cache_ttl_seconds=5.0,
    )
    base.update(overrides)
    return Settings(**base)


@pytest_asyncio.fixture
async def client(tmp_path):
    app = create_app(make_settings(tmp_path))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest_asyncio.fixture
def auth():
    return {"X-Admin-Token": ADMIN_TOKEN}


@pytest_asyncio.fixture
def client_factory(tmp_path):
    """For tests that need custom settings (e.g. a tight rate limiter)."""
    created = []

    def _factory(**overrides):
        app = create_app(make_settings(tmp_path, **overrides))
        return app

    yield _factory
    created.clear()

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.conftest import ADMIN_TOKEN


async def test_disable_feature_flag_clears_injected_errors(client, auth):
    await client.post("/admin/fault", json={"type": "error", "magnitude": 1.0}, headers=auth)
    assert (await client.get("/orders")).status_code == 500

    r = await client.post(
        "/admin/remediation", json={"action": "disable_feature_flag"}, headers=auth
    )
    assert r.status_code == 200
    assert r.json()["remediation"]["feature_flag_enabled"] is False

    # Fault row still present, but rollback suppresses the 500s.
    assert (await client.get("/orders")).status_code == 200
    assert (await client.get("/admin/state", headers=auth)).json()["fault"]["error_rate"] == 1.0


async def test_enable_cache_bypasses_slow_db(client, auth):
    await client.post("/orders", json={"item": "c", "quantity": 1})
    await client.post(
        "/admin/fault", json={"type": "latency", "ms": 600, "target": "db"}, headers=auth
    )
    await client.post("/admin/remediation", json={"action": "enable_cache"}, headers=auth)

    # First call primes the cache (pays the DB latency once).
    first = time.perf_counter()
    await client.get("/orders")
    assert time.perf_counter() - first >= 0.6

    # Subsequent calls are served from cache, far below the 600ms DB delay.
    second = time.perf_counter()
    resp = await client.get("/orders")
    assert resp.status_code == 200
    assert time.perf_counter() - second < 0.3


async def test_reset_pool_increments_generation_and_keeps_serving(client, auth):
    before = (await client.get("/admin/state", headers=auth)).json()["remediation"]["pool_generation"]
    r = await client.post("/admin/remediation", json={"action": "reset_pool"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["remediation"]["pool_generation"] == before + 1
    assert (await client.get("/orders")).status_code == 200


async def test_enable_rate_limit_sheds_load_then_restores(client_factory, auth):
    app = client_factory(rate_limit_rps=0.0, rate_limit_burst=1)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            await ac.post(
                "/admin/remediation", json={"action": "enable_rate_limit"}, headers=auth
            )
            statuses = [(await ac.get("/orders")).status_code for _ in range(4)]
            assert statuses[0] == 200
            assert 429 in statuses

            await ac.post(
                "/admin/remediation",
                json={"action": "enable_rate_limit", "enabled": False},
                headers=auth,
            )
            assert (await ac.get("/orders")).status_code == 200


async def test_state_reports_replica_hostname(client, auth):
    resp = await client.get("/admin/state", headers=auth)
    assert resp.status_code == 200
    assert isinstance(resp.json()["replica"], str) and resp.json()["replica"]

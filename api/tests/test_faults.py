from __future__ import annotations

import time

import pytest


async def test_error_fault_forces_500_then_clears(client, auth):
    r = await client.post("/admin/fault", json={"type": "error", "magnitude": 1.0}, headers=auth)
    assert r.status_code == 200
    assert r.json()["fault"]["error_rate"] == 1.0

    resp = await client.get("/orders")
    assert resp.status_code == 500

    cleared = await client.post("/admin/fault", json={"type": "clear"}, headers=auth)
    assert cleared.status_code == 200
    assert cleared.json()["fault"]["error_rate"] == 0.0
    assert (await client.get("/orders")).status_code == 200


async def test_api_latency_fault_adds_delay(client, auth):
    baseline_start = time.perf_counter()
    await client.get("/orders")
    baseline = time.perf_counter() - baseline_start

    r = await client.post(
        "/admin/fault", json={"type": "latency", "ms": 300, "target": "api"}, headers=auth
    )
    assert r.status_code == 200

    start = time.perf_counter()
    resp = await client.get("/orders")
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200
    assert elapsed >= 0.3
    assert elapsed - baseline >= 0.25


async def test_db_latency_fault_adds_delay_on_query_path(client, auth):
    r = await client.post(
        "/admin/fault", json={"type": "latency", "ms": 250, "target": "db"}, headers=auth
    )
    assert r.status_code == 200
    assert r.json()["fault"]["latency_target"] == "db"

    start = time.perf_counter()
    resp = await client.get("/orders")
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200
    assert elapsed >= 0.25


async def test_invalid_fault_payload_is_422_and_no_change(client, auth):
    r = await client.post("/admin/fault", json={"type": "error", "magnitude": 5}, headers=auth)
    assert r.status_code == 422
    state = await client.get("/admin/state", headers=auth)
    assert state.json()["fault"]["error_rate"] == 0.0


async def test_admin_requires_token(client):
    r = await client.post("/admin/fault", json={"type": "clear"})
    assert r.status_code == 401
    r2 = await client.post(
        "/admin/fault", json={"type": "clear"}, headers={"X-Admin-Token": "wrong"}
    )
    assert r2.status_code == 401

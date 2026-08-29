from __future__ import annotations

import pytest


async def test_create_then_read_order(client):
    resp = await client.post("/orders", json={"item": "widget", "quantity": 2, "price_cents": 500})
    assert resp.status_code == 201
    body = resp.json()
    assert isinstance(body["id"], int)
    assert body["item"] == "widget"
    assert body["status"] == "new"

    got = await client.get(f"/orders/{body['id']}")
    assert got.status_code == 200
    assert got.json()["quantity"] == 2


async def test_get_missing_order_is_404(client):
    resp = await client.get("/orders/999999")
    assert resp.status_code == 404


async def test_list_and_patch_and_delete(client):
    for i in range(3):
        await client.post("/orders", json={"item": f"i{i}", "quantity": 1})

    listed = await client.get("/orders")
    assert listed.status_code == 200
    ids = [o["id"] for o in listed.json()]
    assert len(ids) == 3

    patched = await client.patch(f"/orders/{ids[0]}", json={"status": "paid"})
    assert patched.status_code == 200
    assert patched.json()["status"] == "paid"

    deleted = await client.delete(f"/orders/{ids[0]}")
    assert deleted.status_code == 204
    assert (await client.get(f"/orders/{ids[0]}")).status_code == 404


async def test_create_rejects_bad_payload(client):
    resp = await client.post("/orders", json={"item": "", "quantity": 0})
    assert resp.status_code == 422


async def test_healthz_ok(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_metrics_exposes_red_series(client):
    await client.get("/orders")
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "http_requests_total" in text
    assert "http_request_duration_seconds_bucket" in text

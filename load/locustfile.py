"""Load generator for the demo.

``OrderUser`` drives mixed ``/orders`` CRUD through Traefik. A single
``ScenarioShape`` selects one of three ramp profiles via the ``LOCUST_SCENARIO``
env var (``baseline`` | ``traffic`` | ``errors`` | ``latency``) — Locust only
runs one shape per process, so the profile is chosen by env rather than by
swapping shape classes.

    LOCUST_SCENARIO=traffic locust -f locustfile.py --headless -u 300 -r 20 \\
        --host http://localhost:80
"""
from __future__ import annotations

import os
import random

from locust import HttpUser, LoadTestShape, between, task

ITEMS = ["widget", "sprocket", "gizmo", "cog", "flange", "bracket"]
SCENARIO = os.getenv("LOCUST_SCENARIO", "baseline").lower()

# The latency scenario relies on `enable_cache` recovering p95, so it drives the
# one cacheable endpoint only — writes would invalidate the cache and the
# per-id GET also pays the injected DB latency.
_READ_ONLY = SCENARIO == "latency"
W_LIST = 1 if _READ_ONLY else 5
W_CREATE = 0 if _READ_ONLY else 3
W_GET = 0 if _READ_ONLY else 2
W_PATCH = 0 if _READ_ONLY else 1


class OrderUser(HttpUser):
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        self._ids: list[int] = []
        # prime a few ids so GET /orders/:id has targets from the first tick
        try:
            r = self.client.get("/orders?limit=25", name="GET /orders")
            self._ids = [o["id"] for o in r.json()][:25]
        except Exception:  # noqa: BLE001
            pass

    @task(W_LIST)
    def list_orders(self) -> None:
        self.client.get("/orders?limit=25", name="GET /orders")

    @task(W_CREATE)
    def create_order(self) -> None:
        payload = {
            "item": f"{random.choice(ITEMS)}-{random.randint(1, 999)}",
            "quantity": random.randint(1, 5),
            "price_cents": random.randint(100, 9999),
        }
        with self.client.post("/orders", json=payload, name="POST /orders",
                              catch_response=True) as resp:
            if resp.status_code == 201:
                try:
                    self._ids.append(resp.json()["id"])
                    self._ids = self._ids[-50:]
                except Exception:  # noqa: BLE001
                    pass
                resp.success()
            elif resp.status_code in (429, 500, 503):
                # expected while a fault / load-shed is active — don't skew the
                # failure ratio the agent is watching in Prometheus (it reads
                # http_requests_total directly), but keep Locust's own view sane.
                resp.success()

    @task(W_GET)
    def get_one(self) -> None:
        if not self._ids:
            return
        oid = random.choice(self._ids)
        with self.client.get(f"/orders/{oid}", name="GET /orders/:id",
                             catch_response=True) as resp:
            if resp.status_code in (404, 429, 500, 503):
                resp.success()

    @task(W_PATCH)
    def pay_one(self) -> None:
        if not self._ids:
            return
        oid = random.choice(self._ids)
        with self.client.patch(f"/orders/{oid}", json={"status": "paid"},
                               name="PATCH /orders/:id", catch_response=True) as resp:
            if resp.status_code in (404, 429, 500, 503):
                resp.success()


# --- ramp profiles ---------------------------------------------------------
# Each stage: (duration_seconds_from_start, target_users, spawn_rate)
PROFILES: dict[str, list[tuple[int, int, int]]] = {
    # gentle steady load — the agent should log HEALTHY every poll
    "baseline": [(60, 20, 5), (100000, 20, 5)],
    # capacity exhaustion: 10 -> 300 users
    "traffic": [
        (30, 10, 5),
        (90, 60, 10),
        (150, 300, 30),
        (100000, 300, 10),
    ],
    # steady load; the scenario script injects the error fault via /admin/fault
    "errors": [(30, 25, 5), (100000, 25, 5)],
    # steady load; the scenario script injects the DB-latency fault
    "latency": [(30, 25, 5), (100000, 25, 5)],
}


class ScenarioShape(LoadTestShape):
    def __init__(self) -> None:
        super().__init__()
        name = os.getenv("LOCUST_SCENARIO", "baseline").lower()
        self._stages = PROFILES.get(name, PROFILES["baseline"])
        self._name = name

    def tick(self):
        run_time = self.get_run_time()
        for end_time, users, spawn_rate in self._stages:
            if run_time < end_time:
                return users, spawn_rate
        return None

# Quality & Audit Report: [CHG-001]

## 1. Summary Status
* **Verdict:** APPROVED
* **Total Blockers:** 0
* **Total Warnings:** 3
* **Total Infos:** 3
* **Scope audited:** `api/` service + `docker-compose.yml` and infra config only. `agent/`,
  `load/`, and Grafana dashboards are out of scope for this change and were not reviewed.

---

## 2. Review Matrix

| Category | Status | Notes |
|---|---|---|
| Functional Completeness (`plan.md`) | PASS | FR-01…FR-13 implemented. FR-10/FR-13 are manual and unverifiable here (Docker daemon not running); code paths are in place. |
| Architectural Compliance (`architecture.md`) | PASS | Module layout, shared state file, middleware order, best-effort telemetry, and interface contracts all match. `deps.py` added (allowed — implementation detail). |
| Security & OWASP Standards | PASS | `secrets.compare_digest` token check; admin token only from env; Pydantic validation on all admin payloads; token never logged. |
| Performance & Scalability | PASS | async SA + asyncpg; TTL response cache; env-tunable pool; `api` replicated with no `container_name`. |
| Test Coverage & DoD | PASS | 16 unit tests, all green, no external services. Covers every AC except AC-04 (multi-replica, manual). |
| Code Formatting & Quality | PASS | Consistent style, typed signatures, docstrings on every module. |

---

## 3. Findings & Action Items

### [BLOCKER] findings
_None._

### [WARNING] findings
* **ID:** WRN-01
  * **Location:** `api/app/state_store.py:56` / `docker-compose.yml` `fault-state` volume
  * **Issue:** Fleet-wide state relies on a shared named volume + `os.replace`. Concurrent writes from multiple replicas are last-write-wins and the ~0.5s read cache means a replica can serve stale fault state briefly after a change. Acceptable per EC-03 and NFR-OBS-01 tolerances, but worth revisiting if the agent expects sub-second convergence.
  * **Remediation:** If tighter convergence is needed later, move state to Postgres or Redis; document the eventual-consistency window in the README.
* **ID:** WRN-02
  * **Location:** `prometheus/prometheus.yml:18` (`docker_sd_configs`)
  * **Issue:** Prometheus discovers `api` replicas over the Docker socket and rewrites `__address__` to the first container IP. On a multi-network setup this can yield an unreachable address; only validated conceptually (daemon offline).
  * **Remediation:** Verify `Status: UP` for the `api` job after `make up`; if targets are down, add a `__meta_docker_network_name` keep filter for the compose network.
* **ID:** WRN-03
  * **Location:** `api/app/remediation.py:88` (`RateLimitMiddleware`)
  * **Issue:** The token bucket is per-process, so with `--scale api=N` the effective shed threshold is `N ×` the configured rate. Fine for a demo knob; not a precise global limiter.
  * **Remediation:** Document the per-replica semantics; a global limiter would need shared state.

### [INFO] findings
* **ID:** INF-01
  * **Location:** `api/app/main.py:63` — `/metrics` is registered with `include_in_schema=False`, so it is absent from the OpenAPI doc. Intentional; runtime behavior is covered by `test_metrics_exposes_red_series`.
* **ID:** INF-02
  * **Location:** `api/tests/conftest.py:19` — tests use file-backed SQLite so `reset_pool` keeps the schema. A `:memory:` DB would be dropped on `engine.dispose()`. Documented inline.
* **ID:** INF-03
  * **Location:** `grafana/provisioning/` — only the Prometheus datasource is provisioned; the RED dashboard JSON is explicitly out of scope for CHG-001.

---

## 4. Verification Evidence
* `cd api && python -m pytest -q` → **16 passed** (Python 3.13, local venv).
* `docker compose config --quiet` → **OK** (compose schema valid).
* App factory smoke test → all documented routes registered (`/orders`, `/orders/{id}`, `/admin/fault`, `/admin/remediation`, `/admin/state`, `/healthz`; `/metrics` served but hidden from schema).
* **Not run:** `docker compose up --build -d --scale api=2` and the manual FR-10/FR-13/AC-04 checks — the Docker daemon is not running in this environment.

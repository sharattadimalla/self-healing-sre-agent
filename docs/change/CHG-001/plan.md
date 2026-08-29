# Change Plan: [CHG-001] - Managed FastAPI Workload + Docker Compose Stack

## 1. Executive Summary
* **Change ID:** CHG-001
* **Feature Name:** Self-Healing SRE Agent — `api/` workload and `docker-compose.yml`
* **Target Milestone/Release:** Demo v0.1 (Sprint 1)
* **Primary Objective:** Deliver the *managed system* half of the demo: a runnable FastAPI
  order service with a fault-injection + remediation control plane, RED metrics, and
  distributed tracing, plus the `docker compose` stack (Traefik, Postgres, OTEL collector,
  Jaeger, Prometheus, Grafana) that hosts it. The LangGraph agent, load generator, and
  Grafana dashboards are explicitly deferred.

---

## 2. Scope & Boundaries

### In-Scope
- [x] `api/` FastAPI service: app factory, async SQLAlchemy + Postgres, `orders` CRUD workload.
- [x] `app/faults.py` — runtime fault injection (latency @ api or db, error-rate) via middleware + shared JSON state.
- [x] `app/remediation.py` — app-level knobs: response cache, token-bucket rate limit / load shed (429), DB pool reset, feature-flag rollback.
- [x] `app/routes_admin.py` — `POST /admin/fault`, `POST /admin/remediation`, `GET /admin/state`; `X-Admin-Token` auth.
- [x] `app/telemetry.py` — OTEL trace provider + OTLP export → collector; FastAPI + SQLAlchemy instrumentation (API▶DB spans).
- [x] RED metrics at `/metrics` via `prometheus-fastapi-instrumentator`.
- [x] `api/Dockerfile`, `api/pyproject.toml`, `api/tests/` (faults, remediation, orders, health).
- [x] `docker-compose.yml` + minimal infra config (`traefik/`, `otel/`, `prometheus/`, `grafana/provisioning/`), `.env.example`, `Makefile`.

### Out-of-Scope (Explicit Exclusions)
- `agent/` LangGraph loop, approval server, web UI, LLM wrapper.
- `load/` locustfile and scenario scripts.
- Grafana RED dashboard JSON (only the datasource is provisioned).
- `agent` and `locust` services in `docker-compose.yml`.
- CI, SAST/SCA, GitHub PR (repo is not yet a git repository).

---

## 3. Requirements

### 3.1 Functional Requirements (FR)
| ID | Requirement | Priority | Verification Method |
|---|---|---|---|
| FR-01 | `orders` CRUD endpoints each perform 1–2 DB queries so traces have an API▶DB shape. | P0 | Automated Test (`test_orders.py`) |
| FR-02 | `POST /admin/fault {type:latency, ms, target:api\|db}` injects the requested delay on subsequent requests. | P0 | Automated Test (`test_faults.py`) |
| FR-03 | `POST /admin/fault {type:error, magnitude:0..1}` makes that fraction of requests return HTTP 500. | P0 | Automated Test (`test_faults.py`) |
| FR-04 | `POST /admin/remediation {action:disable_feature_flag}` stops injected 500s (rollback semantics). | P0 | Automated Test (`test_remediation.py`) |
| FR-05 | `POST /admin/remediation {action:enable_cache}` serves `GET /orders` from cache, bypassing the slow DB path. | P0 | Automated Test (`test_remediation.py`) |
| FR-06 | `POST /admin/remediation {action:enable_rate_limit}` returns 429 once the token bucket is empty. | P1 | Automated Test (`test_remediation.py`) |
| FR-07 | `POST /admin/remediation {action:reset_pool}` disposes and recreates the DB engine without dropping the process. | P1 | Automated Test (`test_remediation.py`) |
| FR-08 | `GET /admin/state` returns current fault + remediation state and the serving replica hostname. | P0 | Automated Test |
| FR-09 | Admin routes reject requests without a valid `X-Admin-Token` (401). | P0 | Automated Test |
| FR-10 | Fault/remediation state is read from a shared file so it survives replica churn and applies fleet-wide. | P1 | Manual (`--scale api=2`, `GET /admin/state` on both) |
| FR-11 | `GET /healthz` returns 200 when the app and DB are reachable. | P0 | Automated Test + compose healthcheck |
| FR-12 | `/metrics` exposes `http_requests_total` and `http_request_duration_seconds_bucket`. | P0 | Automated Test |
| FR-13 | `docker compose up --build -d --scale api=2` brings all in-scope services healthy; `curl localhost/healthz` via Traefik works; Prometheus `api` targets UP; Jaeger + Grafana UIs reachable. | P0 | Manual verification |

### 3.2 Non-Functional Requirements (NFR)
| Category | ID | Requirement & Metric | Constraint / Boundary |
|---|---|---|---|
| **Performance** | NFR-PERF-01 | Baseline `GET /orders` p95 < 150 ms locally with no fault active. | Single host, Postgres 16, api replicas=2 |
| **Security** | NFR-SEC-01 | All state-mutating admin endpoints require `X-Admin-Token`; token supplied via env, never logged. | Shared-secret header auth only (demo) |
| **Reliability** | NFR-REL-01 | Fault injection and remediation must be independently reversible; removing a fault restores baseline behavior with no restart. | State file is the single source of truth |
| **Scale & Data** | NFR-SCL-01 | `api` runs with `deploy.replicas` / `--scale`, no fixed `container_name`; Postgres not scaled. | Traefik load-balances replicas |
| **Observability** | NFR-OBS-01 | Every request emits a server span; every DB call emits a child span exported via OTLP to Jaeger; RED metrics scraped at 5s. | OTEL export is best-effort — app must start even if the collector is down |

---

## 4. Edge Cases & Risk Analysis
| ID | Edge Case / Risk | Impact | Expected Handling / Mitigation |
|---|---|---|---|
| EC-01 | OTEL collector unavailable at startup. | Med | Telemetry setup is wrapped; export failures are swallowed, app still serves. |
| EC-02 | Shared state file missing or corrupt. | Med | Reader returns safe defaults (no fault, feature flag ON, all knobs OFF) and rewrites the file. |
| EC-03 | Concurrent writes to the state file from multiple replicas. | Low | Writes are atomic (temp file + `os.replace`); last write wins — acceptable for a demo control plane. |
| EC-04 | `reset_pool` called under load. | Med | Old engine disposed after new engine is built; in-flight requests use the session they already hold. |
| EC-05 | Error magnitude outside `[0,1]` or negative latency. | Low | Admin payload validated by Pydantic; 422 on bad input. |
| EC-06 | Rate limiter left enabled traps the demo. | Low | `GET /admin/state` surfaces it; `enable_rate_limit` payload accepts `enabled:false` to clear. |
| EC-07 | Tests requiring Postgres in CI. | Med | Test suite uses `sqlite+aiosqlite` in-memory; telemetry disabled in tests. |

---

## 5. Acceptance Criteria & Definition of Done (DoD)

### Acceptance Criteria
- [ ] **AC-01:** Given a running stack, When `POST /admin/fault {type:error, magnitude:0.3}`, Then ~30% of `GET /orders` calls return 500 and `http_requests_total{status=~"5.."}` rises in Prometheus.
- [ ] **AC-02:** Given AC-01, When `POST /admin/remediation {action:disable_feature_flag}`, Then the 500 rate returns to ~0 with no restart.
- [ ] **AC-03:** Given `POST /admin/fault {type:latency, ms:800, target:db}`, When `GET /orders` is called, Then Jaeger shows the DB span dominating the trace; When `enable_cache` is then applied, Then subsequent `GET /orders` p95 recovers.
- [ ] **AC-04:** Given `--scale api=2`, When a fault is set via either replica, Then `GET /admin/state` on both replicas reflects it.
- [ ] **AC-05:** Given no `X-Admin-Token`, When any `/admin/*` mutation is called, Then the response is 401 and no state changes.
- [ ] **AC-06:** `pytest` in `api/` passes with no external services.

### Definition of Done (DoD) Checklist for Downstream Agents
- [x] `plan.md` confirmed and complete (Planner)
- [x] `architecture.md` approved (Architect)
- [x] OpenSpec changes drafted under `docs/change/CHG-001/open-spec/changes/` (Developer)
- [x] Code implemented and passing unit tests (Developer)
- [x] `critic-report.md` shows 0 BLOCKER findings (Critic)
- [ ] Linting, SCA, SAST, and PR created on remote GitHub (PR Agent) — **deferred: not a git repository**

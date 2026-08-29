# Software Architecture: [CHG-001] - Managed FastAPI Workload + Docker Compose Stack

## 1. Overview & Architectural Goals
* **Target Change ID:** CHG-001
* **Design Philosophy:** Schema-first control plane, decoupled fault/remediation state, best-effort observability. The `api` service is a *plain* FastAPI app whose only unusual property is a runtime-mutable behavior layer (faults + remediation) driven by a shared state file.
* **Key Technical Drivers:** fleet-wide fault state that survives replica churn (NFR-SCL-01, FR-10); independently reversible remediation (NFR-REL-01); API▶DB trace shape (NFR-OBS-01); app boots even when collector/Prometheus are down (EC-01).

---

## 2. System Architecture & Component Interaction

```
            ┌──────────────────────── docker-compose (in-scope) ───────────────────────┐
 curl ─:80─▶│ traefik ──▶ api  ×N ──asyncpg──▶ db (postgres:16)                        │
            │              │  └─BatchSpanProcessor─▶ otel-collector ─OTLP─▶ jaeger     │
            │              └─/metrics◀─scrape(5s)── prometheus ──datasource──▶ grafana │
            │   shared volume  fault-state:/state/state.json  (fault + remediation)    │
            └─────────────────────────────────────────────────────────────────────────┘
                        (agent, locust, grafana dashboards = OUT OF SCOPE)
```

Request path inside `api`:

```
HTTP ─▶ [RateLimitMiddleware] ─▶ [FaultMiddleware] ─▶ router ─▶ (route: maybe_slow_db) ─▶ SQLAlchemy ─▶ Postgres
          │ 429 if bucket empty      │ 500 for error_rate fraction (only if feature flag ON)
          │ (remediation)            │ asyncio.sleep(latency_ms) if target == "api"
```

### Component Breakdown
* **`app/config.py`** — `Settings` (pydantic-settings). Env: `DATABASE_URL`, `ADMIN_TOKEN`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `STATE_PATH`, `DB_POOL_SIZE`, `RATE_LIMIT_RPS`, `RATE_LIMIT_BURST`, `CACHE_TTL_SECONDS`. Cached via `lru_cache`.
* **`app/state_store.py`** — the single source of truth for mutable behavior. `StateStore` reads/writes one JSON doc (`FaultState` + `RemediationState`) with atomic `os.replace`; re-reads on every access with a short mtime cache so replicas converge. Safe defaults on missing/corrupt file (EC-02).
* **`app/faults.py`** — `FaultState` model (`error_rate`, `latency_ms`, `latency_target`), `FaultMiddleware` (error + api-latency injection), `maybe_slow_db()` helper used by routes for `target == "db"`.
* **`app/remediation.py`** — `RemediationState` model (`feature_flag_enabled` default `True`, `cache_enabled`, `rate_limit_enabled`, `pool_generation`), `RateLimitMiddleware` (token bucket → 429), `ResponseCache` (TTL dict for `GET /orders`), and `apply_remediation(action, params)` dispatcher.
* **`app/db.py`** — `Database` holder around `create_async_engine` / `async_sessionmaker`; `get_session` FastAPI dependency; `reset()` disposes and rebuilds the engine (bumps `pool_generation`), backing `reset_pool`.
* **`app/models.py` / `app/schemas.py`** — `Order` ORM row + Pydantic `OrderCreate` / `OrderUpdate` / `OrderRead`.
* **`app/routes_orders.py`** — `/orders` CRUD; list route consults `ResponseCache`; every route calls `maybe_slow_db()` before its queries.
* **`app/routes_admin.py`** — `/admin/fault`, `/admin/remediation`, `/admin/state`; `require_admin` dependency compares `X-Admin-Token` with `secrets.compare_digest`.
* **`app/telemetry.py`** — `setup_telemetry(app, engine)`: `TracerProvider` + `Resource(service.name)`, `OTLPSpanExporter` + `BatchSpanProcessor`, `FastAPIInstrumentor.instrument_app`, `SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)`. No-op + logged warning if endpoint unset or setup raises.
* **`app/main.py`** — `create_app()`: lifespan (create tables, wire telemetry), add middlewares (rate-limit outermost, then fault), `Instrumentator().instrument(app).expose(app)` for `/metrics`, include routers, `GET /healthz` (`SELECT 1`).

---

## 3. Data Models & API Specifications

### Data Schemas & Abstractions

**Shared state file (`STATE_PATH`, default `/state/state.json`):**
```json
{
  "fault":       { "error_rate": 0.0, "latency_ms": 0, "latency_target": "api" },
  "remediation": { "feature_flag_enabled": true, "cache_enabled": false,
                   "rate_limit_enabled": false, "pool_generation": 0 }
}
```

**`orders` table:**
| column | type | notes |
|---|---|---|
| id | int PK autoincr | |
| item | varchar(120) | required |
| quantity | int | > 0 |
| price_cents | int | >= 0 |
| status | varchar(20) | `new` \| `paid` \| `shipped` \| `cancelled`, default `new` |
| created_at | timestamptz | server default `now()` |

### Interface Contracts
```python
# app/state_store.py
class FaultState(BaseModel):
    error_rate: float = 0.0            # [0,1]
    latency_ms: int = 0               # >= 0
    latency_target: Literal["api", "db"] = "api"

class RemediationState(BaseModel):
    feature_flag_enabled: bool = True
    cache_enabled: bool = False
    rate_limit_enabled: bool = False
    pool_generation: int = 0

class StateStore:
    def read(self) -> AppState: ...
    def update_fault(self, **changes) -> FaultState: ...
    def update_remediation(self, **changes) -> RemediationState: ...

# HTTP — all admin routes require header  X-Admin-Token: <ADMIN_TOKEN>
POST /admin/fault
  {"type": "latency", "ms": 800, "target": "db"}       -> 200 {"fault": FaultState}
  {"type": "error",   "magnitude": 0.3}                -> 200 {"fault": FaultState}
  {"type": "clear"}                                    -> 200 {"fault": FaultState}   # reset to defaults
POST /admin/remediation
  {"action": "enable_cache"        [, "enabled": true]}        -> 200 {"remediation": RemediationState}
  {"action": "disable_feature_flag"}                           -> 200 {"remediation": RemediationState}
  {"action": "enable_rate_limit"   [, "enabled": true]}        -> 200 {"remediation": RemediationState}
  {"action": "reset_pool"}                                     -> 200 {"remediation": RemediationState}
GET  /admin/state    -> 200 {"fault": ..., "remediation": ..., "replica": "<hostname>"}

# workload
POST   /orders        {item, quantity, price_cents}   -> 201 OrderRead
GET    /orders        ?limit=50&offset=0              -> 200 [OrderRead]      (cacheable)
GET    /orders/{id}                                   -> 200 OrderRead | 404
PATCH  /orders/{id}   {status? , quantity?}           -> 200 OrderRead | 404
DELETE /orders/{id}                                   -> 204 | 404
GET    /healthz                                       -> 200 {"status":"ok"} | 503
GET    /metrics                                       -> Prometheus text
```

## 4. Non-Functional Requirement (NFR) Architecture Strategies
- **Performance & Throughput:** async SQLAlchemy + asyncpg; `ResponseCache` (TTL, default 5s) short-circuits the list query when `cache_enabled`; connection pool size env-tunable.
- **Security & Auth:** `secrets.compare_digest` constant-time token check; token only read from env; admin payloads validated by Pydantic (EC-05); `/admin/*` never logs the header.
- **Resilience & Fault Tolerance:** telemetry setup and OTLP export are best-effort (EC-01); `StateStore` returns defaults on corrupt file (EC-02); atomic state writes (EC-03); `reset_pool` builds the new engine before disposing the old (EC-04).
- **Observability:** OTLP traces (server span + SQLAlchemy child spans) → collector → Jaeger; `prometheus-fastapi-instrumentator` default RED metrics at `/metrics`; Prometheus discovers replicas via `docker_sd_configs` filtered on `com.docker.compose.service=api`, scrape interval 5s.

## 5. Developer Implementation Guidance
- **Patterns to Follow:** app-factory (`create_app`), dependency-injected `AsyncSession`, Pydantic models for every request/response body, one `StateStore` instance on `app.state`.
- **Dependencies & Tooling:** `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `pydantic-settings`, `prometheus-fastapi-instrumentator`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-sqlalchemy`. Dev: `pytest`, `pytest-asyncio`, `httpx`, `aiosqlite`.
- **Architectural Boundaries (Do Not):**
  - Do **not** implement scale/restart in `api` — those belong to the agent via `docker compose`.
  - Do **not** add `agent` or `locust` services to `docker-compose.yml` in this change.
  - Do **not** hold fault/remediation state only in process memory — it must round-trip through `STATE_PATH`.
  - Do **not** make app startup fail when the OTEL collector or Prometheus is unreachable.
  - Do **not** add a fixed `container_name` to `api`.

# OpenSpec Change: managed-api-and-compose

## Why
The demo needs the system that the Self-Healing SRE Agent will manage. This change delivers
the FastAPI workload with a fault/remediation control plane and the Docker Compose stack
that hosts it and its observability backends. The agent, load generator, and dashboards are
delivered by later changes.

## What Changes
- ADD `api/` FastAPI service (orders CRUD, `/healthz`, `/metrics`).
- ADD runtime fault injection: request latency, DB latency, error-rate — driven by a shared state file.
- ADD app-level remediation knobs: response cache, token-bucket rate limit (429), DB pool reset, feature-flag rollback.
- ADD admin control plane `POST /admin/fault`, `POST /admin/remediation`, `GET /admin/state` with `X-Admin-Token` auth.
- ADD OpenTelemetry tracing (API▶DB spans) exported via OTLP.
- ADD `docker-compose.yml` with `traefik`, `api` (replicated), `db`, `otel-collector`, `jaeger`, `prometheus`, `grafana`, plus `.env.example` and `Makefile`.

## Capabilities

### Capability: orders-workload
Each CRUD endpoint issues 1–2 SQL queries so distributed traces show a real API▶DB shape.

#### Scenario: create then read an order
- **WHEN** a client `POST /orders {item:"widget", quantity:2, price_cents:500}`
- **THEN** the response is `201` with a body containing a numeric `id` and `status:"new"`
- **AND** a subsequent `GET /orders/{id}` returns `200` with the same fields

#### Scenario: read a missing order
- **WHEN** a client `GET /orders/999999`
- **THEN** the response is `404`

### Capability: fault-injection
Faults are set through the admin API and take effect on subsequent requests without a restart.

#### Scenario: error-rate fault
- **WHEN** an operator `POST /admin/fault {type:"error", magnitude:1.0}` with a valid admin token
- **THEN** every subsequent `GET /orders` returns `500`
- **AND** `POST /admin/fault {type:"clear"}` restores `200` responses

#### Scenario: api latency fault
- **WHEN** an operator `POST /admin/fault {type:"latency", ms:300, target:"api"}`
- **THEN** subsequent requests take at least ~300 ms longer to respond

#### Scenario: db latency fault surfaces in traces
- **WHEN** an operator `POST /admin/fault {type:"latency", ms:800, target:"db"}`
- **THEN** the DB span is the dominant span in the request's trace

#### Scenario: invalid fault payload
- **WHEN** an operator `POST /admin/fault {type:"error", magnitude:5}`
- **THEN** the response is `422` and state is unchanged

### Capability: remediation
Each remediation is independently reversible and observable via `GET /admin/state`.

#### Scenario: feature-flag rollback clears injected errors
- **GIVEN** an active `{type:"error", magnitude:1.0}` fault
- **WHEN** an operator `POST /admin/remediation {action:"disable_feature_flag"}`
- **THEN** subsequent `GET /orders` returns `200` even though the fault row is still present

#### Scenario: cache bypasses the slow DB path
- **GIVEN** an active `{type:"latency", ms:800, target:"db"}` fault
- **WHEN** an operator `POST /admin/remediation {action:"enable_cache"}`
- **THEN** the second and later `GET /orders` calls return within the cache TTL, far faster than 800 ms

#### Scenario: rate limit sheds load
- **WHEN** an operator `POST /admin/remediation {action:"enable_rate_limit"}` and then floods `GET /orders`
- **THEN** once the token bucket is empty, further requests return `429`
- **AND** `POST /admin/remediation {action:"enable_rate_limit", enabled:false}` restores `200`

#### Scenario: pool reset does not kill the process
- **WHEN** an operator `POST /admin/remediation {action:"reset_pool"}`
- **THEN** the response is `200`, `pool_generation` increments, and later `GET /orders` still works

### Capability: admin-auth
#### Scenario: missing token is rejected
- **WHEN** any `/admin/*` mutation is called without `X-Admin-Token`
- **THEN** the response is `401` and no state changes

### Capability: fleet-wide-state
#### Scenario: state survives replica churn
- **GIVEN** `api` running with `--scale api=2`
- **WHEN** a fault is set through one replica
- **THEN** `GET /admin/state` served by the other replica reflects the same fault

### Capability: observability
#### Scenario: RED metrics exposed
- **WHEN** a client `GET /metrics`
- **THEN** the body contains `http_requests_total` and `http_request_duration_seconds_bucket`

#### Scenario: app starts without the collector
- **GIVEN** `OTEL_EXPORTER_OTLP_ENDPOINT` points at an unreachable host
- **WHEN** the app starts
- **THEN** `GET /healthz` still returns `200`

### Capability: compose-stack
#### Scenario: stack comes up healthy
- **WHEN** an operator runs `docker compose up --build -d --scale api=2`
- **THEN** `traefik`, `api`, `db`, `otel-collector`, `jaeger`, `prometheus`, `grafana` all reach a healthy/running state
- **AND** `curl localhost/healthz` (via Traefik) returns `200`
- **AND** the Prometheus `api` job shows `--scale` targets UP

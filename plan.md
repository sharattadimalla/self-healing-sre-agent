# Self-Healing SRE Agent — End-to-End Demo

## Context

Greenfield project (empty repo). Goal: a runnable `docker compose` demo where a LangGraph
agent continuously watches a FastAPI service, **detects** anomalies in throughput / latency /
error-rate, **analyzes** them (with distributed traces as evidence), **recommends** a
remediation, **asks a human to approve**, and on approval **applies** the fix and verifies
recovery. Three scripted fault scenarios (traffic spike, error spike, latency spike) drive it.

## Design decisions (confirmed)

- **Agent brain:** `analyze`/`recommend` call **Claude** (`claude-opus-5`, Anthropic SDK,
  adaptive thinking) with a **deterministic rule-table fallback** when `ANTHROPIC_API_KEY`
  is unset — demo always runs.
- **Approval:** **minimal web UI** served by the agent (open-incident list + Approve/Reject
  buttons), backed by the same `/incidents` HTTP API; optional Slack alert if configured.
- **Remediation:** **real container ops** — agent holds the Docker socket and shells out to
  `docker compose` to scale/restart `api`; app-level fixes (cache, feature-flag rollback,
  rate limiter, DB-pool reset) still go through `api`'s admin endpoint.
- **Observability:** **Prometheus + Jaeger + Grafana** (provisioned RED dashboard).

## High-level architecture

```
 locust ─load─▶ Traefik ─▶ FastAPI "api" (N replicas) ──SQL──▶ Postgres
                              │  │
                   OTLP traces│  │Prometheus /metrics (RED)
                              ▼  ▼
                     otel-collector ─▶ Jaeger (traces)      Prometheus ─▶ Grafana
                                          ▲                     ▲
                                          └──── agent ──────────┘
              LangGraph: collect ▶ detect ▶ analyze ▶ recommend
                         ▶ [approval interrupt / web UI] ▶ act ▶ verify
                                          │
                    docker compose --scale/restart  +  POST /admin/remediation
```

### docker compose services
- **traefik** — entry point on :80/:8080; discovers `api` replicas via Docker labels and
  load-balances. locust and the agent target Traefik, so scaling actually spreads load.
- **api** — FastAPI workload + admin control plane; run with `deploy.replicas` / `--scale`,
  no fixed `container_name`.
- **db** — Postgres 16.
- **otel-collector** — receives OTLP from api, exports traces to Jaeger.
- **jaeger** — all-in-one, trace UI + query API (used by the agent's `analyze` node).
- **prometheus** — scrapes api replicas (via Traefik metrics / per-task discovery); source of the 3 key signals.
- **grafana** — provisioned Prometheus datasource + RED dashboard (fault vs. recovery).
- **agent** — LangGraph loop + FastAPI approval server + web UI; mounts
  `/var/run/docker.sock` and the compose file; has `docker` CLI + compose plugin in its image.
- **locust** — headless load gen with per-scenario `LoadTestShape`s + web UI on 8089.

## Component design

### api/ (the managed system)
- `app/main.py` — app factory, lifespan wires telemetry + DB; mounts routers.
- `app/telemetry.py` — OTEL tracer/meter provider, OTLP exporter → collector,
  `FastAPIInstrumentor` + `SQLAlchemyInstrumentor` (distributed API▶DB spans).
- `app/db.py` — async SQLAlchemy engine/session; pool size is a remediation knob.
- `app/models.py` + `app/routes_orders.py` — trivial `orders` CRUD = the workload
  (each request does 1–2 DB queries so traces have a real API▶DB shape).
- `app/faults.py` — middleware reading fault state: inject latency (`asyncio.sleep`),
  inject errors (return 500 for a fraction of requests), or slow the DB path.
- `app/remediation.py` — app-level knobs (per replica, so admin calls fan out or are
  idempotent on each): response cache on/off, token-bucket rate limiter / load shedding
  (429), DB pool reset, feature-flag rollback. (Scale/restart are done by the agent via
  `docker compose`, not here.)
- `app/routes_admin.py` — `POST /admin/fault`, `POST /admin/remediation`,
  `GET /admin/state`; shared-token auth via `X-Admin-Token`. Fault state is also read
  from env/shared volume so it survives replica churn and applies fleet-wide.
- RED metrics via `prometheus-fastapi-instrumentator` at `/metrics`
  (`http_requests_total`, `http_request_duration_seconds_bucket`).

### agent/ (LangGraph — kept simple)
- `app/state.py` — `AgentState` TypedDict: `signals`, `trace_summary`, `anomaly`,
  `diagnosis`, `recommendation`, `incident_id`, `approved`, `action_result`, `verified`.
- `app/graph.py` — one `StateGraph`, `MemorySaver` checkpointer, thread per incident:
  `collect ▶ detect ▶ (END if healthy | analyze) ▶ recommend ▶ approval(interrupt) ▶ act ▶ verify ▶ (END | escalate)`.
  `verify` retries at most once, then escalates (logs + alert).
- `app/clients.py` — `PrometheusClient` (instant PromQL), `JaegerClient`
  (recent traces for `service=api`, span-duration breakdown), `ApiAdminClient`
  (app-level knobs via Traefik), `DockerOpsClient` (wraps `docker compose -p <proj>
  up -d --scale api=N` and `docker compose -p <proj> restart api`; reads current
  replica count from `docker compose ps`).
- `app/nodes/collect.py` — PromQL:
  - throughput `sum(rate(http_requests_total[1m]))`
  - error rate `sum(rate(http_requests_total{status=~"5.."}[1m])) / sum(rate(http_requests_total[1m]))`
  - p95 `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[1m])) by (le))`
  - each compared to a rolling baseline held in agent memory.
- `app/nodes/detect.py` — thresholds → anomaly class:
  `SATURATION` (thru↑ & p95↑ & errors↑), `ERROR_SPIKE` (errors↑, thru/p95 ~flat),
  `LATENCY_SPIKE` (p95↑, DB span dominates), else `HEALTHY`.
- `app/nodes/analyze.py` — builds a hypothesis from signals + Jaeger span breakdown;
  Claude-backed via `app/llm.py`, deterministic rule-table fallback when no API key.
- `app/catalog.py` — remediation catalog. Each entry: `action` id, `executor`
  (`docker` | `api`), params, rationale, risk, expected effect. Actions:
  `scale_api` (docker), `restart_api` (docker), `enable_cache` / `disable_feature_flag`
  / `enable_rate_limit` / `reset_pool` (api). Plus `anomaly ▶ candidate actions` map.
- `app/nodes/recommend.py` — pick top candidate(s) for the diagnosis; structured output
  (Claude via `output_config.format`, or the fallback table).
- `app/nodes/approve.py` — `interrupt()`; write incident to store, render it in the web
  UI, optional Slack alert; resumes when the approval server records a decision.
- `app/nodes/act.py` — dispatch by `executor`: `DockerOpsClient` for scale/restart,
  `ApiAdminClient` for app knobs. Records before/after replica count + admin state.
- `app/nodes/verify.py` — cooldown, re-collect, confirm signal back in band; 1 retry then escalate.
- `app/main.py` — runs the poll loop (default every 15s) **and** a FastAPI server:
  `GET /incidents`, `GET /incidents/{id}`, `POST /incidents/{id}/approve|reject`, and
  `GET /` → **web UI** (server-rendered HTML + a little JS polling `/incidents`, with
  Approve/Reject buttons). `make approve`/`make reject` also hit the same endpoints.
- `app/webui.py` — the HTML template / static assets for the approval UI.
- `app/llm.py` — Anthropic client wrapper: `claude-opus-5`, `thinking={"type":"adaptive"}`,
  structured output for the diagnosis/recommendation schema; returns `None` on no key so
  callers fall back to the rule table.

### load/
- `locustfile.py` — `OrderUser` (mixed CRUD) + three `LoadTestShape` classes.
- `scenarios/*.sh` — orchestrate a run: start baseline load → inject fault via
  `/admin/fault` (or ramp shape) → tail agent logs → wait for approval → `make approve`
  → show recovery. Also `make scenario-traffic|scenario-errors|scenario-latency`.

### infra config
- `traefik/traefik.yml` — Docker provider, entrypoint :80, dashboard :8080.
- `otel/collector-config.yaml` — OTLP receiver → Jaeger exporter.
- `prometheus/prometheus.yml` — scrape api replicas + Traefik, 5s interval.
- `grafana/provisioning/**` + `grafana/dashboards/red.json` — datasource + RED dashboard.

## The three scenarios (detect ▶ analyze ▶ recommend ▶ act)

| Scenario | Injected | Signals | Diagnosis | Recommended action | Verify |
|---|---|---|---|---|---|
| Traffic spike | locust ramp 10▶300 RPS | thru↑, p95↑, 5xx↑ (capacity exhaustion) | saturation / under-provisioned | `scale_api` (2▶4 replicas, docker) + `enable_rate_limit` (api) | p95 back in band, 5xx→0 |
| Error spike | `POST /admin/fault {type:error, magnitude:0.3}` | 5xx rate ↑, thru/p95 flat | bad feature flag / deploy | `disable_feature_flag` (api rollback) | 5xx→~0 |
| Latency spike | `POST /admin/fault {type:latency, ms:800, target:db}` | p95↑, Jaeger shows DB span dominant | slow downstream dependency | `enable_cache` (api) + `restart_api` (docker, clears pool) | p95 recovers |

## Key files to create

- `docker-compose.yml`, `Makefile`, `README.md`, `.env.example`
- `api/Dockerfile`, `api/pyproject.toml`, `api/app/{main,telemetry,db,models,faults,remediation}.py`, `api/app/routes_{orders,admin}.py`, `api/tests/`
- `agent/Dockerfile` (base image + `docker` CLI + compose plugin), `agent/pyproject.toml`,
  `agent/app/{main,graph,state,clients,catalog,llm,webui}.py`,
  `agent/app/nodes/{collect,detect,analyze,recommend,approve,act,verify}.py`, `agent/tests/`
- `load/locustfile.py`, `load/scenarios/{traffic_spike,error_spike,latency_spike}.sh`
- `traefik/traefik.yml`, `otel/collector-config.yaml`, `prometheus/prometheus.yml`,
  `grafana/provisioning/**`, `grafana/dashboards/red.json`

## Verification

1. `make up` — `docker compose up --build -d --scale api=2`; all services healthy;
   `curl localhost/healthz` (via Traefik), Prometheus targets UP, Jaeger + Grafana UIs reachable.
2. `make seed` + `make load` — baseline traffic; agent logs `HEALTHY` each poll;
   Jaeger shows API▶DB spans; Grafana RED dashboard populated.
3. `make scenario-errors` — within ~1 min agent logs `ERROR_SPIKE`, prints diagnosis +
   recommendation, creates an incident visible at `http://localhost:<agent>/`, pauses.
   Click **Approve** (or `make approve`) → agent applies `disable_feature_flag`,
   `verify` confirms 5xx→0, incident closes.
4. `make scenario-traffic` — agent recommends `scale_api`; confirm `docker compose ps`
   shows api scaled 2▶4 and p95 recovers on the dashboard.
5. `make scenario-latency` — analysis cites the Jaeger DB-span breakdown; `restart_api`
   + `enable_cache` recover p95.
6. **Reject** path: recommendation dropped, incident marked `REJECTED`, nothing applied.
7. Tests: `pytest` in `api/` (fault + remediation state) and `agent/` (detect
   thresholds, catalog selection, graph reaches the `approval` interrupt, fallback
   rule-table used when no API key). `DockerOpsClient` unit-tested with the compose
   calls mocked.

## Assumptions

- Local Docker with the compose v2 plugin; the agent container gets `/var/run/docker.sock`
  and the repo mounted read-only so it can run `docker compose` against the same project.
- Single host, single Postgres (not scaled). "Distributed tracing" = API replica ▶ DB
  spans stitched via OTEL context propagation.
- `ANTHROPIC_API_KEY` optional; without it the agent uses the deterministic rule table
  and the demo still completes all three scenarios.
- Ports (host): Traefik 80 + 8080, agent UI 8000, locust 8089, Jaeger 16686,
  Prometheus 9090, Grafana 3000 — adjustable in `.env`.

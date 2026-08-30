# Self-Healing SRE Agent — End-to-End Demo

A runnable `docker compose` demo where a **LangGraph** agent continuously watches a
FastAPI service, **detects** anomalies in throughput / latency / error-rate,
**analyzes** them with distributed traces as evidence, **recommends** a
remediation, **asks a human to approve**, then **applies** the fix and **verifies**
recovery. Three scripted fault scenarios drive it.

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

## Quick start

```bash
cp .env.example .env          # optional; sane defaults otherwise
make up                       # build + start everything, api scaled to 2
make health                   # api (via Traefik) + agent both OK
make seed && make load        # 20 orders + a steady baseline load
make logs-agent               # watch the agent log HEALTHY each poll
```

Open:

| UI | URL |
|---|---|
| Agent approval console | http://localhost:8000/ |
| Grafana RED dashboard  | http://localhost:3000/ (`SRE Demo → SRE Demo — RED`, anon viewer) |
| Prometheus             | http://localhost:9090/ |
| Jaeger                 | http://localhost:16686/ |
| Traefik dashboard      | http://localhost:8080/ |
| Locust                 | http://localhost:8089/ |

Then run a scenario (auto-approves by default):

```bash
make scenario-errors      # 5xx spike  → disable_feature_flag
make scenario-traffic     # saturation → scale_api (2→4) + enable_rate_limit
make scenario-latency     # slow DB    → enable_cache + restart_api
```

To drive approval by hand instead, run with `AUTO_APPROVE=0` and click **Approve**
in the agent UI (or `make approve INC=INC-0001`). `APPROVE_DECISION=reject`
exercises the reject path (nothing is applied, incident → `REJECTED`).

## The three scenarios

| Scenario | Injected | Signals | Diagnosis | Recommended action | Verify |
|---|---|---|---|---|---|
| Traffic spike | locust ramp 10▶300 users | thru↑, p95↑, 5xx↑ | saturation / under-provisioned | `scale_api` (docker) + `enable_rate_limit` (api) | p95 back in band, 5xx→0 |
| Error spike | `POST /admin/fault {type:error, magnitude:0.3}` | 5xx↑, thru/p95 flat | bad feature flag / deploy | `disable_feature_flag` (api) | 5xx→~0 |
| Latency spike | `POST /admin/fault {type:latency, ms:800, target:db}` | p95↑, Jaeger DB span dominates | slow downstream dependency | `enable_cache` (api) + `restart_api` (docker) | p95 recovers |

## How the agent works

`agent/app/graph.py` is one `StateGraph` with a `MemorySaver` checkpointer:

```
collect ▶ detect ▶ (END if HEALTHY | analyze) ▶ recommend
        ▶ approve[interrupt] ▶ act ▶ verify ▶ (END | recheck→escalate)
```

- **collect** — instant PromQL for throughput `sum(rate(http_requests_total[1m]))`,
  5xx ratio, and p95 from `http_request_duration_seconds_bucket`; each compared to
  a rolling EWMA baseline held in agent memory (updated only while `HEALTHY`).
- **detect** — threshold rules → `SATURATION` (thru↑ & p95↑ & errors↑),
  `ERROR_SPIKE` (errors↑, rest flat), `LATENCY_SPIKE` (p95↑, errors flat), else
  `HEALTHY`. Thresholds live in `agent/app/config.py`.
- **analyze** — pulls a Jaeger span-duration breakdown (DB vs. app self-time) and
  builds a root-cause hypothesis. **Claude-backed** (`claude-opus-5`, adaptive
  thinking, JSON-schema structured output) when `ANTHROPIC_API_KEY` is set;
  otherwise a **deterministic rule table** — the demo always runs.
- **recommend** — picks action(s) from `agent/app/catalog.py` for the anomaly
  class; Claude ranks/selects the subset when available, else the catalog default.
- **approve** — opens an incident, renders it in the web UI, optionally posts a
  Slack alert, and `interrupt()`s the graph until a human decision is recorded.
- **act** — dispatches by executor: `docker compose` scale/restart for
  `scale_api` / `restart_api`; `POST /admin/remediation` for the app-level knobs
  (`enable_cache`, `disable_feature_flag`, `enable_rate_limit`, `reset_pool`).
  Records before/after replica count + admin state.
- **verify** — cooldown, re-collect, confirm the signal is back in band; one
  extra recheck, then escalate (log + Slack).

The poll loop (`agent/app/main.py`, default every 10s) runs one graph thread at a
time and also serves the approval API + web UI:
`GET /incidents`, `GET /incidents/{id}`, `POST /incidents/{id}/approve|reject`,
`GET /` (HTML console).

## Remediation transport

The `agent` container mounts `/var/run/docker.sock` and the repo (read-only) and
runs `docker compose -p sre-demo -f /workspace/docker-compose.yml …` to scale or
restart `api`. App-level fixes go through Traefik to `api`'s `/admin/remediation`
(shared-token `X-Admin-Token` auth), so they fan out across replicas.

## Tests

```bash
make test          # api + agent
# or individually
cd api   && pytest -q   # fault + remediation state, orders, health, /metrics
cd agent && pytest -q   # detect thresholds, catalog selection, DockerOps argv (mocked),
                        # graph reaches the approval interrupt, rule-table fallback,
                        # approve / reject / escalate paths
```

## Layout

```
api/     FastAPI workload + fault/remediation control plane + OTEL + RED metrics
agent/   LangGraph loop, clients, catalog, nodes/, LLM wrapper, approval UI
load/    locustfile.py (OrderUser + scenario ramp profiles) + scenarios/*.sh
traefik/ otel/ prometheus/ grafana/   infra config + provisioned RED dashboard
docker-compose.yml  Makefile  .env.example
```

## Notes / assumptions

- Local Docker with the compose v2 plugin. The `agent` image bundles the docker
  CLI + compose plugin (copied from `docker:27-cli`).
- Single host, single Postgres (not scaled). "Distributed tracing" = API replica
  ▶ DB spans stitched via OTEL context propagation.
- Locust runs one `LoadTestShape` per process, so the ramp profile is selected by
  the `LOCUST_SCENARIO` env var (`baseline` | `traffic` | `errors` | `latency`)
  rather than by swapping shape classes.
- `ANTHROPIC_API_KEY` is optional; without it the agent uses the rule table and
  still completes all three scenarios.
- Ports are all overridable in `.env`.

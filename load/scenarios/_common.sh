# Shared helpers for the scenario scripts. Sourced, not executed.
# Host-side: talks to the published ports from docker-compose.

set -euo pipefail

BASE="${BASE:-http://localhost:${TRAEFIK_HTTP_PORT:-80}}"
AGENT="${AGENT:-http://localhost:${AGENT_UI_PORT:-8000}}"
ADMIN_TOKEN="${ADMIN_TOKEN:-dev-admin-token}"
ADMIN_HDR=(-H "X-Admin-Token: ${ADMIN_TOKEN}" -H "Content-Type: application/json")
AUTO_APPROVE="${AUTO_APPROVE:-1}"     # 1 = script approves; 0 = wait for the UI
APPROVE_DECISION="${APPROVE_DECISION:-approve}"  # approve | reject

say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }

require_stack() {
  if ! curl -fsS --max-time 3 "${BASE}/healthz" >/dev/null 2>&1; then
    echo "stack not reachable at ${BASE} — run 'make up' first" >&2
    exit 1
  fi
  if ! curl -fsS --max-time 3 "${AGENT}/healthz" >/dev/null 2>&1; then
    echo "agent not reachable at ${AGENT} — run 'make up' first" >&2
    exit 1
  fi
}

PROJECT="${COMPOSE_PROJECT:-sre-demo}"
_LOAD_CID=""

seed_orders() {
  local n="${1:-30}"
  for _ in $(seq 1 "${n}"); do
    curl -fsS -X POST "${BASE}/orders" -H "Content-Type: application/json" \
      -d "{\"item\":\"seed-$RANDOM\",\"quantity\":$((RANDOM % 5 + 1)),\"price_cents\":$((RANDOM % 5000))}" \
      >/dev/null || true
  done
  info "seeded ${n} orders"
}

# start_load <profile> <users> [run-time]
start_load() {
  local profile="$1" users="$2" runtime="${3:-10m}"
  _LOAD_CID=$(docker compose -p "${PROJECT}" run -d --rm \
    -e LOCUST_SCENARIO="${profile}" locust \
    -f /mnt/locust/locustfile.py --headless -u "${users}" -r 10 \
    --run-time "${runtime}" --host http://traefik:80 2>/dev/null | tr -d '[:space:]')
  info "load started (${profile}, ${users} users, container ${_LOAD_CID:0:12})"
}

stop_load() {
  [[ -n "${_LOAD_CID}" ]] && docker rm -f "${_LOAD_CID}" >/dev/null 2>&1 || true
  # Nuke *every* locust container (one-off runs and the standing compose service):
  # any writer POSTing /orders keeps clearing the response cache, which breaks
  # the latency scenario's recovery. Bring the service back with
  # `docker compose up -d locust` for the manual web-UI workflow.
  docker ps -q --filter "ancestor=locustio/locust:2.31.8" \
    | xargs -r docker rm -f >/dev/null 2>&1 || true
  _LOAD_CID=""
}

fault() { curl -fsS -X POST "${BASE}/admin/fault" "${ADMIN_HDR[@]}" -d "$1" >/dev/null; }
clear_fault() { curl -fsS -X POST "${BASE}/admin/fault" "${ADMIN_HDR[@]}" -d '{"type":"clear"}' >/dev/null; }
admin_state() { curl -fsS "${BASE}/admin/state" "${ADMIN_HDR[@]}"; }
remediate() { curl -fsS -X POST "${BASE}/admin/remediation" "${ADMIN_HDR[@]}" -d "$1" >/dev/null; }

# Put the system back to a clean baseline so scenarios are re-runnable:
# no fault, every remediation knob off, api scaled to the minimum, and the agent
# restarted so it warms a fresh rolling baseline for this scenario's load shape.
reset_system() {
  clear_fault
  remediate '{"action":"disable_feature_flag","enabled":false}'   # flag back ON
  remediate '{"action":"enable_cache","enabled":false}'
  remediate '{"action":"enable_rate_limit","enabled":false}'
  docker compose -p "${PROJECT}" up -d --no-recreate --no-build \
    --scale api="${API_REPLICAS:-2}" api >/dev/null 2>&1 || true
  stop_load

  # Let the previous scenario's fault wash out of Prometheus' 1m rate windows
  # BEFORE restarting the agent, so it doesn't seed its rolling baseline from a
  # stale-high p95 / error rate.
  info "draining prior metrics (up to 90s)…"
  local waited=0 p95 err thru
  while (( waited < 90 )); do
    thru=$(promq 'sum(rate(http_requests_total[1m]))')
    p95=$(promq 'histogram_quantile(0.95,sum(rate(http_request_duration_seconds_bucket[1m]))by(le))')
    err=$(promq 'sum(rate(http_requests_total{status=~"5.."}[1m]))/clamp_min(sum(rate(http_requests_total[1m])),1e-9)')
    # quiet == almost no traffic, OR (low p95 AND low error rate)
    if awk "BEGIN{q=(\"${thru:-9}\"+0 < 1);
                  ok=(\"${p95:-9}\"+0 < 0.2 && \"${err:-9}\"+0 < 0.02);
                  exit !(q || ok)}"; then break; fi
    sleep 6; waited=$((waited+6))
  done

  docker compose -p "${PROJECT}" restart agent >/dev/null 2>&1 || true
  waited=0
  until curl -fsS --max-time 2 "${AGENT}/healthz" >/dev/null 2>&1 || (( waited > 30 )); do
    sleep 2; waited=$((waited+2))
  done
  info "system reset (no fault, knobs off, api=${API_REPLICAS:-2}, agent baseline cleared)"
}

# promq <expr> -> first scalar value, or empty
promq() {
  curl -fsS -G "http://localhost:${PROMETHEUS_PORT:-9090}/api/v1/query" \
    --data-urlencode "query=$1" 2>/dev/null \
  | python3 -c "import sys,json;r=json.load(sys.stdin).get('data',{}).get('result',[]);print(r[0]['value'][1] if r else '')" 2>/dev/null || true
}

# Wait until an incident shows up in the requested state; echoes its id.
wait_for_incident() {
  local want="${1:-OPEN}" timeout="${2:-120}" waited=0 id
  while (( waited < timeout )); do
    id=$(curl -fsS "${AGENT}/incidents" 2>/dev/null \
      | python3 -c "import sys,json;
xs=[i for i in json.load(sys.stdin) if i['status']=='${want}'];
print(xs[0]['id'] if xs else '')" 2>/dev/null || true)
    [[ -n "${id}" ]] && { echo "${id}"; return 0; }
    sleep 3; waited=$((waited+3))
  done
  return 1
}

wait_for_status() {
  local id="$1" want="$2" timeout="${3:-180}" waited=0 st
  while (( waited < timeout )); do
    st=$(curl -fsS "${AGENT}/incidents/${id}" 2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || true)
    info "incident ${id} status=${st:-?}"
    [[ "${st}" == "${want}" ]] && return 0
    [[ "${st}" == "ESCALATED" && "${want}" == "RESOLVED" ]] && { warn "escalated"; return 1; }
    sleep 4; waited=$((waited+4))
  done
  return 1
}

show_incident() {
  curl -fsS "${AGENT}/incidents/$1" | python3 -m json.tool
}

decide() {
  local id="$1" verb="${2:-approve}"
  curl -fsS -X POST "${AGENT}/incidents/${id}/${verb}" >/dev/null
  info "sent ${verb} for ${id}"
}

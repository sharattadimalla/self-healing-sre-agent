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
    --run-time "${runtime}" --host http://traefik:80)
  info "load started (${profile}, ${users} users, container ${_LOAD_CID:0:12})"
}

stop_load() {
  [[ -n "${_LOAD_CID}" ]] && docker rm -f "${_LOAD_CID}" >/dev/null 2>&1 || true
}

fault() { curl -fsS -X POST "${BASE}/admin/fault" "${ADMIN_HDR[@]}" -d "$1" >/dev/null; }
clear_fault() { curl -fsS -X POST "${BASE}/admin/fault" "${ADMIN_HDR[@]}" -d '{"type":"clear"}' >/dev/null; }
admin_state() { curl -fsS "${BASE}/admin/state" "${ADMIN_HDR[@]}"; }

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

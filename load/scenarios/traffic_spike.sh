#!/usr/bin/env bash
# Traffic-spike scenario: ramp locust 10 -> 300 users so the api saturates
# (throughput, p95 and 5xx all climb). The agent should diagnose SATURATION and
# recommend scale_api (+ enable_rate_limit); confirm replicas 2 -> 4 and p95 recovers.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/_common.sh"

require_stack
reset_system
trap 'stop_load' EXIT

replicas() {
  docker compose -p "${PROJECT}" ps --format json api 2>/dev/null \
    | python3 -c "import sys;print(sum(1 for l in sys.stdin if l.strip()))" 2>/dev/null || echo '?'
}

seed_orders 30
start_load baseline 15 3m

say "Baseline for 90s so the agent warms its rolling baseline"
sleep 90
stop_load

say "Ramping load 10 -> 300 users (locust 'traffic' profile)"
start_load traffic 300 8m

say "Waiting for the agent to open an incident…"
INC=$(wait_for_incident OPEN 240) || { echo "no incident opened" >&2; exit 1; }
say "Incident ${INC} is open — review it at ${AGENT}/"
show_incident "${INC}"

BEFORE=$(replicas); info "api replicas before: ${BEFORE}"

if [[ "${AUTO_APPROVE}" == "1" ]]; then
  say "Auto-${APPROVE_DECISION}-ing ${INC} (set AUTO_APPROVE=0 to use the web UI)"
  decide "${INC}" "${APPROVE_DECISION}"
else
  say "Open ${AGENT}/ and click Approve (or: make approve INC=${INC})"
fi

if [[ "${APPROVE_DECISION}" == "reject" ]]; then
  wait_for_status "${INC}" REJECTED 60 && say "Rejected — nothing applied ✔"
  exit 0
fi

say "Waiting for verification…"
wait_for_status "${INC}" RESOLVED 260 || warn "did not resolve — check agent logs"

AFTER=$(replicas)
say "api replicas: ${BEFORE} -> ${AFTER}   (expect a scale-out)"
admin_state | python3 -m json.tool

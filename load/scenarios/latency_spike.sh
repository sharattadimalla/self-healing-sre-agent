#!/usr/bin/env bash
# Latency-spike scenario: inject 800ms of DB-path latency. The agent should see
# p95 climb with a flat error rate, cite the Jaeger DB-span breakdown, recommend
# enable_cache (+ restart_api), and confirm p95 recovers.
#
# The load is read-heavy (LOCUST_SCENARIO=latency) because the api response cache
# is invalidated on every write.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/_common.sh"

require_stack
trap 'clear_fault; stop_load' EXIT

seed_orders 50
start_load latency 25 12m

say "Baseline for 30s so the agent warms its rolling baseline"
sleep 30

say "Injecting latency fault: +800ms on the DB path"
fault '{"type":"latency","ms":800,"target":"db"}'
admin_state | python3 -m json.tool

say "Waiting for the agent to open an incident…"
INC=$(wait_for_incident OPEN 150) || { echo "no incident opened" >&2; exit 1; }
say "Incident ${INC} is open — review it at ${AGENT}/"
show_incident "${INC}"

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
if wait_for_status "${INC}" RESOLVED 220; then
  say "p95 recovered ✔"
else
  warn "did not resolve — check agent logs"
fi
say "Final admin state:"
admin_state | python3 -m json.tool

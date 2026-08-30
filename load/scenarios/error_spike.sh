#!/usr/bin/env bash
# Error-spike scenario: inject a 30% 5xx rate, let the agent detect ERROR_SPIKE,
# recommend disable_feature_flag, approve it, and confirm 5xx -> ~0.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${HERE}/_common.sh"

require_stack
reset_system
trap 'clear_fault; stop_load' EXIT

seed_orders 30
start_load errors 25 10m

say "Baseline for 90s so the agent warms its rolling baseline"
sleep 90

say "Injecting error fault: 30% of requests -> HTTP 500"
fault '{"type":"error","magnitude":0.3}'
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
if wait_for_status "${INC}" RESOLVED 180; then
  say "Recovered ✔"
else
  warn "did not resolve — check agent logs"
fi
say "Final admin state:"
admin_state | python3 -m json.tool

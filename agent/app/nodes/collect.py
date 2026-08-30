"""collect — pull the three RED signals from Prometheus."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.deps import Deps
from app.state import AgentState

logger = logging.getLogger("app.nodes.collect")

THROUGHPUT_Q = 'sum(rate(http_requests_total[1m]))'
ERROR_RATE_Q = (
    'sum(rate(http_requests_total{status=~"5.."}[1m])) '
    '/ clamp_min(sum(rate(http_requests_total[1m])), 1e-9)'
)
P95_Q = (
    'histogram_quantile(0.95, '
    'sum(rate(http_request_duration_seconds_bucket[1m])) by (le))'
)


def make_collect(deps: Deps):
    async def collect(state: AgentState) -> AgentState:
        throughput = await deps.prom.instant(THROUGHPUT_Q)
        error_rate = await deps.prom.instant(ERROR_RATE_Q)
        p95 = await deps.prom.instant(P95_Q)

        signals = {
            "throughput_rps": round(throughput, 3) if throughput is not None else 0.0,
            "error_rate": round(error_rate, 4) if error_rate is not None else 0.0,
            "p95_seconds": round(p95, 4) if p95 is not None else 0.0,
        }
        baseline = deps.baseline.snapshot()
        logger.info("collect signals=%s baseline=%s", signals, baseline)
        return {
            "poll_ts": datetime.now(timezone.utc).isoformat(),
            "signals": signals,
            "baseline": baseline,
        }

    return collect

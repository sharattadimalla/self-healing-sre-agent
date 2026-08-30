"""analyze — build a root-cause hypothesis from signals + Jaeger span breakdown.

Claude-backed when an API key is configured; otherwise a deterministic rule table.
"""
from __future__ import annotations

import logging

from app.deps import Deps
from app.nodes.detect import ERROR_SPIKE, LATENCY_SPIKE, SATURATION
from app.state import AgentState

logger = logging.getLogger("app.nodes.analyze")


def _rule_diagnosis(anomaly: str, signals: dict, baseline: dict, traces: dict) -> dict:
    err = signals.get("error_rate", 0.0)
    p95 = signals.get("p95_seconds", 0.0)
    thru = signals.get("throughput_rps", 0.0)
    db_frac = traces.get("db_fraction", 0.0)

    if anomaly == SATURATION:
        return {
            "summary": (
                f"api is under-provisioned for the current load "
                f"({thru:.0f} rps): throughput, p95 ({p95*1000:.0f}ms) and 5xx "
                f"({err*100:.0f}%) are all elevated — capacity exhaustion."
            ),
            "root_cause": "insufficient api replicas for offered load",
            "confidence": "high",
            "evidence": [
                f"throughput {thru:.1f} rps vs baseline {baseline.get('throughput_rps', 0):.1f}",
                f"p95 {p95*1000:.0f}ms vs baseline {baseline.get('p95_seconds', 0)*1000:.0f}ms",
                f"5xx fraction {err:.3f}",
            ],
        }
    if anomaly == ERROR_SPIKE:
        return {
            "summary": (
                f"5xx rate is {err*100:.0f}% while throughput and latency are flat — "
                f"consistent with a bad feature flag / recent deploy."
            ),
            "root_cause": "regression behind an enabled feature flag",
            "confidence": "high",
            "evidence": [
                f"5xx fraction {err:.3f} (>= threshold)",
                f"p95 {p95*1000:.0f}ms ~ baseline",
                f"throughput {thru:.1f} rps ~ baseline",
            ],
        }
    if anomaly == LATENCY_SPIKE:
        db_note = (
            f"Jaeger: DB spans are {db_frac*100:.0f}% of trace time"
            if db_frac
            else "Jaeger span breakdown unavailable"
        )
        return {
            "summary": (
                f"p95 latency is {p95*1000:.0f}ms with a flat error rate; {db_note} "
                f"— slow downstream dependency (database)."
            ),
            "root_cause": "slow database dependency dominating request latency",
            "confidence": "high" if db_frac >= 0.5 else "medium",
            "evidence": [
                f"p95 {p95*1000:.0f}ms vs baseline {baseline.get('p95_seconds', 0)*1000:.0f}ms",
                f"db_fraction {db_frac:.2f}",
                f"top ops (ms): {traces.get('by_operation_ms', {})}",
            ],
        }
    return {
        "summary": "no clear anomaly signature",
        "root_cause": "unknown",
        "confidence": "low",
        "evidence": [],
    }


def make_analyze(deps: Deps):
    async def analyze(state: AgentState) -> AgentState:
        anomaly = state["anomaly"]
        signals = state.get("signals", {})
        baseline = state.get("baseline", {})

        trace_summary = await deps.jaeger.span_breakdown(service="api", lookback="5m")
        logger.info("analyze trace_summary=%s", trace_summary)

        diagnosis = None
        source = "rules"
        if deps.llm.enabled:
            diagnosis = await deps.llm.diagnose(
                anomaly=anomaly,
                signals=signals,
                baseline=baseline,
                trace_summary=trace_summary,
            )
            if diagnosis is not None:
                source = "llm"
        if diagnosis is None:
            diagnosis = _rule_diagnosis(anomaly, signals, baseline, trace_summary)

        logger.info("analyze diagnosis (%s): %s", source, diagnosis.get("summary"))
        return {
            "trace_summary": trace_summary,
            "diagnosis": diagnosis,
            "diagnosis_source": source,
        }

    return analyze

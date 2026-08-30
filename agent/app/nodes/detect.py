"""detect — classify the current signals into an anomaly class.

    SATURATION    throughput ↑ AND p95 ↑ AND error-rate ↑
    ERROR_SPIKE   error-rate ↑ with throughput / p95 ~flat
    LATENCY_SPIKE p95 ↑ with error-rate ~flat
    HEALTHY       otherwise

Ratios are vs. the rolling baseline; if the baseline is not warm yet, only the
absolute error-rate threshold can fire.
"""
from __future__ import annotations

import logging

from app.config import Settings
from app.deps import Deps
from app.state import AgentState

logger = logging.getLogger("app.nodes.detect")

HEALTHY = "HEALTHY"
SATURATION = "SATURATION"
ERROR_SPIKE = "ERROR_SPIKE"
LATENCY_SPIKE = "LATENCY_SPIKE"


def classify(signals: dict, baseline: dict, s: Settings) -> tuple[str, list[str]]:
    notes: list[str] = []
    thru = signals.get("throughput_rps", 0.0)
    err = signals.get("error_rate", 0.0)
    p95 = signals.get("p95_seconds", 0.0)

    base_thru = baseline.get("throughput_rps")
    base_p95 = baseline.get("p95_seconds")

    thru_ratio = (thru / base_thru) if base_thru and base_thru > 0 else None
    p95_ratio = (p95 / base_p95) if base_p95 and base_p95 > 0 else None

    errors_up = err >= s.error_rate_abs
    thru_up = thru_ratio is not None and thru_ratio >= s.throughput_ratio_high
    p95_up = (
        p95_ratio is not None
        and p95_ratio >= s.p95_ratio_high
        and p95 >= s.p95_abs_floor_seconds
    )
    thru_flat = thru_ratio is None or thru_ratio < s.throughput_ratio_high
    p95_flat = p95_ratio is None or p95_ratio < s.p95_ratio_high

    notes.append(
        f"thru={thru:.2f}rps (x{thru_ratio:.2f})" if thru_ratio
        else f"thru={thru:.2f}rps (no baseline)"
    )
    notes.append(f"err={err:.3f} (>= {s.error_rate_abs} ? {errors_up})")
    notes.append(
        f"p95={p95*1000:.0f}ms (x{p95_ratio:.2f})" if p95_ratio
        else f"p95={p95*1000:.0f}ms (no baseline)"
    )

    if thru < s.min_throughput_rps and not errors_up:
        notes.append("throughput below min — treating as idle/healthy")
        return HEALTHY, notes

    if errors_up and thru_up and p95_up:
        return SATURATION, notes
    if errors_up and thru_flat and p95_flat:
        return ERROR_SPIKE, notes
    if p95_up and not errors_up:
        return LATENCY_SPIKE, notes
    if errors_up:
        # errors up but pattern is ambiguous — default to the flag rollback path
        notes.append("errors elevated, pattern ambiguous -> ERROR_SPIKE")
        return ERROR_SPIKE, notes
    return HEALTHY, notes


def make_detect(deps: Deps):
    async def detect(state: AgentState) -> AgentState:
        anomaly, notes = classify(
            state.get("signals", {}), state.get("baseline", {}), deps.settings
        )
        logger.info("detect -> %s | %s", anomaly, "; ".join(notes))
        return {"anomaly": anomaly, "detect_notes": notes}

    return detect

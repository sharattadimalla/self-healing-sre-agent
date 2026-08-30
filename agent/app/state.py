"""The LangGraph agent state — one dict threaded through the graph per incident."""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class Signals(TypedDict, total=False):
    throughput_rps: float
    error_rate: float
    p95_seconds: float


class AgentState(TypedDict, total=False):
    # collect
    poll_ts: str
    signals: Signals
    baseline: Signals
    # detect
    anomaly: str  # SATURATION | ERROR_SPIKE | LATENCY_SPIKE | HEALTHY
    detect_notes: list[str]
    # analyze
    trace_summary: dict[str, Any]
    diagnosis: dict[str, Any]
    diagnosis_source: str  # "llm" | "rules"
    # recommend
    recommendation: dict[str, Any]
    recommendation_source: str
    # approve
    incident_id: str
    approved: Optional[bool]
    # act
    action_result: dict[str, Any]
    # verify
    verify_attempts: int
    verified: bool
    escalated: bool
    post_signals: Signals

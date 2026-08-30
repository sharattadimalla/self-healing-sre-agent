"""Remediation catalog: what the agent is allowed to do, and for which anomaly.

Each action is executed either by ``docker`` (compose scale/restart, done by the
agent) or by ``api`` (an app-level knob via ``POST /admin/remediation``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Executor = Literal["docker", "api"]


@dataclass(frozen=True)
class Action:
    id: str
    executor: Executor
    summary: str
    rationale: str
    risk: str
    expected_effect: str
    params: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "executor": self.executor,
            "summary": self.summary,
            "rationale": self.rationale,
            "risk": self.risk,
            "expected_effect": self.expected_effect,
            "params": dict(self.params),
        }


CATALOG: dict[str, Action] = {
    "scale_api": Action(
        id="scale_api",
        executor="docker",
        summary="Scale the api service out by adding replicas",
        rationale="More replicas spread the request load and restore headroom.",
        risk="low",
        expected_effect="p95 latency falls back into band, 5xx from saturation stop",
        params={"mode": "scale_out"},
    ),
    "restart_api": Action(
        id="restart_api",
        executor="docker",
        summary="Rolling restart of the api service",
        rationale="Drops stale DB connections / warmed-bad state and rebuilds the pool.",
        risk="medium",
        expected_effect="connection-pool pressure clears, latency recovers",
    ),
    "enable_cache": Action(
        id="enable_cache",
        executor="api",
        summary="Enable the GET /orders response cache",
        rationale="Serves reads from cache so a slow DB dependency stops dominating latency.",
        risk="low",
        expected_effect="read-path p95 recovers while the DB is slow",
    ),
    "disable_feature_flag": Action(
        id="disable_feature_flag",
        executor="api",
        summary="Roll back the suspect feature flag",
        rationale="A recent flag/deploy is returning 5xx; rolling it back is the fast mitigation.",
        risk="low",
        expected_effect="5xx rate returns to ~0 with no restart",
    ),
    "enable_rate_limit": Action(
        id="enable_rate_limit",
        executor="api",
        summary="Enable token-bucket load shedding (429 over budget)",
        rationale="Protects the service while it scales; sheds the excess instead of failing hard.",
        risk="medium",
        expected_effect="error rate from overload drops; some requests get 429 until scaled",
    ),
    "reset_pool": Action(
        id="reset_pool",
        executor="api",
        summary="Dispose and rebuild the DB connection pool",
        rationale="Clears exhausted/leaked connections without a process restart.",
        risk="low",
        expected_effect="DB acquisition stalls clear, latency recovers",
    ),
}

# anomaly class -> ordered candidate action ids (best first)
ANOMALY_ACTIONS: dict[str, list[str]] = {
    "SATURATION": ["scale_api", "enable_rate_limit"],
    "ERROR_SPIKE": ["disable_feature_flag"],
    "LATENCY_SPIKE": ["enable_cache", "restart_api"],
}


def candidates_for(anomaly: str) -> list[Action]:
    return [CATALOG[a] for a in ANOMALY_ACTIONS.get(anomaly, [])]

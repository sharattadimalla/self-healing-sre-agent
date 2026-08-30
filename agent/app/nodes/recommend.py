"""recommend — pick the remediation action(s) for the diagnosis.

Claude ranks/selects from the catalog candidates when available; otherwise the
catalog's default order for the anomaly class is used verbatim.
"""
from __future__ import annotations

import logging

from app.catalog import CATALOG, candidates_for
from app.deps import Deps
from app.state import AgentState

logger = logging.getLogger("app.nodes.recommend")


def _fallback_recommendation(anomaly: str) -> dict:
    actions = candidates_for(anomaly)
    return {
        "actions": [a.as_dict() for a in actions],
        "rationale": "; ".join(a.rationale for a in actions),
        "risk": max((a.risk for a in actions), default="low",
                    key=lambda r: {"low": 0, "medium": 1, "high": 2}[r]),
        "expected_effect": "; ".join(a.expected_effect for a in actions),
    }


def make_recommend(deps: Deps):
    async def recommend(state: AgentState) -> AgentState:
        anomaly = state["anomaly"]
        diagnosis = state.get("diagnosis", {})
        catalog_candidates = candidates_for(anomaly)

        recommendation = None
        source = "rules"
        if deps.llm.enabled and catalog_candidates:
            picked = await deps.llm.recommend(
                anomaly=anomaly,
                diagnosis=diagnosis,
                candidates=[a.as_dict() for a in catalog_candidates],
            )
            if picked is not None:
                valid_ids = [
                    aid for aid in picked.get("action_ids", []) if aid in CATALOG
                ]
                if valid_ids:
                    recommendation = {
                        "actions": [CATALOG[aid].as_dict() for aid in valid_ids],
                        "rationale": picked.get("rationale", ""),
                        "risk": picked.get("risk", "low"),
                        "expected_effect": picked.get("expected_effect", ""),
                    }
                    source = "llm"

        if recommendation is None:
            recommendation = _fallback_recommendation(anomaly)

        logger.info(
            "recommend (%s): %s",
            source,
            [a["id"] for a in recommendation["actions"]],
        )
        return {"recommendation": recommendation, "recommendation_source": source}

    return recommend

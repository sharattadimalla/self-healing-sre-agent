"""approve — open an incident, pause the graph, resume on a human decision.

``interrupt()`` suspends the run; the poll loop resumes it with
``Command(resume={"approved": bool})`` once the approval API records a decision.
The node re-runs from its top on resume, so incident creation is idempotent
(keyed by the graph thread id).
"""
from __future__ import annotations

import logging

from langgraph.types import interrupt

from app.deps import Deps
from app.state import AgentState

logger = logging.getLogger("app.nodes.approve")


def make_approve(deps: Deps):
    async def approve(state: AgentState, config) -> AgentState:
        thread_id = config["configurable"]["thread_id"]
        incident = deps.store.get_or_create(
            thread_id,
            anomaly=state["anomaly"],
            signals=state.get("signals", {}),
            baseline=state.get("baseline", {}),
            trace_summary=state.get("trace_summary", {}),
            diagnosis=state.get("diagnosis", {}),
            recommendation=state.get("recommendation", {}),
        )
        incident_id = incident["id"]

        if incident["status"] == "OPEN" and incident.get("recommendation_source") is None:
            deps.store.update(
                incident_id,
                recommendation_source=state.get("recommendation_source", "rules"),
                diagnosis_source=state.get("diagnosis_source", "rules"),
            )
            actions = [a["id"] for a in state.get("recommendation", {}).get("actions", [])]
            logger.info(
                "INCIDENT %s opened: %s -> recommend %s (awaiting approval)",
                incident_id, state["anomaly"], actions,
            )
            await deps.alert(
                f":rotating_light: {incident_id} {state['anomaly']} — "
                f"{state.get('diagnosis', {}).get('summary', '')}\n"
                f"Recommended: {actions}. Approve at the agent web UI."
            )

        decision = interrupt(
            {
                "incident_id": incident_id,
                "anomaly": state["anomaly"],
                "diagnosis": state.get("diagnosis", {}),
                "recommendation": state.get("recommendation", {}),
            }
        )

        approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
        logger.info("INCIDENT %s decision: %s", incident_id,
                    "APPROVED" if approved else "REJECTED")
        return {"incident_id": incident_id, "approved": approved}

    return approve

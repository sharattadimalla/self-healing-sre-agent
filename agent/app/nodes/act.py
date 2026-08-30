"""act — apply the approved remediation, dispatching by executor.

``docker`` actions go through ``DockerOpsClient`` (compose scale/restart);
``api`` actions go through ``ApiAdminClient`` (POST /admin/remediation).
Records before/after replica count and admin state.
"""
from __future__ import annotations

import logging

from app.deps import Deps
from app.incidents import APPROVED, REJECTED
from app.state import AgentState

logger = logging.getLogger("app.nodes.act")


def make_act(deps: Deps):
    s = deps.settings

    async def _run_docker(action: dict) -> dict:
        before = await deps.docker.replica_count()
        if action["id"] == "scale_api":
            target = min(s.max_replicas, max(s.min_replicas, before + s.scale_step))
            await deps.docker.scale(target)
            after = await deps.docker.replica_count()
            return {"id": action["id"], "executor": "docker", "ok": True,
                    "replicas_before": before, "replicas_after": after,
                    "target": target}
        if action["id"] == "restart_api":
            await deps.docker.restart()
            after = await deps.docker.replica_count()
            return {"id": action["id"], "executor": "docker", "ok": True,
                    "replicas_before": before, "replicas_after": after}
        raise ValueError(f"unknown docker action {action['id']}")

    async def _run_api(action: dict) -> dict:
        resp = await deps.api.set_remediation(action["id"], enabled=True)
        return {"id": action["id"], "executor": "api", "ok": True, "response": resp}

    async def act(state: AgentState) -> AgentState:
        incident_id = state.get("incident_id")
        if not state.get("approved"):
            logger.info("INCIDENT %s rejected — applying nothing", incident_id)
            result = {"applied": [], "skipped": True, "reason": "rejected"}
            if incident_id:
                deps.store.update(incident_id, status=REJECTED, action_result=result)
            return {"action_result": result}

        applied: list[dict] = []
        errors: list[str] = []
        for action in state.get("recommendation", {}).get("actions", []):
            try:
                if action["executor"] == "docker":
                    applied.append(await _run_docker(action))
                else:
                    applied.append(await _run_api(action))
            except Exception as exc:  # keep going; verify/escalate handles partials
                logger.exception("action %s failed", action.get("id"))
                errors.append(f"{action.get('id')}: {exc}")

        try:
            admin_state = await deps.api.get_state()
        except Exception:  # pragma: no cover - best effort
            admin_state = None

        result = {
            "applied": applied,
            "errors": errors,
            "skipped": False,
            "admin_state": admin_state,
        }
        logger.info("INCIDENT %s acted: %s errors=%s", incident_id,
                    [a["id"] for a in applied], errors)
        if incident_id:
            deps.store.update(incident_id, status=APPROVED, action_result=result)
        return {"action_result": result}

    return act

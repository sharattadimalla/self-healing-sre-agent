"""verify — cooldown, re-collect, confirm the signal is back in band.

At most ``verify_max_retries`` extra cooldown+recheck cycles, then escalate.
"""
from __future__ import annotations

import asyncio
import logging

from app.deps import Deps
from app.incidents import ESCALATED, RESOLVED
from app.nodes.collect import ERROR_RATE_Q, P95_Q, THROUGHPUT_Q
from app.nodes.detect import HEALTHY, classify
from app.state import AgentState

logger = logging.getLogger("app.nodes.verify")


def make_verify(deps: Deps):
    s = deps.settings

    async def _sample() -> dict:
        thru = await deps.prom.instant(THROUGHPUT_Q)
        err = await deps.prom.instant(ERROR_RATE_Q)
        p95 = await deps.prom.instant(P95_Q)
        return {
            "throughput_rps": round(thru, 3) if thru is not None else 0.0,
            "error_rate": round(err, 4) if err is not None else 0.0,
            "p95_seconds": round(p95, 4) if p95 is not None else 0.0,
        }

    async def verify(state: AgentState) -> AgentState:
        incident_id = state.get("incident_id")
        attempts = state.get("verify_attempts", 0) + 1

        if state.get("action_result", {}).get("skipped"):
            # nothing was applied (rejected path) — terminal, not escalated
            return {"verify_attempts": attempts, "verified": False, "escalated": False}

        await asyncio.sleep(s.verify_cooldown_seconds)
        post = await _sample()
        anomaly, notes = classify(post, deps.baseline.snapshot(), s)
        healthy = anomaly == HEALTHY
        logger.info(
            "INCIDENT %s verify attempt %d -> %s | %s",
            incident_id, attempts, anomaly, "; ".join(notes),
        )

        if healthy:
            if incident_id:
                deps.store.update(
                    incident_id, status=RESOLVED,
                    verify_result={"ok": True, "attempts": attempts, "post_signals": post},
                )
            return {"verify_attempts": attempts, "verified": True,
                    "escalated": False, "post_signals": post}

        if attempts <= s.verify_max_retries:
            logger.info("INCIDENT %s not recovered — will recheck once more", incident_id)
            return {"verify_attempts": attempts, "verified": False,
                    "escalated": False, "post_signals": post}

        logger.warning("INCIDENT %s NOT recovered after %d checks — escalating",
                       incident_id, attempts)
        await deps.alert(
            f":warning: {incident_id} did NOT recover after remediation "
            f"({attempts} checks). Post-signals: {post}. Human attention needed."
        )
        if incident_id:
            deps.store.update(
                incident_id, status=ESCALATED,
                verify_result={"ok": False, "attempts": attempts, "post_signals": post},
            )
        return {"verify_attempts": attempts, "verified": False,
                "escalated": True, "post_signals": post}

    return verify

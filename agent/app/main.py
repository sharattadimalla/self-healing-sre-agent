"""Agent entrypoint: the poll loop + the approval API / web UI, one process.

The poll loop drives one graph run at a time:

* idle  -> start a fresh run (``collect``/``detect``); HEALTHY ends it and feeds
  the rolling baseline. An anomaly runs through ``recommend`` and pauses at the
  ``approve`` interrupt; the run's thread id + incident id are remembered.
* pending approval -> once the approval API records a decision, resume the same
  thread with ``Command(resume=…)`` so ``act`` / ``verify`` run to completion.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.config import get_settings
from app.deps import Deps
from app.graph import build_graph
from app.incidents import IncidentStore
from app.nodes.detect import HEALTHY
from app.webui import PAGE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app.main")


class Agent:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.store = IncidentStore()
        self.deps = Deps.build(self.settings, store=self.store)
        self.graph = build_graph(self.deps, checkpointer=MemorySaver())
        self._run_seq = 0
        self._active: dict | None = None  # {"thread_id", "incident_id"}
        self._stop = asyncio.Event()

    # --- poll loop ---------------------------------------------------------
    async def _start_run(self) -> None:
        self._run_seq += 1
        thread_id = f"poll-{self._run_seq}"
        config = {"configurable": {"thread_id": thread_id}}
        result = await self.graph.ainvoke({}, config)

        interrupts = result.get("__interrupt__")
        if interrupts:
            payload = getattr(interrupts[0], "value", {}) or {}
            incident_id = payload.get("incident_id")
            self._active = {"thread_id": thread_id, "incident_id": incident_id}
            logger.info("run %s paused for approval on %s", thread_id, incident_id)
            return

        anomaly = result.get("anomaly", HEALTHY)
        if anomaly == HEALTHY:
            signals = result.get("signals", {})
            # Freeze the baseline once signals start to ramp, so a slow climb
            # into an anomaly doesn't drag the reference along with it.
            if self.deps.baseline.is_stable(
                signals, warm_floor=self.settings.min_throughput_rps
            ):
                self.deps.baseline.update(signals)
            else:
                logger.info("baseline frozen (signals ramping): %s", signals)
        else:
            logger.info("run %s ended without interrupt (anomaly=%s)", thread_id, anomaly)

    async def _resume_if_decided(self) -> None:
        assert self._active is not None
        incident = self.store.get(self._active["incident_id"])
        if incident is None or incident.get("decision") is None:
            return  # still waiting on a human
        decision = bool(incident["decision"])
        config = {"configurable": {"thread_id": self._active["thread_id"]}}
        logger.info("resuming %s with approved=%s", self._active["incident_id"], decision)
        result = await self.graph.ainvoke(Command(resume={"approved": decision}), config)
        logger.info(
            "incident %s closed: verified=%s escalated=%s",
            self._active["incident_id"],
            result.get("verified"),
            result.get("escalated"),
        )
        self._active = None

    async def poll_loop(self) -> None:
        logger.info(
            "poll loop up (interval=%ss, prometheus=%s, llm=%s)",
            self.settings.poll_interval_seconds,
            self.settings.prometheus_url,
            "on" if self.deps.llm.enabled else "off (rule-table fallback)",
        )
        while not self._stop.is_set():
            try:
                if self._active is None:
                    await self._start_run()
                else:
                    await self._resume_if_decided()
            except Exception:  # never let the loop die
                logger.exception("poll tick failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.poll_interval_seconds
                )

    def stop(self) -> None:
        self._stop.set()


def build_app(agent: Agent) -> FastAPI:
    app = FastAPI(title="SRE Agent", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return PAGE

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "active_incident": (agent._active or {}).get("incident_id")}

    @app.get("/incidents")
    async def list_incidents():
        return agent.store.list()

    @app.get("/incidents/{incident_id}")
    async def get_incident(incident_id: str):
        incident = agent.store.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="unknown incident")
        return incident

    @app.post("/incidents/{incident_id}/approve")
    async def approve_incident(incident_id: str):
        updated = agent.store.record_decision(incident_id, True)
        if updated is None:
            raise HTTPException(status_code=409, detail="incident not open")
        return updated

    @app.post("/incidents/{incident_id}/reject")
    async def reject_incident(incident_id: str):
        updated = agent.store.record_decision(incident_id, False)
        if updated is None:
            raise HTTPException(status_code=409, detail="incident not open")
        return updated

    @app.exception_handler(Exception)
    async def _unhandled(_request, exc):  # pragma: no cover
        logger.exception("request failed")
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    return app


async def _main() -> None:
    agent = Agent()
    app = build_app(agent)
    config = uvicorn.Config(
        app,
        host=agent.settings.server_host,
        port=agent.settings.server_port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    loop_task = asyncio.create_task(agent.poll_loop())
    try:
        await server.serve()
    finally:
        agent.stop()
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task


if __name__ == "__main__":
    asyncio.run(_main())

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.graph import build_graph
from app.incidents import APPROVED, REJECTED, RESOLVED


def _config(tid):
    return {"configurable": {"thread_id": tid}}


async def test_healthy_run_ends_without_incident(stub_deps):
    stub_deps.baseline.seed({"throughput_rps": 20.0, "error_rate": 0.0, "p95_seconds": 0.05})
    stub_deps.prom.set(throughput_rps=20.0, error_rate=0.0, p95_seconds=0.05)
    graph = build_graph(stub_deps, checkpointer=MemorySaver())

    result = await graph.ainvoke({}, _config("t-healthy"))
    assert result["anomaly"] == "HEALTHY"
    assert "__interrupt__" not in result
    assert stub_deps.store.list() == []


async def test_anomaly_run_pauses_at_approval_interrupt_with_rule_fallback(stub_deps):
    stub_deps.baseline.seed({"throughput_rps": 20.0, "error_rate": 0.0, "p95_seconds": 0.05})
    stub_deps.prom.set(throughput_rps=20.0, error_rate=0.30, p95_seconds=0.05)
    graph = build_graph(stub_deps, checkpointer=MemorySaver())

    result = await graph.ainvoke({}, _config("t-err"))
    assert result["__interrupt__"]
    payload = result["__interrupt__"][0].value
    assert payload["anomaly"] == "ERROR_SPIKE"
    assert payload["recommendation"]["actions"][0]["id"] == "disable_feature_flag"

    incidents = stub_deps.store.list()
    assert len(incidents) == 1
    assert incidents[0]["status"] == "OPEN"
    assert incidents[0]["recommendation_source"] == "rules"  # no API key -> fallback


async def test_approve_path_applies_api_action_and_resolves(stub_deps):
    stub_deps.baseline.seed({"throughput_rps": 20.0, "error_rate": 0.0, "p95_seconds": 0.05})
    stub_deps.prom.set(throughput_rps=20.0, error_rate=0.30, p95_seconds=0.05)
    graph = build_graph(stub_deps, checkpointer=MemorySaver())

    await graph.ainvoke({}, _config("t-approve"))
    incident_id = stub_deps.store.list()[0]["id"]
    stub_deps.store.record_decision(incident_id, True)

    # once remediation lands, the (stub) signals read healthy again
    stub_deps.prom.set(error_rate=0.0)
    result = await graph.ainvoke(Command(resume={"approved": True}), _config("t-approve"))

    assert result["verified"] is True
    assert ("disable_feature_flag", True) in stub_deps.api.calls
    assert stub_deps.store.get(incident_id)["status"] == RESOLVED


async def test_reject_path_applies_nothing(stub_deps):
    stub_deps.baseline.seed({"throughput_rps": 20.0, "error_rate": 0.0, "p95_seconds": 0.05})
    stub_deps.prom.set(throughput_rps=20.0, error_rate=0.30, p95_seconds=0.05)
    graph = build_graph(stub_deps, checkpointer=MemorySaver())

    await graph.ainvoke({}, _config("t-reject"))
    incident_id = stub_deps.store.list()[0]["id"]
    stub_deps.store.record_decision(incident_id, False)

    result = await graph.ainvoke(Command(resume={"approved": False}), _config("t-reject"))
    assert result["action_result"]["skipped"] is True
    assert stub_deps.api.calls == []
    assert stub_deps.store.get(incident_id)["status"] == REJECTED


async def test_saturation_scales_via_docker_and_escalates_if_not_recovered(stub_deps):
    stub_deps.baseline.seed({"throughput_rps": 20.0, "error_rate": 0.0, "p95_seconds": 0.05})
    stub_deps.prom.set(throughput_rps=140.0, error_rate=0.12, p95_seconds=0.6)
    graph = build_graph(stub_deps, checkpointer=MemorySaver())

    await graph.ainvoke({}, _config("t-sat"))
    incident_id = stub_deps.store.list()[0]["id"]
    assert stub_deps.store.get(incident_id)["anomaly"] == "SATURATION"
    stub_deps.store.record_decision(incident_id, True)

    # signals stay bad -> verify retries once then escalates
    result = await graph.ainvoke(Command(resume={"approved": True}), _config("t-sat"))
    assert "scale=4" in stub_deps.docker.calls
    assert result["escalated"] is True
    assert stub_deps.store.get(incident_id)["status"] == "ESCALATED"

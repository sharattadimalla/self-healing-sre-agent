"""The self-healing control graph.

    collect ▶ detect ▶ (END if HEALTHY | analyze)
            ▶ recommend ▶ approve[interrupt] ▶ act ▶ verify ▶ (END | recheck)

One ``StateGraph`` with a ``MemorySaver`` checkpointer; the poll loop uses one
thread id per incident so ``interrupt()`` / ``Command(resume=…)`` works.
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.deps import Deps
from app.nodes.act import make_act
from app.nodes.analyze import make_analyze
from app.nodes.approve import make_approve
from app.nodes.collect import make_collect
from app.nodes.detect import HEALTHY, make_detect
from app.nodes.recommend import make_recommend
from app.nodes.verify import make_verify
from app.state import AgentState


def _route_after_detect(state: AgentState) -> str:
    return END if state.get("anomaly", HEALTHY) == HEALTHY else "analyze"


def _route_after_verify(state: AgentState) -> str:
    if state.get("verified") or state.get("escalated"):
        return END
    if state.get("action_result", {}).get("skipped"):
        return END
    return "verify"  # one more cooldown + recheck (bounded by verify_max_retries)


def build_graph(deps: Deps, checkpointer=None):
    builder = StateGraph(AgentState)
    builder.add_node("collect", make_collect(deps))
    builder.add_node("detect", make_detect(deps))
    builder.add_node("analyze", make_analyze(deps))
    builder.add_node("recommend", make_recommend(deps))
    builder.add_node("approve", make_approve(deps))
    builder.add_node("act", make_act(deps))
    builder.add_node("verify", make_verify(deps))

    builder.add_edge(START, "collect")
    builder.add_edge("collect", "detect")
    builder.add_conditional_edges("detect", _route_after_detect,
                                 {"analyze": "analyze", END: END})
    builder.add_edge("analyze", "recommend")
    builder.add_edge("recommend", "approve")
    builder.add_edge("approve", "act")
    builder.add_edge("act", "verify")
    builder.add_conditional_edges("verify", _route_after_verify,
                                 {"verify": "verify", END: END})

    return builder.compile(checkpointer=checkpointer or MemorySaver())

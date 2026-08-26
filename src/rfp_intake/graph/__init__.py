"""LangGraph state graph definition — deterministic pipeline."""

from __future__ import annotations

from langgraph.graph import StateGraph

from rfp_intake.adjudicate import adjudicate_node
from rfp_intake.derive import derive_node
from rfp_intake.domain.schemas import RunState
from rfp_intake.extract import extract_node
from rfp_intake.gate import gate_node
from rfp_intake.graph.nodes.classify import classify_node
from rfp_intake.graph.nodes.ingest import ingest_node
from rfp_intake.normalize import normalize_node
from rfp_intake.plan import plan_node
from rfp_intake.reconcile import reconcile_node


def build_graph() -> StateGraph:  # type: ignore[type-arg]
    """Build the extraction pipeline graph.

    Topology: INGEST -> CLASSIFY -> PLAN -> EXTRACT -> NORMALIZE -> RECONCILE
              -> ADJUDICATE -> DERIVE -> GATE

    RENDER (§4.10) is not yet built; GATE is the last node today.

    PLAN generates ExtractionTasks; EXTRACT processes them all sequentially
    (fan-out via Send deferred to when RECONCILE needs it for real volume).
    The operator.add reducer on RunState.records handles record accumulation.
    """
    graph = StateGraph(RunState)

    graph.add_node("ingest", ingest_node)
    graph.add_node("classify", classify_node)
    graph.add_node("plan", plan_node)
    graph.add_node("extract", extract_node)
    graph.add_node("normalize", normalize_node)
    graph.add_node("reconcile", reconcile_node)
    graph.add_node("adjudicate", adjudicate_node)
    graph.add_node("derive", derive_node)
    graph.add_node("gate", gate_node)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "classify")
    graph.add_edge("classify", "plan")
    graph.add_edge("plan", "extract")
    graph.add_edge("extract", "normalize")
    graph.add_edge("normalize", "reconcile")
    graph.add_edge("reconcile", "adjudicate")
    graph.add_edge("adjudicate", "derive")
    graph.add_edge("derive", "gate")

    return graph

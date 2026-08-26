"""LangGraph state graph definition — deterministic pipeline."""

from __future__ import annotations

from langgraph.graph import StateGraph

from rfp_intake.domain.schemas import RunState
from rfp_intake.extract import extract_node
from rfp_intake.graph.nodes.classify import classify_node
from rfp_intake.graph.nodes.ingest import ingest_node
from rfp_intake.normalize import normalize_node
from rfp_intake.plan import plan_node


def build_graph() -> StateGraph:  # type: ignore[type-arg]
    """Build the Phase 2 extraction pipeline graph.

    Topology: INGEST → CLASSIFY → PLAN → EXTRACT → NORMALIZE

    PLAN generates ExtractionTasks; EXTRACT processes them all sequentially
    (fan-out via Send deferred to Phase 3 when RECONCILE needs it).
    The operator.add reducer on RunState.records handles record accumulation.
    """
    graph = StateGraph(RunState)

    graph.add_node("ingest", ingest_node)
    graph.add_node("classify", classify_node)
    graph.add_node("plan", plan_node)
    graph.add_node("extract", extract_node)
    graph.add_node("normalize", normalize_node)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "classify")
    graph.add_edge("classify", "plan")
    graph.add_edge("plan", "extract")
    graph.add_edge("extract", "normalize")

    return graph

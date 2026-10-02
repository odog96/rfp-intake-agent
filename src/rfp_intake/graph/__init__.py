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
from rfp_intake.other_study import mark_other_study_node
from rfp_intake.plan import plan_node
from rfp_intake.reconcile import reconcile_node
from rfp_intake.sections import find_sections_node
from rfp_intake.sections.set_aside import set_aside_sections_node


def build_graph() -> StateGraph:  # type: ignore[type-arg]
    """Build the extraction pipeline graph.

    Topology: INGEST -> CLASSIFY -> FIND_SECTIONS -> SET_ASIDE_SECTIONS
              -> MARK_OTHER_STUDY -> PLAN -> EXTRACT -> NORMALIZE -> RECONCILE
              -> ADJUDICATE -> DERIVE -> GATE

    FIND_SECTIONS, SET_ASIDE_SECTIONS and MARK_OTHER_STUDY are stages 1, 3 and 4
    of docs/PLAN_2026-10-02.md. MARK_OTHER_STUDY runs after SET_ASIDE_SECTIONS so
    it is never asked about a section an analyst skips anyway, and before PLAN so
    the text it removes is gone before anything is scored or extracted.

    RENDER (§4.10) is not yet built; GATE is the last node today.

    PLAN generates ExtractionTasks; EXTRACT processes them all sequentially
    (fan-out via Send deferred to when RECONCILE needs it for real volume).
    The operator.add reducer on RunState.records handles record accumulation.
    """
    graph = StateGraph(RunState)

    graph.add_node("ingest", ingest_node)
    graph.add_node("classify", classify_node)
    graph.add_node("find_sections", find_sections_node)
    graph.add_node("set_aside_sections", set_aside_sections_node)
    graph.add_node("mark_other_study", mark_other_study_node)
    graph.add_node("plan", plan_node)
    graph.add_node("extract", extract_node)
    graph.add_node("normalize", normalize_node)
    graph.add_node("reconcile", reconcile_node)
    graph.add_node("adjudicate", adjudicate_node)
    graph.add_node("derive", derive_node)
    graph.add_node("gate", gate_node)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "classify")
    graph.add_edge("classify", "find_sections")
    graph.add_edge("find_sections", "set_aside_sections")
    graph.add_edge("set_aside_sections", "mark_other_study")
    graph.add_edge("mark_other_study", "plan")
    graph.add_edge("plan", "extract")
    graph.add_edge("extract", "normalize")
    graph.add_edge("normalize", "reconcile")
    graph.add_edge("reconcile", "adjudicate")
    graph.add_edge("adjudicate", "derive")
    graph.add_edge("derive", "gate")

    return graph

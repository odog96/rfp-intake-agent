"""The graph's shape, asserted rather than assumed.

Nothing tested the topology before 2026-10-02. That is a gap worth closing with
this stage, because the stage's whole effect is to sit between two existing
nodes: SET_ASIDE_SECTIONS could be registered as a node, pass every test it
owns, and never run, and the only sign would be a bigger bill and a `_STEPS`
list in `app.py` that counts a step the engine never reports.

`CLAUDE.md` rule 1 — every edge is a static Python edge, no model decides what
runs next — is also only enforceable by a test like this one.
"""

from __future__ import annotations

from rfp_intake.graph import build_graph

# Graph order. Node names are lowercase here and upper-cased for status.json by
# job/__init__.py, which is why app.py's `_STEPS` reads "SET_ASIDE_SECTIONS".
EXPECTED_ORDER = [
    "ingest",
    "classify",
    "find_sections",
    "set_aside_sections",
    "mark_other_study",
    "plan",
    "extract",
    "normalize",
    "reconcile",
    "adjudicate",
    "derive",
    "gate",
]


def _edges(graph: object) -> set[tuple[str, str]]:
    return {(start, end) for start, end in graph.edges}  # type: ignore[attr-defined]


class TestTopology:
    def test_every_node_is_registered(self) -> None:
        graph = build_graph()
        assert set(graph.nodes) == set(EXPECTED_ORDER)

    def test_the_nodes_run_in_one_straight_line(self) -> None:
        graph = build_graph()
        edges = _edges(graph)
        for start, end in zip(EXPECTED_ORDER, EXPECTED_ORDER[1:], strict=False):
            assert (start, end) in edges, f"missing edge {start} -> {end}"

    def test_set_aside_sections_sits_between_find_sections_and_plan(self) -> None:
        """The one thing stage 3 changes about the pipeline's shape.

        Asserted on its own, and negatively, because the failure that matters is
        the old edge surviving alongside the new ones: the graph would still run
        and PLAN would still see every section.
        """
        edges = _edges(build_graph())
        assert ("find_sections", "set_aside_sections") in edges
        assert ("find_sections", "plan") not in edges

    def test_mark_other_study_sits_between_set_aside_sections_and_plan(self) -> None:
        """The one thing stage 4 changes about the pipeline's shape.

        Negatively as well as positively, for the same reason as stage 3 above: if
        `set_aside_sections -> plan` survived alongside the new pair, the graph
        would still run, MARK_OTHER_STUDY might never be reached, and the only
        sign would be `study.phase` still carrying another study's phase.

        The order matters in both directions. After SET_ASIDE_SECTIONS, so no
        model call is spent on a section an analyst skips anyway; before PLAN, so
        the removed text is gone before any section is scored or extracted.
        """
        edges = _edges(build_graph())
        assert ("set_aside_sections", "mark_other_study") in edges
        assert ("mark_other_study", "plan") in edges
        assert ("set_aside_sections", "plan") not in edges

    def test_the_entry_point_is_ingest(self) -> None:
        edges = _edges(build_graph())
        assert ("__start__", "ingest") in edges

    def test_the_step_list_the_app_shows_matches_the_graph(self) -> None:
        """app.py's progress display counts steps; a mismatch miscounts them.

        PLAN_2026-10-02.md section 4 asks for this explicitly: "otherwise the
        progress display counts nine steps for a twelve-step pipeline".
        """
        import app
        import app_v2

        expected = [name.upper() for name in EXPECTED_ORDER]
        assert [name for name, _ in app._STEPS] == expected  # noqa: SLF001
        assert [name for name, _ in app_v2._STEPS] == expected  # noqa: SLF001
